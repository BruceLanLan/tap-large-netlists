// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {Test, console} from "forge-std/Test.sol";
import {TapLargeNetlistStoreOwned} from "../../src/TapLargeNetlistStore.sol";
import {TapLargeVerifier} from "../../src/TapLargeVerifier.sol";
import {TapLargeHash} from "../../src/TapLargeHash.sol";
import {TapLargeDisputeGame} from "../../src/game/TapLargeDisputeGame.sol";
import {Upload} from "../utils/Upload.sol";
import {Cases} from "../utils/Cases.sol";

/// Test-only: puts a dispute straight into the Prove phase, so that the costliest final calls measured by
/// test/GasVerify.t.sol (files of 2^24 signals, which the Python parties cannot play through in reasonable time)
/// can be sent through the game's own proveSegment / proveBeatEnd.
contract GameHarness is TapLargeDisputeGame {
    constructor(Params memory p) TapLargeDisputeGame(p) {}

    function forceProveSeg(
        address claimant_,
        address challenger_,
        bytes32 nh2,
        bytes32 latchRootT,
        bytes32 memRootT,
        TapLargeVerifier.SegTuple calldata a,
        TapLargeVerifier.SegTuple calldata b
    ) external returns (uint256 id) {
        id = _force(claimant_, challenger_, nh2);
        Dispute storage d = _disputes[id][challenger_];
        d.kind = KIND_SEG;
        d.latchRootT = latchRootT;
        d.memRootT = memRootT;
        d.lo = 6;
        d.hi = 7;
        d.loLeaf = _segLeaf(_claims[id], 6, a);
        d.hiLeaf[0] = _segLeaf(_claims[id], 7, b);
    }

    function forceProveEnd(
        address claimant_,
        address challenger_,
        bytes32 nh2,
        TapLargeVerifier.SegTuple calldata segM,
        uint8 kind,
        TapLargeVerifier.EndTuple calldata a,
        TapLargeVerifier.EndTuple calldata b
    ) external returns (uint256 id) {
        id = _force(claimant_, challenger_, nh2);
        Dispute storage d = _disputes[id][challenger_];
        d.kind = kind;
        d.segMIndex = 9;
        d.segMLeaf = _segLeaf(_claims[id], 9, segM);
        d.lo = 0;
        d.hi = 1;
        d.loLeaf = TapLargeHash.endLeaf(kind, 0, a.ordinal, a.root);
        d.hiLeaf[0] = TapLargeHash.endLeaf(kind, 1, b.ordinal, b.root);
    }

    function _force(address claimant_, address challenger_, bytes32 nh2) internal returns (uint256 id) {
        id = _claims.length;
        Claim storage c = _claims.push();
        c.claimant = claimant_;
        c.T = 1;
        c.K = 1;
        c.nCp = 2;
        (c.fileLen, c.nIn, c.nOut,,) = store.largeInfo(nh2);
        (c.budget.gates, c.budget.extWords, c.budget.chunks) = verifier.segmentBudget();
        c.netlistHash2 = nh2;
        c.hasMem = _memFlag(nh2);
        c.status = Status.Open;
        c.windowEnd = uint64(block.timestamp) + challengeWindow;
        c.openDisputes = 1;
        Dispute storage d = _disputes[id][challenger_];
        d.phase = Phase.Prove;
        d.turn = Role.Claimant;
        d.prover = Role.Claimant;
        d.deadline = uint64(block.timestamp) + moveTime;
    }
}

