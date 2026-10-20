// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {Test, console} from "forge-std/Test.sol";
import {Vm, VmSafe} from "forge-std/Vm.sol";
import {TapLargeNetlistStoreOwned} from "../../src/TapLargeNetlistStore.sol";
import {TapLargeVerifier} from "../../src/TapLargeVerifier.sol";
import {TapLargeHash} from "../../src/TapLargeHash.sol";
import {TapLargeDisputeGame} from "../../src/game/TapLargeDisputeGame.sol";
import {Upload} from "../utils/Upload.sol";
import {Cases} from "../utils/Cases.sol";

/// End-to-end disputes of the informative game (src/game/TapLargeDisputeGame.sol) on six files: the examples of
/// assets/tap-draft-large-netlists/ `message` (sequential ROM, TINY budget, K = 8, inputs of case (a): the game
/// computes inputRoot) and `large` (default budget, K = 2, case (b): a separate input provider publishes them);
/// the files with RAM ports (MEM) `ram16` (TINY, K = 4, case (a)) and `rammix` (default, K = 4, case (b)); and the
/// TAPBOY-8 cartridge of demo/cartridge, `dodge` (RAM ports, default budget, T = 651, K = 32, case (b)), and its
/// LATCH-RAM build `legacy` (no MEM, same parameters).
///
/// The moves come from test/data/game_gen.py, which plays the honest and cheating parties of assets/tap-draft-large-netlists/bisect_sim.py
/// against a Python mirror of the referee and records every transaction plus the predicted outcome. This test
/// replays each transaction on chain from the party's own address and asserts the on-chain outcome of every
/// dispute of the claim (winner, reason, the index the game asks for at every reveal, the number of verifier
/// calls: exactly one when the dispute reaches the final call of level 3, zero otherwise), the final status of
/// the claim (accepted after the window, or rejected), and every bond transfer.
///
/// Multi-challenger scenarios (§7.1): a sock puppet of the claimant that loses at once or stalls does not stop an
/// honest challenger, two honest challengers in parallel (the first to win takes the claimant's bond, the other
/// is refunded), and an honest claimant against two cheating challengers in parallel (accepted, both bonds won).
///
/// Gas: in Foundry 1.8.3 every top-level call from a test runs as its own transaction (state committed, slots
/// and accounts cold), and `vm.lastFrameGas().gasTotalUsed` is that transaction's gas including the 21,000
/// intrinsic and the calldata gas, before refunds. That is the per-move figure; the table sums it per party.
contract E2ETest is Test {
    uint256 internal constant BOND = 1 ether;
    uint64 internal constant CHALLENGE_WINDOW = 1 days;
    uint64 internal constant MOVE_TIME = 1 hours;
    uint64 internal constant PER_BEAT_TIME = 60;
    uint64 internal constant MAX_K = 64;
    uint256 internal constant TX_GAS_CAP = 16_777_216;
    uint256 internal constant N_ACTORS = 5; // claimant, challenger, bystander, input provider, second challenger
    uint8 internal constant NONE = 255;

    uint8 internal constant M_CLAIM = 1;
    uint8 internal constant M_CHALLENGE = 2;
    uint8 internal constant M_COMMIT_BEATS = 3;
    uint8 internal constant M_REVEAL_BEAT = 4;
    uint8 internal constant M_COMMIT_TUPLES = 5;
    uint8 internal constant M_REVEAL_SEG = 6;
    uint8 internal constant M_REVEAL_END = 7;
    uint8 internal constant M_PROVE_SEG = 8;
    uint8 internal constant M_PROVE_END = 9;
    uint8 internal constant M_TIMEOUT = 10;
    uint8 internal constant M_PUBLISH = 11;
    uint8 internal constant M_ACCEPT = 12;
    uint8 internal constant M_REFUND = 13;

    uint8 internal constant CL_WON = 0;
    uint8 internal constant CH_WON = 1;
    uint8 internal constant VOIDED = 2;

    struct Scenario {
        string name;
        uint8 game;
        uint8 status; // TapLargeDisputeGame.Status at the end
        uint256 firstExp; // index into `exps`
        uint256 nExp;
        uint256 nMoves;
        uint256 at; // reader offset of the first move
    }

    /// Expected outcome of one dispute of a scenario.
    struct Exp {
        uint8 actor; // the challenger actor (keys the dispute)
        uint8 outcome; // CL_WON, CH_WON, VOIDED
        uint8 reason;
        uint8 calls;
        uint8 kind;
    }

    struct Stats {
        uint256[5] gas; // per actor
        uint256[5] txs;
        uint256 maxGas;
        uint8 maxType;
        uint256 verifierCalls;
    }

    TapLargeNetlistStoreOwned internal store;
    TapLargeVerifier[] internal verifiers;
    TapLargeDisputeGame[] internal games;
    bytes32[] internal nh2s;
    address[5] internal actors;
    bytes internal data;
    Scenario[] internal scenarios;
    Exp[] internal exps;
    // across all replayed moves: per move type, the smallest and largest transaction gas
    uint256[14] internal minGasOf;
    uint256[14] internal maxGasOf;

    function setUp() public {
        data = vm.readFileBinary("test/data/game_moves.bin");
        Cases.Reader memory r = Cases.Reader(data, 0);
        require(Cases.uintN(r, 4) == 0x54444733, "magic"); // "TDG3"
        uint256 ng = Cases.uintN(r, 1);
        require(ng == 6, "six games");
        store = new TapLargeNetlistStoreOwned(address(this));
        actors = [
            makeAddr("claimant"),
            makeAddr("challenger"),
            makeAddr("bystander"),
            makeAddr("inputProvider"),
            makeAddr("challenger2")
        ];
        for (uint256 k; k < N_ACTORS; ++k) {
            vm.deal(actors[k], 1000 ether);
        }
        for (uint256 k; k < ng; ++k) {
            bytes memory file = Cases.bytesN(r, Cases.uintN(r, 4));
            nh2s.push(Upload.upload(store, file, true, 0));
            uint32 g = uint32(Cases.uintN(r, 4));
            uint32 e = uint32(Cases.uintN(r, 4));
            uint32 c = uint32(Cases.uintN(r, 4));
            bool caseB = Cases.uintN(r, 1) == 1;
            verifiers.push(new TapLargeVerifier(store, g, e, c));
            games.push(new TapLargeDisputeGame(
                TapLargeDisputeGame.Params({
                    store: store,
                    verifier: verifiers[k],
                    bond: BOND,
                    challengeWindow: CHALLENGE_WINDOW,
                    moveTime: MOVE_TIME,
                    perBeatTime: PER_BEAT_TIME,
                    maxK: MAX_K,
                    maxT: MAX_K * 2047,
                    inputProvider: caseB ? actors[3] : address(0)
                })
            ));
        }

        uint256 ns = Cases.uintN(r, 4);
        for (uint256 s; s < ns; ++s) {
            Scenario memory sc;
            sc.name = string(Cases.bytesN(r, Cases.uintN(r, 2)));
            sc.game = uint8(Cases.uintN(r, 1));
            sc.status = uint8(Cases.uintN(r, 1));
            sc.nExp = Cases.uintN(r, 1);
            sc.firstExp = exps.length;
            for (uint256 k; k < sc.nExp; ++k) {
                exps.push(
                    Exp(
                        uint8(Cases.uintN(r, 1)),
                        uint8(Cases.uintN(r, 1)),
                        uint8(Cases.uintN(r, 1)),
                        uint8(Cases.uintN(r, 1)),
                        uint8(Cases.uintN(r, 1))
                    )
                );
            }
            sc.nMoves = Cases.uintN(r, 2);
            sc.at = r.p;
            for (uint256 m; m < sc.nMoves; ++m) {
                Cases.uintN(r, 3);
                r.p += Cases.uintN(r, 4);
            }
            scenarios.push(sc);
        }
        require(r.p == data.length, "trailing bytes");
    }

    // ================================================================== named scenarios (with vm.expectCall)

    function test_honestClaimant_vs_cheatingChallenger_message() public {
        _named("message/c_rom_as_challenger");
    }

    function test_honestClaimant_vs_cheatingChallenger_large() public {
        _named("large/b_state_as_challenger");
    }

    function test_cheatingClaimant_a_nonCanonicalShort_message() public {
        _named("message/a_short_as_claimant");
    }

    function test_cheatingClaimant_a_nonCanonicalLong_large() public {
        _named("large/a_long_as_claimant");
    }

    function test_cheatingClaimant_b_stateRootLie_message() public {
        _named("message/b_state_as_claimant");
    }

    function test_cheatingClaimant_b_stateRootLie_large() public {
        _named("large/b_state_as_claimant");
    }

    function test_cheatingClaimant_c_romReadLie_message() public {
        _named("message/c_rom_as_claimant");
    }

    function test_cheatingClaimant_c_romReadLie_large() public {
        _named("large/c_rom_big_as_claimant");
    }

    function test_cheatingClaimant_d_checkpointLie_message() public {
        _named("message/d_checkpoint_as_claimant");
    }

    function test_cheatingClaimant_d_checkpointLie_large() public {
        _named("large/d_checkpoint_as_claimant");
    }

    function test_cheatingClaimant_inputLie_message() public {
        _named("message/inputs_wrong_as_claimant");
    }

    function test_cheatingClaimant_inputWithheld_large() public {
        _named("large/inputs_withhold_as_claimant");
    }

    function test_cheatingClaimant_outputLie_large() public {
        _named("large/o_output_as_claimant");
    }

    function test_extraStepPastEof_message() public {
        _named("message/e_eof_extra_as_challenger");
    }

    function test_timeout_claimantMissesCommitBeats() public {
        _named("message/timeout_claimant_commitBeats");
    }

    function test_timeout_challengerMissesCommitTuples() public {
        _named("large/timeout_challenger_commitTuples");
    }

    function test_timeout_claimantMissesProve() public {
        _named("message/timeout_claimant_prove");
    }

    function test_timeout_challengerMissesRevealSeg() public {
        _named("large/timeout_challenger_revealSeg");
    }

    function test_timeout_unchallengedClaimAccepted() public {
        _named("message/honest_unchallenged");
    }

    function test_wrongIndex_equalLeaves() public {
        _named("message/index_1_vs_honest");
    }

    function test_wrongIndex_outOfRange() public {
        _named("message/index_6_out_of_range");
    }

    function test_wrongIndex_cannotOpenForgedLeaf() public {
        _named("message/index_4_vs_forger_t24");
    }

    function test_oneBeatInterval_K1() public {
        _named("message/k1_c_rom_as_claimant");
    }

    // ------------------------------------------------------------------ files with RAM ports (MEM)

    function test_ram_readLie_claimant() public {
        _named("ram16/a_ram_read_as_claimant");
    }

    function test_ram_readLie_challenger() public {
        _named("rammix/a_ram_read_as_challenger");
    }

    function test_ram_writeLie_claimant() public {
        _named("rammix/b_ram_write_as_claimant");
    }

    function test_ram_writeLie_challenger() public {
        _named("ram16/b_ram_write_as_challenger");
    }

    function test_ram_writeDataGateLie_claimant() public {
        _named("ram16/c_write_data_as_claimant");
    }

    function test_ram_writeDataGateLie_challenger() public {
        _named("rammix/c_write_data_as_challenger");
    }

    function test_ram_beat0WriteLie() public {
        _named("ram16/e_beat0_write_as_claimant");
    }

    /// A claimant that cannot open the agreed stateRoot_t (§7.4) loses at its commitTuples move, without a call.
    function test_ram_claimantWithoutOpeningLoses() public {
        _named("ram16/i_no_opening_as_claimant");
        _named("rammix/i_no_opening_as_claimant");
    }

    // ------------------------------------------------------------------ the TAPBOY-8 cartridge (RAM ports)

    function test_cartridge_inflatedScoreByRamWrite() public {
        _named("dodge/B_ram_write_as_claimant");
    }

    function test_cartridge_inflatedScoreByGate() public {
        _named("dodge/C_alu_gate_as_claimant");
    }

    function test_cartridge_cheatingChallengerRamWrite() public {
        _named("dodge/D_ram_write_as_challenger");
    }

    function test_cartridge_inflatedScoreByRamRead() public {
        _named("dodge/E_ram_read_as_claimant");
    }

    function test_cartridge_cheatingChallengerRamRead() public {
        _named("dodge/F_ram_read_as_challenger");
    }

    // ------------------------------------------------------------------ M forgery on files without MEM (§4.5, §7.4)

    /// A cheater (bisect_sim's MForger) lies about one output of beat t and puts garbage in the M field of its last
    /// segment tuple. For a file without MEM, M is not committed (not hashed into segLeaf), so every segment leaf
    /// of the cheater is honest. The game compares the last tuples by their committed fields and picks the output
    /// list, where the cheater loses by verify_beat_end, in either role and on all three files without MEM. The
    /// mirror (game_gen.py) predicts the same outcome.
    function test_mForgery_claimantLoses_message() public {
        _named("message/j_m_forgery_as_claimant");
    }

    function test_mForgery_claimantLoses_large() public {
        _named("large/j_m_forgery_as_claimant");
    }

    function test_mForgery_claimantLoses_legacyCartridge() public {
        _named("legacy/m_forgery_as_claimant");
    }

    function test_mForgery_challengerLoses() public {
        _named("message/j_m_forgery_as_challenger");
        _named("large/j_m_forgery_as_challenger");
        _named("legacy/m_forgery_as_challenger");
    }

    /// The review's reproduction, step by step: before the fix, the forged M made the game pick the segment list
    /// although all its leaves agree; at h = n - 1 both leaves were equal with equal lengths, the game asked the
    /// honest challenger for tuple h + 1, which does not exist, and the honest challenger lost by timeout. Now the
    /// output list is picked (kind 1), the game never reaches the equal-at-h branch, and the honest challenger wins.
    function test_mForgery_honestChallengerWins_stepByStep() public {
        Scenario memory s = _find("message/j_m_forgery_as_claimant");
        TapLargeDisputeGame g = games[s.game];
        Cases.Reader memory r = Cases.Reader(data, s.at);
        Stats memory st;
        uint256 id;
        uint256 m;
        uint256 commits;
        address ch = actors[1];
        for (; m < s.nMoves; ++m) {
            uint8 mtype = uint8(data[r.p + 1]);
            id = _move(g, id, r, st);
            if (mtype == M_COMMIT_TUPLES && ++commits == 2) break; // the challenger's commit: the list is picked
        }
        (,,,,,, uint8 kind) = g.stateOf(id, ch);
        assertEq(kind, 1, "the output list is picked (the segment leaves all agree)");
        for (++m; m < s.nMoves; ++m) {
            uint8 mtype = uint8(data[r.p + 1]);
            assertTrue(mtype != M_REVEAL_SEG && mtype != M_PROVE_SEG, "no segment move");
            id = _move(g, id, r, st);
            (TapLargeDisputeGame.Phase phase,,,,,,) = g.stateOf(id, ch);
            assertTrue(phase != TapLargeDisputeGame.Phase.TupleExtra, "never the equal-at-h branch");
        }
        (address w, TapLargeDisputeGame.Role role, TapLargeDisputeGame.Reason reason) = g.outcomeOf(id, ch);
        assertEq(uint8(role), uint8(TapLargeDisputeGame.Role.Challenger), "the honest challenger wins");
        assertEq(uint8(reason), uint8(TapLargeDisputeGame.Reason.VerifierFalse));
        assertEq(w, ch);
        assertEq(uint8(g.claimOf(id).status), uint8(TapLargeDisputeGame.Status.Rejected));
    }

    // ------------------------------------------------------------------ several challengers (§7.1)

    /// The claimant's sock puppet challenges with index 0 and loses in the claim's block; the claim stays open and
    /// the honest challenger, who computed the run, challenges and wins: the claim is rejected.
    function test_multi_sockPuppetLosesAtOnce_honestStillWins() public {
        _named("multi/sock_index0_then_honest");
    }

    /// The sock puppet challenges a real index and stalls; it loses by timeout, and the honest challenger still
    /// challenges within the window and wins.
    function test_multi_sockPuppetStalls_honestStillWins() public {
        _named("multi/sock_stall_then_honest");
    }

    /// Two honest challengers in parallel: the first to win takes the claimant's bond; the other dispute is
    /// voided by the rejection and its bond refunded.
    function test_multi_twoHonestChallengers_message() public {
        _named("multi/two_honest_message");
    }

    function test_multi_twoHonestChallengers_large() public {
        _named("multi/two_honest_large");
    }

    /// An honest claimant against two cheating challengers in parallel: it wins both, takes both bonds, and the
    /// claim is accepted after the window.
    function test_multi_honestClaimant_twoCheaters_message() public {
        _named("multi/two_cheaters_message");
    }

    function test_multi_honestClaimant_twoCheaters_large() public {
        _named("multi/two_cheaters_large");
    }

    /// Cheaters that also break a rule of the game lose at a reveal check of levels 2 and 3 (§7.3, §7.4 checks
    /// 1–5, Merkle proofs), with no verifier call.
    function test_ruleBreakersLoseWithoutVerifierCall() public {
        string[8] memory names = [
            "message/rule_endpoints_by_claimant",
            "message/rule_beat_proof_by_challenger",
            "message/rule_tuple_proof_by_claimant",
            "message/rule_seg0_by_challenger",
            "message/rule_last_proof_by_claimant",
            "message/rule_seg_eof_by_claimant",
            "message/rule_out_root_by_challenger",
            "message/rule_state_root_by_claimant"
        ];
        address v = address(verifiers[0]);
        vm.expectCall(v, abi.encodePacked(TapLargeVerifier.verifySegment.selector), 0);
        vm.expectCall(v, abi.encodePacked(TapLargeVerifier.verifyBeatEnd.selector), 0);
        for (uint256 k; k < names.length; ++k) {
            Scenario memory s = _find(names[k]);
            assertEq(exps[s.firstExp].calls, 0);
            _run(s);
        }
    }

    /// Runs one scenario with vm.expectCall on the verifier: exactly one call of the expected function when the
    /// dispute reaches the final call of level 3, and no call of either function otherwise.
    function _named(string memory name) internal {
        Scenario memory s = _find(name);
        address v = address(verifiers[s.game]);
        uint64 nSeg;
        uint64 nEnd;
        for (uint256 k; k < s.nExp; ++k) {
            Exp memory e = exps[s.firstExp + k];
            if (e.calls == 1 && e.kind == 0) ++nSeg;
            if (e.calls == 1 && e.kind != 0) ++nEnd;
        }
        vm.expectCall(v, abi.encodePacked(TapLargeVerifier.verifySegment.selector), nSeg);
        vm.expectCall(v, abi.encodePacked(TapLargeVerifier.verifyBeatEnd.selector), nEnd);
        _run(s);
    }

    function _find(string memory name) internal view returns (Scenario memory) {
        for (uint256 k; k < scenarios.length; ++k) {
            if (keccak256(bytes(scenarios[k].name)) == keccak256(bytes(name))) return scenarios[k];
        }
        revert(string.concat("no scenario ", name));
    }

    // ================================================================== all scenarios, with the gas table

    function test_allScenarios_gasTable() public {
        string memory csv = "scenario,claimStatus,disputes,verifierCalls,txsClaimant,txsChallengers,txsOthers,"
            "gasClaimant,gasChallengers,gasOthers,gasTotal,maxMoveGas,maxMove\n";
        uint256 worst;
        for (uint256 k; k < scenarios.length; ++k) {
            Scenario memory s = scenarios[k];
            Stats memory st = _run(s);
            if (st.maxGas > worst) worst = st.maxGas;
            csv = string.concat(csv, _row(s, st));
            console.log(
                string.concat(
                    _pad(s.name, 42),
                    " txs ",
                    vm.toString(st.txs[0]),
                    "/",
                    vm.toString(st.txs[1] + st.txs[4]),
                    "/",
                    vm.toString(st.txs[2] + st.txs[3]),
                    " gas cl ",
                    vm.toString(st.gas[0]),
                    " ch ",
                    vm.toString(st.gas[1] + st.gas[4]),
                    " max ",
                    vm.toString(st.maxGas),
                    " (",
                    _moveName(st.maxType),
                    ")"
                )
            );
        }
        vm.writeFile("test/data/game_gas.csv", csv);
        console.log("largest single move (tx gas incl. intrinsic and calldata):", worst);
        for (uint8 t = 1; t < 14; ++t) {
            console.log(string.concat("  ", _pad(_moveName(t), 14), " min ", vm.toString(minGasOf[t]), " max ", vm.toString(maxGasOf[t])));
        }
        assertLe(worst, TX_GAS_CAP);
    }

    function _row(Scenario memory s, Stats memory st) internal pure returns (string memory) {
        string memory a = string.concat(
            s.name,
            ",",
            vm.toString(uint256(s.status)),
            ",",
            vm.toString(s.nExp),
            ",",
            vm.toString(st.verifierCalls),
            ",",
            vm.toString(st.txs[0]),
            ",",
            vm.toString(st.txs[1] + st.txs[4]),
            ",",
            vm.toString(st.txs[2] + st.txs[3])
        );
        return string.concat(
            a,
            ",",
            vm.toString(st.gas[0]),
            ",",
            vm.toString(st.gas[1] + st.gas[4]),
            ",",
            vm.toString(st.gas[2] + st.gas[3]),
            ",",
            vm.toString(st.gas[0] + st.gas[1] + st.gas[2] + st.gas[3] + st.gas[4]),
            ",",
            vm.toString(st.maxGas),
            ",",
            _moveName(st.maxType),
            "\n"
        );
    }

    // ================================================================== replay

    function _run(Scenario memory s) internal returns (Stats memory st) {
        TapLargeDisputeGame g = games[s.game];
        Cases.Reader memory r = Cases.Reader(data, s.at);
        uint256[5] memory creditBefore;
        for (uint256 k; k < N_ACTORS; ++k) {
            creditBefore[k] = g.credit(actors[k]);
        }
        uint256 id;
        vm.startStateDiffRecording();
        for (uint256 m; m < s.nMoves; ++m) {
            id = _move(g, id, r, st);
        }
        Vm.AccountAccess[] memory acc = vm.stopAndReturnStateDiff();
        for (uint256 k; k < acc.length; ++k) {
            if (
                acc[k].account == address(verifiers[s.game])
                    && (acc[k].kind == VmSafe.AccountAccessKind.Call
                        || acc[k].kind == VmSafe.AccountAccessKind.StaticCall) && _isVerifyCall(acc[k].data)
            ) ++st.verifierCalls;
        }
        _checkOutcome(s, g, id, st, creditBefore);
    }

    /// verifySegment or verifyBeatEnd (the claim also reads segmentBudget from the verifier; that is no call
    /// of a verifier in the sense of §7.4).
    function _isVerifyCall(bytes memory cd) internal pure returns (bool) {
        if (cd.length < 4) return false;
        bytes4 sel = bytes4(cd[0]) | (bytes4(cd[1]) >> 8) | (bytes4(cd[2]) >> 16) | (bytes4(cd[3]) >> 24);
        return sel == TapLargeVerifier.verifySegment.selector || sel == TapLargeVerifier.verifyBeatEnd.selector;
    }

    /// Replays one recorded transaction from its party's address; returns the claim id.
    function _move(TapLargeDisputeGame g, uint256 id, Cases.Reader memory r, Stats memory st)
        internal
        returns (uint256)
    {
        uint8 actor = uint8(Cases.uintN(r, 1));
        uint8 mtype = uint8(Cases.uintN(r, 1));
        uint8 target = uint8(Cases.uintN(r, 1));
        uint256 bodyEnd = Cases.uintN(r, 4);
        bodyEnd += r.p;
        address ch = target == NONE ? address(0) : actors[target];
        (bytes memory cd, uint256 value) = _encode(g, id, ch, mtype, r);
        require(r.p == bodyEnd, "move body length");
        vm.prank(actors[actor]);
        (bool ok, bytes memory ret) = address(g).call{value: value}(cd);
        if (!ok) {
            console.log("move reverted:", _moveName(mtype));
            assembly {
                revert(add(ret, 0x20), mload(ret))
            }
        }
        uint256 used = vm.lastFrameGas().gasTotalUsed;
        if (minGasOf[mtype] == 0 || used < minGasOf[mtype]) minGasOf[mtype] = used;
        if (used > maxGasOf[mtype]) maxGasOf[mtype] = used;
        st.gas[actor] += used;
        st.txs[actor] += 1;
        if (used > st.maxGas) {
            st.maxGas = used;
            st.maxType = mtype;
        }
        assertLe(used, TX_GAS_CAP, "single move above 2^24 gas");
        return mtype == M_CLAIM ? abi.decode(ret, (uint256)) : id;
    }

    /// Every dispute's outcome, the claim's status, the verifier calls, and the bonds: the claimant receives one
    /// bond per dispute it won and its own bond back on acceptance; a challenger that won receives two bonds; a
    /// voided dispute's challenger receives its own bond back; nobody else receives anything (no fee).
    function _checkOutcome(
        Scenario memory s,
        TapLargeDisputeGame g,
        uint256 id,
        Stats memory st,
        uint256[5] memory creditBefore
    ) internal view {
        TapLargeDisputeGame.Claim memory c = g.claimOf(id);
        assertEq(uint8(c.status), s.status, string.concat(s.name, ": claim status"));
        assertEq(c.openDisputes, 0, string.concat(s.name, ": open disputes"));
        uint256[5] memory gain;
        uint256 calls;
        for (uint256 k; k < s.nExp; ++k) {
            Exp memory e = exps[s.firstExp + k];
            calls += e.calls;
            _checkDispute(s.name, g, id, e, gain);
        }
        if (s.status == uint8(TapLargeDisputeGame.Status.Accepted)) gain[0] += BOND;
        for (uint256 k; k < N_ACTORS; ++k) {
            assertEq(g.credit(actors[k]) - creditBefore[k], gain[k], string.concat(s.name, ": credit"));
        }
        assertEq(st.verifierCalls, calls, string.concat(s.name, ": verifier calls"));
    }

    function _checkDispute(string memory name, TapLargeDisputeGame g, uint256 id, Exp memory e, uint256[5] memory gain)
        internal
        view
    {
        address ch = actors[e.actor];
        (TapLargeDisputeGame.Phase phase,,,,,, uint8 kind) = g.stateOf(id, ch);
        if (e.outcome == VOIDED) {
            assertEq(uint8(phase), uint8(TapLargeDisputeGame.Phase.Voided), name);
            gain[e.actor] += BOND;
            return;
        }
        assertEq(uint8(phase), uint8(TapLargeDisputeGame.Phase.Resolved), name);
        (address w, TapLargeDisputeGame.Role role, TapLargeDisputeGame.Reason reason) = g.outcomeOf(id, ch);
        assertEq(uint8(role), e.outcome, string.concat(name, ": winner"));
        assertEq(uint8(reason), e.reason, string.concat(name, ": reason"));
        assertEq(w, e.outcome == CL_WON ? actors[0] : ch, name);
        if (e.calls == 1) assertEq(kind, e.kind, string.concat(name, ": picked list"));
        if (e.outcome == CL_WON) gain[0] += BOND;
        else gain[e.actor] += 2 * BOND;
    }

    // ================================================================== move decoding

    function _encode(TapLargeDisputeGame g, uint256 id, address ch, uint8 mtype, Cases.Reader memory r)
        internal
        returns (bytes memory cd, uint256 value)
    {
        if (mtype == M_PUBLISH) {
            uint64 T = uint64(Cases.uintN(r, 8));
            bytes32 root = Cases.b32(r);
            bytes memory inputData = Cases.bytesN(r, Cases.uintN(r, 4));
            return (abi.encodeCall(TapLargeDisputeGame.publishInputs, (_nh2Of(g), T, root, inputData)), 0);
        }
        if (mtype == M_CLAIM) return (_claim(g, r), BOND);
        if (mtype == M_ACCEPT) {
            uint64 end = g.claimOf(id).windowEnd;
            if (block.timestamp <= end) vm.warp(end + 1);
            return (abi.encodeCall(TapLargeDisputeGame.accept, (id)), 0);
        }
        if (mtype == M_REFUND) return (abi.encodeCall(TapLargeDisputeGame.refund, (id, ch)), 0);
        if (mtype == M_CHALLENGE) return (_challenge(id, r), BOND);
        if (mtype == M_COMMIT_BEATS) {
            bytes32 root = Cases.b32(r);
            bytes32[] memory p0 = _proof(r);
            return (abi.encodeCall(TapLargeDisputeGame.commitBeats, (id, ch, root, p0, _proof(r))), 0);
        }
        if (mtype == M_REVEAL_BEAT) {
            uint64 expected = uint64(Cases.uintN(r, 8));
            (,,, uint64 lo, uint64 hi,,) = g.stateOf(id, ch);
            assertEq((lo + hi) / 2, expected, "game asks for another beat than the mirror");
            bytes32 sr = Cases.b32(r);
            bytes32 io = Cases.b32(r);
            return (abi.encodeCall(TapLargeDisputeGame.revealBeat, (id, ch, sr, io, _proof(r))), 0);
        }
        if (mtype == M_COMMIT_TUPLES) return (_commitTuples(id, ch, r), 0);
        if (mtype == M_REVEAL_SEG || mtype == M_REVEAL_END) {
            uint64 expected = uint64(Cases.uintN(r, 8));
            (,,,,, uint64 ask,) = g.stateOf(id, ch);
            assertEq(ask, expected, "game asks for another tuple than the mirror");
            if (mtype == M_REVEAL_SEG) {
                TapLargeVerifier.SegTuple memory t = Cases.tuple(r);
                return (abi.encodeCall(TapLargeDisputeGame.revealSeg, (id, ch, t, _proof(r))), 0);
            }
            TapLargeVerifier.EndTuple memory e = _end(r);
            return (abi.encodeCall(TapLargeDisputeGame.revealEnd, (id, ch, e, _proof(r))), 0);
        }
        if (mtype == M_PROVE_SEG) {
            TapLargeVerifier.SegTuple memory a = Cases.tuple(r);
            TapLargeVerifier.SegTuple memory b = Cases.tuple(r);
            bytes memory w = Cases.bytesN(r, Cases.uintN(r, 4));
            return (abi.encodeCall(TapLargeDisputeGame.proveSegment, (id, ch, a, b, w)), 0);
        }
        if (mtype == M_PROVE_END) {
            TapLargeVerifier.SegTuple memory segM = Cases.tuple(r);
            TapLargeVerifier.EndTuple memory a = _end(r);
            TapLargeVerifier.EndTuple memory b = _end(r);
            bytes memory w = Cases.bytesN(r, Cases.uintN(r, 4));
            return (abi.encodeCall(TapLargeDisputeGame.proveBeatEnd, (id, ch, segM, a, b, w)), 0);
        }
        require(mtype == M_TIMEOUT, "move type");
        (,, uint64 deadline,,,,) = g.stateOf(id, ch);
        vm.warp(uint256(deadline) + 1);
        return (abi.encodeCall(TapLargeDisputeGame.timeout, (id, ch)), 0);
    }

    function _claim(TapLargeDisputeGame g, Cases.Reader memory r) internal view returns (bytes memory) {
        uint64 T = uint64(Cases.uintN(r, 8));
        uint64 K = uint64(Cases.uintN(r, 8));
        bytes32 inputRoot = Cases.b32(r);
        uint256 n = Cases.uintN(r, 2);
        bytes32[] memory leaves = new bytes32[](n);
        for (uint256 k; k < n; ++k) {
            leaves[k] = Cases.b32(r);
        }
        bytes memory inputData = Cases.bytesN(r, Cases.uintN(r, 4));
        return abi.encodeCall(TapLargeDisputeGame.claim, (_nh2Of(g), T, K, inputRoot, leaves, inputData));
    }

    function _nh2Of(TapLargeDisputeGame g) internal view returns (bytes32) {
        for (uint256 k; k < games.length; ++k) {
            if (games[k] == g) return nh2s[k];
        }
        revert("game");
    }

    function _challenge(uint256 id, Cases.Reader memory r) internal pure returns (bytes memory) {
        TapLargeDisputeGame.ChallengeArgs memory x;
        x.index = uint32(Cases.uintN(r, 4));
        x.prevStateRoot = Cases.b32(r);
        x.prevIo = Cases.b32(r);
        x.proofPrev = _proof(r);
        x.leafCur = Cases.b32(r);
        x.proofCur = _proof(r);
        x.stateRoot = Cases.b32(r);
        x.io = Cases.b32(r);
        return abi.encodeCall(TapLargeDisputeGame.challenge, (id, x));
    }

    function _commitTuples(uint256 id, address ch, Cases.Reader memory r) internal pure returns (bytes memory) {
        bytes memory packed = Cases.bytesN(r, Cases.uintN(r, 4));
        bytes32[] memory inProof = _proof(r);
        TapLargeDisputeGame.TupleLists memory x;
        x.stateRootT = Cases.b32(r);
        x.ioT = Cases.b32(r);
        x.stateRootT1 = Cases.b32(r);
        x.ioT1 = Cases.b32(r);
        x.latchRootT = Cases.b32(r);
        x.memRootT = Cases.b32(r);
        for (uint256 k; k < 3; ++k) {
            x.roots[k] = Cases.b32(r);
        }
        for (uint256 k; k < 3; ++k) {
            x.lengths[k] = uint32(Cases.uintN(r, 4));
        }
        x.segLast = Cases.tuple(r);
        x.outLast = _end(r);
        x.stateLast = _end(r);
        x.proofSeg0 = _proof(r);
        x.proofSegLast = _proof(r);
        x.proofOut0 = _proof(r);
        x.proofOutLast = _proof(r);
        x.proofState0 = _proof(r);
        x.proofStateLast = _proof(r);
        return abi.encodeCall(TapLargeDisputeGame.commitTuples, (id, ch, packed, inProof, x));
    }

    function _proof(Cases.Reader memory r) internal pure returns (bytes32[] memory p) {
        p = new bytes32[](Cases.uintN(r, 2));
        for (uint256 k; k < p.length; ++k) {
            p[k] = Cases.b32(r);
        }
    }

    function _end(Cases.Reader memory r) internal pure returns (TapLargeVerifier.EndTuple memory e) {
        e.ordinal = uint32(Cases.uintN(r, 4));
        e.root = Cases.b32(r);
    }

    function _moveName(uint8 t) internal pure returns (string memory) {
        string[14] memory names = [
            "",
            "claim",
            "challenge",
            "commitBeats",
            "revealBeat",
            "commitTuples",
            "revealSeg",
            "revealEnd",
            "proveSegment",
            "proveBeatEnd",
            "timeout",
            "publishInputs",
            "accept",
            "refund"
        ];
        return names[t];
    }

    function _pad(string memory s, uint256 n) internal pure returns (string memory) {
        bytes memory b = bytes(s);
        if (b.length >= n) return s;
        bytes memory out = new bytes(n);
        for (uint256 k; k < n; ++k) {
            out[k] = k < b.length ? b[k] : bytes1(" ");
        }
        return string(out);
    }
}
