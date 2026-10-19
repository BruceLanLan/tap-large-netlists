// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {TapLargeNetlistStoreOwned} from "../../src/TapLargeNetlistStore.sol";
import {TapLargeVerifier} from "../../src/TapLargeVerifier.sol";
import {TapLargeHash} from "../../src/TapLargeHash.sol";
import {TapLargeDisputeGame} from "../../src/game/TapLargeDisputeGame.sol";
import {Upload} from "../utils/Upload.sol";
import {Cases} from "../utils/Cases.sol";

/// §7.1, several challengers. The second review's reproductions (a sock puppet of the claimant that loses its
/// challenge at once, or challenges and then stalls) finalised a bogus claim in the old single-slot game. Here
/// they must fail as attacks: a dispute won by the claimant leaves the claim open, the window stays open for
/// other challengers, an honest challenger can still challenge and win, and only `accept` after the window, with
/// no dispute open and none won by a challenger, accepts a claim. Full disputes with real values (sock puppet +
/// honest challenger, two honest challengers, two cheating challengers) are replayed in E2E.t.sol.
contract MultiChallengerTest is Test {
    uint256 internal constant BOND = 1 ether;
    uint64 internal constant WINDOW = 1 days;
    uint64 internal constant MOVE = 1 hours;
    uint64 internal constant PER_BEAT = 60;

    TapLargeNetlistStoreOwned internal store;
    TapLargeDisputeGame internal g;
    bytes32 internal nh2;
    bytes internal inputData; // T = 40 beats, nIn = 1
    bytes32 internal inputRoot;
    address internal claimant = makeAddr("claimant");
    address internal sybil = makeAddr("sybil"); // a second address of the claimant
    address internal honest = makeAddr("honestChallenger");
    address internal honest2 = makeAddr("honestChallenger2");

    function setUp() public {
        store = new TapLargeNetlistStoreOwned(address(this));
        Cases.Reader memory r = Cases.Reader(vm.readFileBinary("test/data/game_moves.bin"), 5);
        bytes memory file = Cases.bytesN(r, Cases.uintN(r, 4)); // the message example
        nh2 = Upload.upload(store, file, true, 0);
        g = new TapLargeDisputeGame(
            TapLargeDisputeGame.Params({
                store: store,
                verifier: new TapLargeVerifier(store, 16, 4, 2),
                bond: BOND,
                challengeWindow: WINDOW,
                moveTime: MOVE,
                perBeatTime: PER_BEAT,
                maxK: 64,
                maxT: 64 * 2047,
                inputProvider: address(0) // case (a)
            })
        );
        inputData = new bytes(40);
        bytes32[] memory lv = new bytes32[](40);
        for (uint256 t; t < 40; ++t) {
            inputData[t] = 0x01;
            lv[t] = TapLargeHash.inputLeaf(uint64(t), hex"01");
        }
        inputRoot = TapLargeHash.merkleRoot(lv);
        vm.deal(claimant, 10 ether);
        vm.deal(sybil, 10 ether);
        vm.deal(honest, 10 ether);
        vm.deal(honest2, 10 ether);
    }

    function _bogusLeaves() internal view returns (bytes32[] memory leaves) {
        leaves = new bytes32[](6); // T = 40, K = 8
        leaves[0] = g.initialLeaf();
        for (uint256 k = 1; k < 6; ++k) {
            leaves[k] = keccak256(abi.encode("forged", k));
        }
    }

    function _bogusClaim() internal returns (uint256 id, bytes32[] memory leaves) {
        leaves = _bogusLeaves();
        vm.prank(claimant);
        id = g.claim{value: BOND}(nh2, 40, 8, inputRoot, leaves, inputData);
    }

    /// A valid level-1 challenge at index i of the published list, with the challenger's own (different) leaf.
    function _args(bytes32[] memory leaves, uint32 i) internal view returns (TapLargeDisputeGame.ChallengeArgs memory x) {
        bytes32[][] memory lv = Upload.levels(leaves);
        x.index = i;
        x.proofPrev = Upload.proof(lv, i - 1);
        x.leafCur = leaves[i];
        x.proofCur = Upload.proof(lv, i);
        x.stateRoot = bytes32(uint256(0xdead));
        x.io = bytes32(uint256(0xbeef));
    }

    /// Computes the arguments first (they hash with the SHA-256 precompile, which would consume a prank).
    function _challenge(address who, uint256 id, bytes32[] memory leaves, uint32 i) internal {
        TapLargeDisputeGame.ChallengeArgs memory x = _args(leaves, i);
        vm.prank(who);
        g.challenge{value: BOND}(id, x);
    }

    function _status(uint256 id) internal view returns (TapLargeDisputeGame.Status) {
        return g.claimOf(id).status;
    }

    function _deadline(uint256 id, address ch) internal view returns (uint64 d) {
        (,, d,,,,) = g.stateOf(id, ch);
    }

    /// Repro 1, now failing as an attack: claim + sock-puppet challenge with index 0 in the same block. The sock
    /// puppet's dispute is decided for the claimant (IndexOutOfRange), but the claim stays open; the honest
    /// challenger challenges, the claimant cannot defend its forged leaf, and the claim is rejected. The
    /// attacker (claimant + sock puppet) loses one bond net.
    function test_repro1_sockPuppetIndex0_doesNotFinalise() public {
        uint256 before = claimant.balance + sybil.balance;
        (uint256 id, bytes32[] memory leaves) = _bogusClaim();

        TapLargeDisputeGame.ChallengeArgs memory x; // index 0: out of range
        vm.prank(sybil);
        g.challenge{value: BOND}(id, x);
        (address w,, TapLargeDisputeGame.Reason reason) = g.outcomeOf(id, sybil);
        assertEq(w, claimant);
        assertEq(uint8(reason), uint8(TapLargeDisputeGame.Reason.IndexOutOfRange));
        assertEq(uint8(_status(id)), uint8(TapLargeDisputeGame.Status.Open), "a claimant win does not accept");
        vm.expectRevert(TapLargeDisputeGame.TooEarly.selector);
        g.accept(id);

        // the honest challenger is NOT locked out
        _challenge(honest, id, leaves, 1);
        (TapLargeDisputeGame.Phase ph,,,,,,) = g.stateOf(id, honest);
        assertEq(uint8(ph), uint8(TapLargeDisputeGame.Phase.BeatCommit));

        // the claimant cannot commit a beat tree that ends at its forged leaf and is right; it stalls and loses
        vm.warp(uint256(_deadline(id, honest)) + 1);
        g.timeout(id, honest);
        assertEq(uint8(_status(id)), uint8(TapLargeDisputeGame.Status.Rejected));
        assertEq(g.claimOf(id).rejectedBy, honest);
        vm.warp(block.timestamp + WINDOW);
        vm.expectRevert(TapLargeDisputeGame.ClaimNotOpen.selector);
        g.accept(id);

        assertEq(g.credit(claimant), BOND, "the sock puppet's bond");
        assertEq(g.credit(honest), 2 * BOND, "its own bond and the claimant's");
        vm.prank(claimant);
        g.withdraw();
        assertEq(claimant.balance + sybil.balance, before - BOND, "attacker net cost: one bond");
    }

    /// Repro 2, now failing as an attack: the sock puppet challenges a valid index and stalls after the
    /// claimant's commitBeats; it loses by timeout long before the window ends, the claim stays open, and the
    /// honest challenger still challenges and wins.
    function test_repro2_sockPuppetStall_doesNotFinalise() public {
        (uint256 id, bytes32[] memory leaves) = _bogusClaim();
        _challenge(sybil, id, leaves, 1);

        // the honest challenger may join while the sock puppet's dispute is open
        _challenge(honest, id, leaves, 1);
        assertEq(g.claimOf(id).openDisputes, 2);

        // in the sock puppet's dispute the claimant commits a tree whose ends are L_0 and its forged L_1
        bytes32[] memory beat = new bytes32[](9);
        beat[0] = g.initialLeaf();
        beat[8] = leaves[1];
        bytes32[][] memory blv = Upload.levels(beat);
        vm.prank(claimant);
        g.commitBeats(id, sybil, blv[blv.length - 1][0], Upload.proof(blv, 0), Upload.proof(blv, 8));

        // the sock puppet never moves; after its deadline anyone times it out
        vm.warp(uint256(_deadline(id, sybil)) + 1);
        g.timeout(id, sybil);
        (address w,, TapLargeDisputeGame.Reason reason) = g.outcomeOf(id, sybil);
        assertEq(w, claimant);
        assertEq(uint8(reason), uint8(TapLargeDisputeGame.Reason.Timeout));
        assertLt(block.timestamp, g.claimOf(id).windowEnd, "decided well inside the window");
        assertEq(uint8(_status(id)), uint8(TapLargeDisputeGame.Status.Open));

        // the honest dispute was opened at the same time; the claimant stalled there too and loses it
        g.timeout(id, honest);
        assertEq(uint8(_status(id)), uint8(TapLargeDisputeGame.Status.Rejected));
        assertEq(g.credit(claimant), BOND);
        assertEq(g.credit(honest), 2 * BOND);
    }

    /// A late honest challenger: the sock puppet's dispute has been lost by the sock puppet, then the honest
    /// challenger arrives just before the window ends; the claim is still open for it.
    function test_lateHonestChallengerAfterSockPuppetLost() public {
        (uint256 id, bytes32[] memory leaves) = _bogusClaim();
        _challenge(sybil, id, leaves, 1); // a valid index (only L_0 has a known preimage here)
        vm.warp(uint256(_deadline(id, sybil)) + 1);
        // the claimant did not even move in its own sock puppet's dispute: the sock puppet wins and the claim
        // is rejected (a sock puppet can only hurt its own claim)
        g.timeout(id, sybil);
        assertEq(uint8(_status(id)), uint8(TapLargeDisputeGame.Status.Rejected));

        // second claim: the sock puppet loses, the honest challenger comes at the last second of the window
        (uint256 id2, bytes32[] memory leaves2) = _bogusClaim();
        TapLargeDisputeGame.ChallengeArgs memory x0;
        vm.prank(sybil);
        g.challenge{value: BOND}(id2, x0);
        vm.warp(g.claimOf(id2).windowEnd);
        _challenge(honest, id2, leaves2, 1);
        vm.warp(uint256(g.claimOf(id2).windowEnd) + 1);
        vm.expectRevert(TapLargeDisputeGame.DisputesOpen.selector); // acceptance waits for the open dispute
        g.accept(id2);
        vm.warp(uint256(_deadline(id2, honest)) + 1);
        g.timeout(id2, honest);
        assertEq(uint8(_status(id2)), uint8(TapLargeDisputeGame.Status.Rejected));
    }

    /// Two honest challengers: the first to win takes the claimant's bond and rejects the claim; the other
    /// dispute's moves revert, it cannot be timed out, and `refund` returns its bond once.
    function test_twoChallengers_rejectionVoidsTheOther() public {
        (uint256 id, bytes32[] memory leaves) = _bogusClaim();
        _challenge(honest, id, leaves, 1);
        _challenge(honest2, id, leaves, 1);
        vm.warp(uint256(_deadline(id, honest)) + 1);
        g.timeout(id, honest);
        assertEq(uint8(_status(id)), uint8(TapLargeDisputeGame.Status.Rejected));

        bytes32[] memory none = new bytes32[](0);
        vm.prank(claimant);
        vm.expectRevert(TapLargeDisputeGame.ClaimNotOpen.selector);
        g.commitBeats(id, honest2, bytes32(0), none, none);
        vm.expectRevert(TapLargeDisputeGame.ClaimNotOpen.selector);
        g.timeout(id, honest2);
        TapLargeDisputeGame.ChallengeArgs memory x2 = _args(leaves, 2);
        vm.prank(sybil);
        vm.expectRevert(TapLargeDisputeGame.ClaimNotOpen.selector); // no new challenges either
        g.challenge{value: BOND}(id, x2);

        g.refund(id, honest2);
        vm.expectRevert(TapLargeDisputeGame.WrongPhase.selector);
        g.refund(id, honest2);
        vm.expectRevert(TapLargeDisputeGame.WrongPhase.selector); // a resolved dispute is not refunded
        g.refund(id, honest);
        (TapLargeDisputeGame.Phase ph,,,,,,) = g.stateOf(id, honest2);
        assertEq(uint8(ph), uint8(TapLargeDisputeGame.Phase.Voided));
        assertEq(g.claimOf(id).openDisputes, 0);
        assertEq(g.credit(honest), 2 * BOND);
        assertEq(g.credit(honest2), BOND);
        assertEq(g.credit(claimant), 0);
        assertEq(address(g).balance, 3 * BOND, "all three bonds held until withdrawn");
    }

    /// One dispute per challenger address and claim; refunds only for rejected claims.
    function test_oneDisputePerChallenger_andRefundGuards() public {
        (uint256 id, bytes32[] memory leaves) = _bogusClaim();
        _challenge(honest, id, leaves, 1);
        TapLargeDisputeGame.ChallengeArgs memory x2 = _args(leaves, 2);
        vm.prank(honest);
        vm.expectRevert(TapLargeDisputeGame.AlreadyChallenged.selector);
        g.challenge{value: BOND}(id, x2);
        vm.expectRevert(TapLargeDisputeGame.ClaimNotOpen.selector);
        g.refund(id, honest);
    }

    /// A claim that is challenged only by losers is accepted after the window: the claimant gets every
    /// challenger's bond and its own back.
    function test_claimantWinsAll_acceptedAfterWindow() public {
        (uint256 id, bytes32[] memory leaves) = _bogusClaim();
        TapLargeDisputeGame.ChallengeArgs memory x0;
        vm.prank(sybil);
        g.challenge{value: BOND}(id, x0); // loses at once
        TapLargeDisputeGame.ChallengeArgs memory eq = _args(leaves, 1);
        eq.stateRoot = bytes32(0);
        eq.io = bytes32(0);
        vm.prank(honest);
        g.challenge{value: BOND}(id, eq); // its leaf differs; it then stalls at its commitBeats
        bytes32[] memory beat = new bytes32[](9);
        beat[0] = g.initialLeaf();
        beat[8] = leaves[1];
        bytes32[][] memory blv = Upload.levels(beat);
        vm.prank(claimant);
        g.commitBeats(id, honest, blv[blv.length - 1][0], Upload.proof(blv, 0), Upload.proof(blv, 8));
        vm.warp(uint256(_deadline(id, honest)) + 1);
        g.timeout(id, honest);
        vm.expectRevert(TapLargeDisputeGame.TooEarly.selector);
        g.accept(id);
        vm.warp(uint256(g.claimOf(id).windowEnd) + 1);
        g.accept(id);
        assertEq(uint8(_status(id)), uint8(TapLargeDisputeGame.Status.Accepted));
        assertEq(g.credit(claimant), 3 * BOND);
    }
}