/// Limits of the game: bounds on T and K, the two input cases, time-based deadlines scaled with K, the claim
/// with the RECOMMENDED maximum of 2,048 checkpoint leaves, the costliest final calls of the gas suite sent
/// through the game, and the guards on claims and moves.
contract GameLimitsTest is Test {
    uint256 internal constant TX_GAS_CAP = 16_777_216;
    uint64 internal constant WINDOW = 1 days;
    uint64 internal constant MOVE = 1 hours;
    uint64 internal constant PER_BEAT = 60;
    TapLargeNetlistStoreOwned internal store;
    address internal claimant = makeAddr("claimant");
    address internal challenger = makeAddr("challenger");
    address internal provider = makeAddr("inputProvider");

    function setUp() public {
        store = new TapLargeNetlistStoreOwned(address(this));
        vm.deal(claimant, 100 ether);
        vm.deal(challenger, 100 ether);
        vm.deal(provider, 100 ether);
    }

    function _params(TapLargeVerifier v, uint64 maxK, uint64 maxT, address prov)
        internal
        view
        returns (TapLargeDisputeGame.Params memory)
    {
        return TapLargeDisputeGame.Params({
            store: store,
            verifier: v,
            bond: 1 ether,
            challengeWindow: WINDOW,
            moveTime: MOVE,
            perBeatTime: PER_BEAT,
            maxK: maxK,
            maxT: maxT,
            inputProvider: prov
        });
    }

    /// The message example (nIn = 1) and a game for it: case (a) if prov is zero, else case (b).
    function _message(uint64 maxK, uint64 maxT, address prov) internal returns (bytes32 nh2, TapLargeDisputeGame g) {
        Cases.Reader memory r = Cases.Reader(vm.readFileBinary("test/data/game_moves.bin"), 5);
        bytes memory file = Cases.bytesN(r, Cases.uintN(r, 4)); // game 0: the message example
        nh2 = Upload.upload(store, file, true, 0);
        g = new TapLargeDisputeGame(_params(new TapLargeVerifier(store, 16, 4, 2), maxK, maxT, prov));
    }

    function _message() internal returns (bytes32 nh2, TapLargeDisputeGame g) {
        return _message(64, 64 * 2047, address(0));
    }

    function _leaves(uint256 n, bytes32 l0) internal pure returns (bytes32[] memory leaves) {
        leaves = new bytes32[](n);
        leaves[0] = l0;
        for (uint256 k = 1; k < n; ++k) {
            leaves[k] = keccak256(abi.encode(k));
        }
    }

    /// T packed input vectors of the message example (nIn = 1: one byte each, bit 0 only) and their root (§4.4).
    function _inputs(uint256 T) internal view returns (bytes memory data, bytes32 root) {
        data = new bytes(T);
        bytes32[] memory lv = new bytes32[](T);
        for (uint256 t; t < T; ++t) {
            data[t] = bytes1(uint8(t % 2));
            bytes memory one = new bytes(1);
            one[0] = data[t];
            lv[t] = TapLargeHash.inputLeaf(uint64(t), one);
        }
        root = TapLargeHash.merkleRoot(lv);
    }

    // ------------------------------------------------------------------ §7.1: 2,048 leaves, case (b)

    /// §7.1: 2,048 leaves in one transaction (n = ceil(T/K) + 1), with the inputs published beforehand by an
    /// input provider that is not the claimant (case (b)): K = 32, T = 2,047 · 32 = 65,504 beats, one input byte
    /// per beat.
    function test_claimWith2048Leaves() public {
        (bytes32 nh2, TapLargeDisputeGame g) = _message(32, 32 * 2047, provider);
        uint64 K = 32;
        uint64 T = 2047 * K; // checkpoint times 0, K, …, 2046K, T
        (bytes memory data, bytes32 inputRoot) = _inputs(T);
        vm.prank(provider);
        g.publishInputs(nh2, T, inputRoot, data);
        console.log("publishInputs of 65,504 input bytes, tx gas incl. calldata:", vm.lastFrameGas().gasTotalUsed);
        bytes32[] memory leaves = _leaves(2048, g.initialLeaf());
        vm.prank(claimant);
        g.claim{value: 1 ether}(nh2, T, K, inputRoot, leaves, "");
        uint256 used = vm.lastFrameGas().gasTotalUsed;
        console.log("claim with 2,048 leaves, tx gas incl. calldata:", used);
        assertLe(used, TX_GAS_CAP);
        assertEq(g.claimOf(0).checkpointRoot, TapLargeHash.merkleRoot(leaves));
        assertEq(g.claimOf(0).nCp, 2048);
        assertEq(g.claimOf(0).inputRoot, inputRoot);
    }

    // ------------------------------------------------------------------ §7.1 inputs

    /// Case (a): the game computes inputRoot from the inputs in the claim; the length, the padding bits and the
    /// stated root are checked.
    function test_caseA_gameComputesInputRoot() public {
        (bytes32 nh2, TapLargeDisputeGame g) = _message();
        bytes32[] memory leaves = _leaves(6, g.initialLeaf()); // T = 40, K = 8
        (bytes memory data, bytes32 root) = _inputs(40);
        vm.startPrank(claimant);
        vm.expectRevert(TapLargeDisputeGame.BadInputData.selector); // empty inputs
        g.claim{value: 1 ether}(nh2, 40, 8, root, leaves, "");
        vm.expectRevert(TapLargeDisputeGame.BadInputData.selector); // one byte short
        g.claim{value: 1 ether}(nh2, 40, 8, root, leaves, _slice(data, 39));
        bytes memory padded = bytes.concat(data);
        padded[3] = 0x02; // nIn = 1: bits 1..7 must be zero
        vm.expectRevert(TapLargeDisputeGame.BadInputData.selector);
        g.claim{value: 1 ether}(nh2, 40, 8, root, leaves, padded);
        bytes memory other = bytes.concat(data);
        other[1] ^= 0x01; // inputs that do not hash to the stated root (the reviewer's X ≠ Y)
        vm.expectRevert(TapLargeDisputeGame.InputRootMismatch.selector);
        g.claim{value: 1 ether}(nh2, 40, 8, root, leaves, other);
        uint256 id = g.claim{value: 1 ether}(nh2, 40, 8, root, leaves, data);
        vm.stopPrank();
        assertEq(g.claimOf(id).inputRoot, root);
    }

    /// Case (b): only the provider publishes; the provider can never claim; a claim must use a published
    /// (netlistHash2, T, inputRoot); the published inputs must have the right length.
    function test_caseB_providerNotClaimant() public {
        (bytes32 nh2, TapLargeDisputeGame g) = _message(64, 64 * 2047, provider);
        bytes32[] memory leaves = _leaves(6, g.initialLeaf());
        (bytes memory data, bytes32 root) = _inputs(40);
        vm.prank(claimant);
        vm.expectRevert(TapLargeDisputeGame.NotProvider.selector); // the claimant cannot publish inputs
        g.publishInputs(nh2, 40, root, data);
        vm.prank(provider);
        vm.expectRevert(TapLargeDisputeGame.BadInputData.selector);
        g.publishInputs(nh2, 40, root, _slice(data, 39));
        vm.prank(claimant);
        vm.expectRevert(TapLargeDisputeGame.InputsNotPublished.selector);
        g.claim{value: 1 ether}(nh2, 40, 8, root, leaves, "");
        vm.prank(provider);
        g.publishInputs(nh2, 40, root, data);
        vm.prank(provider);
        vm.expectRevert(TapLargeDisputeGame.ProviderCannotClaim.selector);
        g.claim{value: 1 ether}(nh2, 40, 8, root, leaves, "");
        bytes32[] memory leaves7 = _leaves(7, g.initialLeaf());
        vm.startPrank(claimant);
        vm.expectRevert(TapLargeDisputeGame.InputsNotPublished.selector); // another T
        g.claim{value: 1 ether}(nh2, 48, 8, root, leaves7, "");
        vm.expectRevert(TapLargeDisputeGame.BadInputData.selector); // case (b) claims carry no inputs
        g.claim{value: 1 ether}(nh2, 40, 8, root, leaves, data);
        uint256 id = g.claim{value: 1 ether}(nh2, 40, 8, root, leaves, "");
        vm.stopPrank();
        assertEq(g.claimOf(id).inputRoot, root);
    }

    // ------------------------------------------------------------------ §7.1 bounds on T and K

    function test_boundsOnTandK() public {
        // maxK = 8, maxT = 24 (below maxK · 2,047, as an application that wants short runs would set it)
        (bytes32 nh2, TapLargeDisputeGame g) = _message(8, 24, address(0));
        assertEq(g.maxK(), 8);
        assertEq(g.maxT(), 24);
        (bytes memory d24, bytes32 r24) = _inputs(24);
        (bytes memory d32, bytes32 r32) = _inputs(32);
        bytes32 l0 = g.initialLeaf();
        vm.startPrank(claimant);
        vm.expectRevert(TapLargeDisputeGame.BoundsExceeded.selector); // K = 12 > maxK
        g.claim{value: 1 ether}(nh2, 24, 12, r24, _leaves(3, l0), d24);
        vm.expectRevert(TapLargeDisputeGame.BoundsExceeded.selector); // T = 32 > maxT
        g.claim{value: 1 ether}(nh2, 32, 8, r32, _leaves(5, l0), d32);
        g.claim{value: 1 ether}(nh2, 24, 8, r24, _leaves(4, l0), d24);
        vm.stopPrank();
        // the reviewer's claim T = 2^50, K = 2^40 (1,025 leaves, inside the leaf cap) is refused by the bounds
        (, TapLargeDisputeGame g2) = _message();
        bytes32[] memory l1025 = _leaves(1025, l0);
        vm.prank(claimant);
        vm.expectRevert(TapLargeDisputeGame.BoundsExceeded.selector);
        g2.claim{value: 1 ether}(nh2, uint64(1) << 50, uint64(1) << 40, 0, l1025, "");
    }

    /// maxT must not exceed maxK · (MAX_CHECKPOINTS − 1), bounds must be nonzero, and deadlines must fit.
    function test_constructorBounds() public {
        TapLargeVerifier v = new TapLargeVerifier(store, 16, 4, 2);
        vm.expectRevert(TapLargeDisputeGame.BadParams.selector);
        new TapLargeDisputeGame(_params(v, 8, 8 * 2047 + 1, address(0)));
        vm.expectRevert(TapLargeDisputeGame.BadParams.selector);
        new TapLargeDisputeGame(_params(v, 0, 0, address(0)));
        TapLargeDisputeGame.Params memory p = _params(v, 8, 8, address(0));
        p.perBeatTime = type(uint64).max / 8;
        vm.expectRevert(TapLargeDisputeGame.BadParams.selector);
        new TapLargeDisputeGame(p);
        new TapLargeDisputeGame(_params(v, 8, 8 * 2047, address(0)));
    }

    // ------------------------------------------------------------------ §7.1 deadlines (time-based)

    /// The commitBeats deadline is moveTime + perBeatTime · K; other moves get moveTime; the window and the
    /// deadlines are timestamps (vm.warp), not block numbers.
    function test_deadlinesScaleWithK() public {
        (bytes32 nh2, TapLargeDisputeGame g) = _message();
        (bytes memory data, bytes32 root) = _inputs(40);
        bytes32[] memory leaves = _leaves(6, g.initialLeaf());
        vm.prank(claimant);
        uint256 id = g.claim{value: 1 ether}(nh2, 40, 8, root, leaves, data);
        uint64 t0 = uint64(block.timestamp);
        assertEq(g.claimOf(id).windowEnd, t0 + WINDOW);
        TapLargeDisputeGame.ChallengeArgs memory x = _challengeArgs(leaves, 1);
        vm.roll(block.number + 1_000_000); // blocks alone change nothing
        vm.prank(challenger);
        g.challenge{value: 1 ether}(id, x);
        (TapLargeDisputeGame.Phase ph,, uint64 deadline,,,,) = g.stateOf(id, challenger);
        assertEq(uint8(ph), uint8(TapLargeDisputeGame.Phase.BeatCommit));
        assertEq(deadline, t0 + MOVE + PER_BEAT * 8);
        vm.warp(deadline);
        vm.expectRevert(TapLargeDisputeGame.TooEarly.selector);
        g.timeout(id, challenger);
        vm.warp(uint256(deadline) + 1);
        g.timeout(id, challenger); // the claimant missed commitBeats
        (address w,,) = g.outcomeOf(id, challenger);
        assertEq(w, challenger);
        assertEq(uint8(g.claimOf(id).status), uint8(TapLargeDisputeGame.Status.Rejected));
    }

    // ------------------------------------------------------------------ guards

    function test_claimGuards() public {
        (bytes32 nh2, TapLargeDisputeGame g) = _message();
        bytes32 l0 = g.initialLeaf();
        (bytes memory d40, bytes32 r40) = _inputs(40);
        vm.startPrank(claimant);
        vm.expectRevert(TapLargeDisputeGame.BadClaim.selector); // 2,049 leaves
        g.claim{value: 1 ether}(nh2, 2048 * 2, 2, 0, _leaves(2049, l0), "");
        vm.expectRevert(TapLargeDisputeGame.BadClaim.selector); // one leaf too few
        g.claim{value: 1 ether}(nh2, 40, 8, r40, _leaves(5, l0), d40);
        vm.expectRevert(TapLargeDisputeGame.BadClaim.selector); // L_0 is not the constant
        g.claim{value: 1 ether}(nh2, 40, 8, r40, _leaves(6, bytes32(uint256(7))), d40);
        vm.expectRevert(TapLargeDisputeGame.WrongBond.selector);
        g.claim{value: 0.5 ether}(nh2, 40, 8, r40, _leaves(6, l0), d40);
        vm.expectRevert(abi.encodeWithSelector(TapLargeDisputeGame.NotStored.selector, bytes32(uint256(5))));
        g.claim{value: 1 ether}(bytes32(uint256(5)), 40, 8, r40, _leaves(6, l0), d40);
        vm.stopPrank();
    }

    /// §7.1 item 1: a file with a missing chunk cannot be claimed about.
    function test_claimRequiresLargeStored() public {
        Cases.Reader memory r = Cases.Reader(vm.readFileBinary("test/data/game_moves.bin"), 5);
        Cases.bytesN(r, Cases.uintN(r, 4));
        r.p += 13;
        bytes memory large = Cases.bytesN(r, Cases.uintN(r, 4)); // game 1: the large example (6 chunks)
        bytes32 nh2 = Upload.upload(store, large, false, 0); // only chunk 0
        TapLargeDisputeGame g =
            new TapLargeDisputeGame(_params(new TapLargeVerifier(store, 6000, 256, 16), 64, 64 * 2047, address(0)));
        bytes32[] memory leaves = _leaves(4, g.initialLeaf());
        vm.prank(claimant);
        vm.expectRevert(abi.encodeWithSelector(TapLargeDisputeGame.NotStored.selector, nh2));
        g.claim{value: 1 ether}(nh2, 6, 2, 0, leaves, "");
    }

    /// Moves out of turn, after the deadline, or before it for timeout and accept, are rejected; an unchallenged
    /// claim is accepted only after the window.
    function test_moveGuards() public {
        (bytes32 nh2, TapLargeDisputeGame g) = _message();
        (bytes memory data, bytes32 root) = _inputs(40);
        bytes32[] memory leaves = _leaves(6, g.initialLeaf());
        vm.prank(claimant);
        uint256 id = g.claim{value: 1 ether}(nh2, 40, 8, root, leaves, data);
        vm.expectRevert(TapLargeDisputeGame.TooEarly.selector);
        g.accept(id);
        vm.expectRevert(TapLargeDisputeGame.WrongPhase.selector); // no dispute by this address
        g.timeout(id, challenger);
        TapLargeDisputeGame.ChallengeArgs memory x;
        x.index = 1;
        vm.prank(claimant);
        vm.expectRevert(TapLargeDisputeGame.NotYourTurn.selector);
        g.challenge{value: 1 ether}(id, x);
        vm.warp(block.timestamp + WINDOW + 1);
        vm.prank(challenger);
        vm.expectRevert(TapLargeDisputeGame.TooLate.selector);
        g.challenge{value: 1 ether}(id, x);
        vm.prank(challenger);
        vm.expectRevert(TapLargeDisputeGame.WrongPhase.selector);
        g.commitBeats(id, challenger, 0, new bytes32[](0), new bytes32[](0));
        g.accept(id);
        vm.expectRevert(TapLargeDisputeGame.ClaimNotOpen.selector);
        g.accept(id);
        assertEq(g.credit(claimant), 1 ether);
        vm.prank(claimant);
        g.withdraw();
        assertEq(claimant.balance, 100 ether);
    }

    function _challengeArgs(bytes32[] memory leaves, uint32 i)
        internal
        view
        returns (TapLargeDisputeGame.ChallengeArgs memory x)
    {
        bytes32[][] memory lv = Upload.levels(leaves);
        x.index = i;
        x.prevStateRoot = bytes32(0);
        x.prevIo = bytes32(0);
        x.proofPrev = Upload.proof(lv, i - 1);
        x.leafCur = leaves[i];
        x.proofCur = Upload.proof(lv, i);
        x.stateRoot = bytes32(uint256(0xdead));
        x.io = bytes32(uint256(0xbeef));
    }

    function _slice(bytes memory b, uint256 n) internal pure returns (bytes memory out) {
        out = new bytes(n);
        for (uint256 k; k < n; ++k) {
            out[k] = b[k];
        }
    }

    /// The costliest calls of the gas suite (test/data/gas_cases.bin) sent through the game's final move: a
    /// default-budget segment of 6,000 NANDs reading 256 scattered words of the deepest signal tree (a file of
    /// 16,777,024 signals, 65,536 words; 75 KB witness), a 256-value state step reading 256 scattered P words
    /// (73 KB witness), and the costliest segments of a file with MEM: 64 RAM write (read) ports with dense memory
    /// (64 proofs of 804 bytes) and 4,078 (4,142) NANDs, where the game hashes M into the segment leaves and passes
    /// latchRoot_t and memRoot_t.
    function test_worstFinalMoveThroughGame() public {
        (Cases.Reader memory r, bytes[] memory files) = Cases.open(vm.readFileBinary("test/data/gas_cases.bin"));
        uint256 n = Cases.uintN(r, 4);
        TapLargeVerifier v = new TapLargeVerifier(store, 6000, 256, 16);
        GameHarness g = new GameHarness(_params(v, 64, 64 * 2047, address(0)));
        bytes32[] memory nh2s = new bytes32[](files.length);
        uint256 found;
        for (uint256 i; i < n; ++i) {
            Cases.Case memory c = Cases.next(r);
            bool seg = _startsWith(c.name, "wide scatter default budget") || _startsWith(c.name, "ram worst");
            bool end = _startsWith(c.name, "wideA beat-end state 0..255");
            if (!seg && !end) continue;
            if (nh2s[c.fileIdx] == 0) nh2s[c.fileIdx] = Upload.upload(store, files[c.fileIdx], true, 0);
            bytes32 nh2 = nh2s[c.fileIdx];
            uint256 id;
            bytes memory cd;
            if (seg) {
                id = g.forceProveSeg(claimant, challenger, nh2, c.latchRoot, c.memRoot, c.a, c.b);
                cd = abi.encodeCall(TapLargeDisputeGame.proveSegment, (id, challenger, c.a, c.b, c.witness));
            } else {
                id = g.forceProveEnd(claimant, challenger, nh2, c.a, 2, c.ea, c.eb);
                cd = abi.encodeCall(TapLargeDisputeGame.proveBeatEnd, (id, challenger, c.a, c.ea, c.eb, c.witness));
            }
            vm.prank(claimant);
            (bool ok,) = address(g).call(cd);
            assertTrue(ok, c.name);
            uint256 used = vm.lastFrameGas().gasTotalUsed;
            (, TapLargeDisputeGame.Role role, TapLargeDisputeGame.Reason reason) = g.outcomeOf(id, challenger);
            assertEq(uint8(reason), uint8(TapLargeDisputeGame.Reason.VerifierTrue), c.name);
            assertEq(uint8(role), uint8(TapLargeDisputeGame.Role.Claimant));
            console.log(c.name);
            console.log("    final move through the game: tx gas %d, calldata %d bytes", used, cd.length);
            assertLe(used, TX_GAS_CAP);
            ++found;
        }
        assertEq(found, 4);
    }

    function _startsWith(string memory s, string memory p) internal pure returns (bool) {
        bytes memory a = bytes(s);
        bytes memory b = bytes(p);
        if (a.length < b.length) return false;
        for (uint256 k; k < b.length; ++k) {
            if (a[k] != b[k]) return false;
        }
        return true;
    }
}
