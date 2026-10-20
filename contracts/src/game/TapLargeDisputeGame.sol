// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {ITapLargeNetlistStore} from "../ITapLargeNetlistStore.sol";
import {TapLargeVerifier} from "../TapLargeVerifier.sol";
import {TapLargeHash} from "../TapLargeHash.sol";
import {TapLargeGameLib} from "./TapLargeGameLib.sol";

/// @title Informative reference dispute game for the TAP "Large Netlists and Verifiable Execution" (§7)
/// @notice A minimal bisection game over the evaluation of a tapnet/2 file. It shows one way to wire the data and
///         rules of §7 to the one-step verifiers of §6; it is NOT normative and NOT production code (no
///         upgradability, no fee, one bond size).
///
///         Claims and disputes (§7.1). A claim is open for a challenge window of `challengeWindow` seconds. Any
///         number of challengers may challenge it within the window, each with its own bond, and each challenge
///         starts an independent two-party dispute keyed by (claim id, challenger address); one address has at
///         most one dispute per claim. New challengers may join while other disputes about the claim are open.
///         The claim's status is kept apart from its disputes:
///
///           Open      the window is open, or disputes about the claim are still running;
///           Rejected  the claimant lost a dispute (as soon as it happens, also within the window);
///           Accepted  only through `accept`, after the window has ended, when no dispute is open and none was
///                     won by a challenger.
///
///         A claimant that wins a dispute does not make its claim accepted; the claim stays open for other
///         challengers until the window ends.
///
///         Bonds. The claimant posts one bond with the claim and each challenger one bond with its challenge.
///           - The claimant wins a dispute: it receives that challenger's bond at once.
///           - A challenger wins a dispute: the claim is rejected, and that challenger receives its own bond and
///             the claimant's bond (the first challenger to win takes the claimant's bond; there is only one).
///           - Disputes still open when the claim is rejected end without a winner: their moves revert, and
///             `refund` returns the challenger's bond (anyone may call it).
///           - The claim is accepted: the claimant's bond is returned.
///         Payouts are pull payments through `withdraw`.
///
///         Inputs (§7.1). Fixed per game instance by `inputProvider`:
///           - zero address, case (a): the claim carries the packed inputs of all T beats as calldata, and the
///             game computes inputRoot from them in `claim` (length checked, unused bits zero), so the published
///             inputs and inputRoot agree by construction; practical for small T only;
///           - otherwise, case (b): the named provider (which can never make a claim in this game) first
///             publishes (netlistHash2, T, inputRoot) with the inputs as calldata through `publishInputs`; a
///             claim can only use a published triple. The game checks the length of the inputs, not their
///             consistency with inputRoot (§7.1 and Security Considerations of the TAP).
///
///         Bounds and deadlines (§7.1). `maxK` and `maxT` bound K and T; deadlines are timestamps. Every move has
///         `moveTime` seconds, and a commitBeats move `moveTime + perBeatTime · K`, since the party has to
///         recompute up to K beats and build the K+1-leaf beat tree.
///
///         Dispute flow (one active party at a time; the party whose turn it is loses if it misses its deadline):
///
///           challenge      challenger, within the window: bond + first differing index i + its own (stateRoot,
///                          io) at t_i + proofs of the claimant's L_{i-1}, L_i (§7.2).
///           commitBeats    claimant, then challenger: root of the beat leaves of [t_{i-1}, t_i] (size fixed by
///                          the game) + proofs that it starts at L_{i-1} and ends at the party's own leaf (§7.3).
///           revealBeat     claimant, then challenger, per round: the leaf at the midpoint the game picks (§7.3).
///           commitTuples   claimant (with the inputs of beat t against inputRoot and, for a file with MEM and
///                          stateRoot_t ≠ EMPTY, the opening (latchRoot_t, memRoot_t) of stateRoot_t), then
///                          challenger: the three tuple lists of beat t (roots and lengths) with their first and
///                          last tuples; reveal checks 1–5 of §7.4; then the game picks the first list whose last
///                          tuples differ.
///           revealSeg /    claimant, then challenger: tuple h = min(lastA, lastB); then bisection rounds; or, if
///           revealEnd      the tuples at h are equal, the longer list's party reveals tuple h + 1 (§7.4).
///           proveSegment / the prover (the claimant, or the longer list's party): exactly one verifier call,
///           proveBeatEnd   verifySegment or verifyBeatEnd; the prover wins iff it returns true (§7.4).
///           timeout        anyone, after the deadline: the party to move loses.
///
///         Rules for failed moves. A reveal checked against the party's OWN earlier commitment (a Merkle proof
///         against a root it committed, a proof of a published leaf it names, a reveal check of §7.4) that does
///         not verify makes that party lose at once (§7.1). Values a party must merely re-pass and that the game
///         already holds as hashes (beat leaf preimages, the agreed tuples at the final call) are checked with a
///         revert: a party that cannot supply them simply times out.
///
///         Files with RAM ports (MEM flag, §1.1, read from chunk 0 when the claim is made): segment leaves hash
///         M, check 5 compares stateRoot_{t+1} with SHA-256(0x0C ‖ last state root ‖ last M), and the final call
///         passes the two roots of the opening. Tree computations over calldata lists live in TapLargeGameLib
///         (an external library), which keeps this contract below the EIP-170 code size limit.
///
///         Events. `Challenged` is emitted for every challenge, also one that loses in the same transaction (out
///         of range index, failed proof, equal leaves); `Resolved` then follows in that transaction. `Resolved`
///         carries the reason; when a final call decides, `VerifierCalled` (with the list kind and the checked
///         index j) is emitted just before it.
contract TapLargeDisputeGame {
    // ------------------------------------------------------------------ types

    enum Status {
        None,
        Open, // challenge window open, or disputes running
        Rejected, // a challenger won a dispute
        Accepted // window ended, no dispute open, none won by a challenger
    }

    enum Phase {
        None, // no dispute by this challenger
        BeatCommit, // level 2: beat roots
        BeatBisect, // level 2: bisection over beats
        TupleCommit, // level 3: inputs, tuple lists, reveal checks
        TupleAtH, // level 3: both reveal tuple h of the picked list
        TupleBisect, // level 3: bisection over the picked list
        TupleExtra, // level 3: equal at h, the longer list's party reveals tuple h + 1
        Prove, // level 3: the final verifier call
        Resolved, // dispute decided
        Voided // the claim was rejected in another dispute; bond refunded
    }

    enum Role {
        Claimant,
        Challenger
    }

    /// Why a dispute ended (stored and emitted; the tests assert it).
    enum Reason {
        None,
        Timeout, // the party to move missed its deadline
        IndexOutOfRange, // level 1: i ∉ [1, n)
        BadCheckpointProof, // level 1: a proof of L_{i-1} or L_i against checkpointRoot failed
        LeavesEqual, // level 1: the challenger's leaf at t_i equals L_i
        BadEndpoints, // level 2: beat tree does not start at L_{i-1} or end at the party's own leaf
        BadProof, // a bisection reveal does not verify against the party's own root
        BadInputs, // level 3: inputs malformed or not under inputRoot at index t
        BadFirstTuple, // level 3, check 1/3: first tuple is not the public seg_0 or (0, EMPTY)
        SegNotAtEof, // level 3, check 2
        OutputMismatch, // level 3, check 4
        StateMismatch, // level 3, check 5
        BadLastTuple, // level 3: the proof of a last tuple failed
        VerifierTrue, // final call returned true: the prover wins
        VerifierFalse, // final call returned false: the prover loses
        BadOpening // level 3, file with MEM: the claimant's (latchRoot_t, memRoot_t) does not open stateRoot_t
    }

    /// Constructor parameters (one struct, to keep the constructor readable).
    struct Params {
        ITapLargeNetlistStore store;
        TapLargeVerifier verifier;
        uint256 bond;
        uint64 challengeWindow; // seconds from the claim
        uint64 moveTime; // seconds per move
        uint64 perBeatTime; // extra seconds per beat of K for a commitBeats move
        uint64 maxK; // §7.1: upper bound on K
        uint64 maxT; // §7.1: upper bound on T, at most maxK · (MAX_CHECKPOINTS − 1)
        address inputProvider; // zero: case (a), the game computes inputRoot; otherwise case (b)
    }

    /// The claim and its public parameters (§7.1), fixed when the claim is made, plus its status.
    struct Claim {
        address claimant;
        uint64 T;
        uint64 K;
        uint32 nCp; // number of checkpoint times
        uint64 fileLen; // largeInfo
        uint32 nIn;
        uint32 nOut;
        TapLargeVerifier.Budget budget; // segmentBudget() at claim time (§5.1)
        bytes32 netlistHash2;
        bytes32 inputRoot;
        bytes32 checkpointRoot;
        Status status;
        bool hasMem; // MEM flag: bit 0 of byte 7 of chunk 0, read when the claim is made (§7.1)
        uint64 windowEnd; // last timestamp at which a challenge is accepted
        uint32 openDisputes;
        address rejectedBy; // the challenger that won (Rejected only)
    }

    /// One dispute, keyed by (claim id, challenger). Level 2 and level 3 reuse lo/hi/loLeaf/hiLeaf/pending/
    /// roots/sizes.
    struct Dispute {
        Phase phase;
        Role turn;
        uint8 kind; // level 3: picked list, 0 segment, 1 output, 2 state
        Role prover; // level 3: whose tuple the final call checks
        Reason reason;
        Role winner;
        uint64 deadline; // last timestamp at which the party to move may move
        uint64 base; // level 2: t_{i-1} (index 0 of the beat trees)
        uint64 top; // level 2: t_i
        uint64 lo; // level 2: beat; level 3: tuple index
        uint64 hi;
        uint64 ask; // level 3: index to reveal now
        bytes32 loLeaf; // the agreed leaf at lo
        bytes32[2] hiLeaf; // each party's leaf at hi (level 3: only the prover's is used at the end)
        bytes32 pending; // the claimant's leaf revealed in the current round
        bytes32[2] roots; // level 2: beat roots; level 3: roots of the picked list
        uint32[2] sizes; // level 3: lengths of the picked list
        // level 3, recorded at the claimant's commitTuples
        bytes32 latchRootT; // latchRoot_t and memRoot_t of the agreed stateRoot_t (arguments of verifySegment)
        bytes32 memRootT;
        bytes32 seg0Leaf; // segLeaf(0, public seg_0 of beat t)
        bytes32[3] clRoots; // claimant's three list roots
        uint32[3] clLengths;
        bytes32[3] clLastHash; // keccak of the claimant's last tuples (compared by value)
        bytes32 segMLeaf; // claimant's last segment leaf (= the agreed seg_m when out/state is picked)
        uint32 segMIndex;
    }

    /// A level-1 challenge (§7.2): the first differing index, the preimage (prevStateRoot, prevIo) of the
    /// accepted leaf L_{i-1}, the claimant's leaf L_i, Merkle proofs of both against checkpointRoot (anyone can
    /// build them from the published list), and the challenger's own (stateRoot, io) at t_i.
    struct ChallengeArgs {
        uint32 index;
        bytes32 prevStateRoot;
        bytes32 prevIo;
        bytes32[] proofPrev;
        bytes32 leafCur;
        bytes32[] proofCur;
        bytes32 stateRoot;
        bytes32 io;
    }

    /// The tuple lists of beat t as one party commits them (§7.4), with the reveal of their first and last
    /// tuples and the beat-leaf preimages the reveal checks need.
    struct TupleLists {
        bytes32 stateRootT; // agreed leaf at t: (stateRoot_t, io_t)
        bytes32 ioT;
        bytes32 stateRootT1; // the party's own leaf at t + 1
        bytes32 ioT1;
        // the claimant's opening (latchRoot_t, memRoot_t) of stateRoot_t, for a file with MEM and stateRoot_t ≠
        // EMPTY (§7.4); ignored otherwise (the game then uses (stateRoot_t, ZERO_MEM)) and in the challenger's move
        bytes32 latchRootT;
        bytes32 memRootT;
        bytes32[3] roots; // segment, output, state
        uint32[3] lengths;
        TapLargeVerifier.SegTuple segLast;
        TapLargeVerifier.EndTuple outLast;
        TapLargeVerifier.EndTuple stateLast;
        bytes32[] proofSeg0;
        bytes32[] proofSegLast;
        bytes32[] proofOut0;
        bytes32[] proofOutLast;
        bytes32[] proofState0;
        bytes32[] proofStateLast;
    }

    // ------------------------------------------------------------------ constants and state

    uint256 public constant MAX_CHECKPOINTS = 2048; // §7.1 RECOMMENDED bound: one transaction
    uint256 internal constant HEADER_LEN = 16;
    bytes32 internal constant EMPTY = bytes32(0);
    bytes32 internal constant ZERO_MEM = 0x073f71c2a5b9f99ff7828db2196e3c833ca70e5225e20912d7dcf150c165dcff;
    uint8 internal constant KIND_SEG = 0;
    uint8 internal constant KIND_OUT = 1;
    uint8 internal constant KIND_STATE = 2;

    ITapLargeNetlistStore public immutable store;
    TapLargeVerifier public immutable verifier;
    uint256 public immutable bond;
    uint64 public immutable challengeWindow;
    uint64 public immutable moveTime;
    uint64 public immutable perBeatTime;
    uint64 public immutable maxK;
    uint64 public immutable maxT;
    address public immutable inputProvider;
    bytes32 public immutable initialLeaf; // L_0 = checkpointLeaf(0, EMPTY, EMPTY)

    Claim[] internal _claims;
    mapping(uint256 id => mapping(address challenger => Dispute)) internal _disputes;
    /// case (b): keccak256(netlistHash2, T, inputRoot) of every triple the provider published
    mapping(bytes32 => bool) public inputsPublished;
    mapping(address => uint256) public credit;

    // ------------------------------------------------------------------ events and errors

    event InputsPublished(bytes32 indexed netlistHash2, uint64 T, bytes32 indexed inputRoot);
    event Claimed(
        uint256 indexed id,
        address indexed claimant,
        bytes32 indexed netlistHash2,
        uint64 T,
        uint64 K,
        bytes32 inputRoot,
        bytes32 checkpointRoot,
        uint64 windowEnd
    );
    event Challenged(uint256 indexed id, address indexed challenger, uint32 index);
    event ListPicked(uint256 indexed id, address indexed challenger, uint8 kind, uint32 h);
    event VerifierCalled(uint256 indexed id, address indexed challenger, uint8 kind, uint64 j, bool result);
    event Resolved(uint256 indexed id, address indexed challenger, address winner, address loser, Reason reason);
    event ClaimRejected(uint256 indexed id, address indexed challenger);
    event DisputeVoided(uint256 indexed id, address indexed challenger);
    event Accepted(uint256 indexed id);

    error BadParams();
    error NotStored(bytes32 netlistHash2);
    error BadClaim();
    error BoundsExceeded();
    error BadInputData();
    error InputRootMismatch();
    error InputsNotPublished();
    error NotProvider();
    error ProviderCannotClaim();
    error WrongBond();
    error ClaimNotOpen();
    error AlreadyChallenged();
    error WrongPhase();
    error NotYourTurn();
    error TooLate();
    error TooEarly();
    error DisputesOpen();
    error BadPreimage();
    error WrongListKind();
    error UnreachableEqualEnds();
    error TransferFailed();

    constructor(Params memory p) {
        // maxT ≤ maxK · (MAX_CHECKPOINTS − 1) follows from the leaf bound; deadlines must fit in uint64 for a
        // very long time (uint32 seconds is about 136 years)
        if (
            p.maxK == 0 || p.maxT == 0 || uint256(p.maxT) > uint256(p.maxK) * (MAX_CHECKPOINTS - 1)
                || uint256(p.moveTime) + uint256(p.perBeatTime) * p.maxK > type(uint32).max
                || p.challengeWindow > type(uint32).max
        ) revert BadParams();
        store = p.store;
        verifier = p.verifier;
        bond = p.bond;
        challengeWindow = p.challengeWindow;
        moveTime = p.moveTime;
        perBeatTime = p.perBeatTime;
        maxK = p.maxK;
        maxT = p.maxT;
        inputProvider = p.inputProvider;
        initialLeaf = TapLargeHash.checkpointLeaf(0, EMPTY, EMPTY);
    }

    // ================================================================== §7.1 inputs, case (b)

    /// @notice Case (b) only: the input provider publishes the packed inputs of all T beats of a run of
    ///         `netlistHash2` (calldata is the publication) and the inputRoot it supplies for them. Claims can
    ///         use the triple from then on. Only the length is checked: T · ⌈nIn/8⌉ bytes.
    function publishInputs(bytes32 netlistHash2, uint64 T, bytes32 inputRoot, bytes calldata inputData) external {
        if (inputProvider == address(0) || msg.sender != inputProvider) revert NotProvider();
        if (!store.largeStored(netlistHash2)) revert NotStored(netlistHash2);
        if (T == 0 || T > maxT) revert BoundsExceeded();
        (, uint32 nIn,,,) = store.largeInfo(netlistHash2);
        if (inputData.length != uint256(T) * ((uint256(nIn) + 7) / 8)) revert BadInputData();
        inputsPublished[keccak256(abi.encode(netlistHash2, T, inputRoot))] = true;
        emit InputsPublished(netlistHash2, T, inputRoot);
    }

    // ================================================================== §7.1 claim

    /// @notice Make a claim. `leaves` is the full checkpoint list L_0 … L_{n-1}; the game derives
    ///         checkpointRoot from it, so list and root cannot disagree. Case (a): `inputData` holds the packed
    ///         inputs of all T beats (T · ⌈nIn/8⌉ bytes) and the game computes inputRoot from it, which must
    ///         equal `inputRoot`. Case (b): `inputData` is empty and (netlistHash2, T, inputRoot) must have been
    ///         published by the input provider, which cannot claim.
    function claim(
        bytes32 netlistHash2,
        uint64 T,
        uint64 K,
        bytes32 inputRoot,
        bytes32[] calldata leaves,
        bytes calldata inputData
    ) external payable returns (uint256 id) {
        if (msg.value != bond) revert WrongBond();
        if (!store.largeStored(netlistHash2)) revert NotStored(netlistHash2); // §7.1 item 1
        if (T == 0 || K == 0) revert BadClaim();
        if (K > maxK || T > maxT) revert BoundsExceeded(); // §7.1: bounds on T and K
        uint256 n = (uint256(T) + K - 1) / K + 1; // times 0, K, 2K, … < T, and T
        if (n > MAX_CHECKPOINTS || leaves.length != n || leaves[0] != initialLeaf) revert BadClaim(); // items 2, 3
        id = _claims.length;
        Claim storage c = _claims.push();
        (c.fileLen, c.nIn, c.nOut,,) = store.largeInfo(netlistHash2);
        if (inputProvider == address(0)) {
            // case (a): inputRoot computed by the game from the published inputs
            if (TapLargeGameLib.inputRoot(T, c.nIn, inputData) != inputRoot) revert InputRootMismatch();
        } else {
            // case (b): a published triple, and the provider is never the claimant
            if (msg.sender == inputProvider) revert ProviderCannotClaim();
            if (inputData.length != 0) revert BadInputData();
            if (!inputsPublished[keccak256(abi.encode(netlistHash2, T, inputRoot))]) revert InputsNotPublished();
        }
        c.claimant = msg.sender;
        c.T = T;
        c.K = K;
        c.nCp = uint32(n); // n ≤ MAX_CHECKPOINTS
        (c.budget.gates, c.budget.extWords, c.budget.chunks) = verifier.segmentBudget();
        c.netlistHash2 = netlistHash2;
        c.hasMem = _memFlag(netlistHash2);
        c.inputRoot = inputRoot;
        c.checkpointRoot = TapLargeGameLib.checkpointRoot(leaves);
        c.status = Status.Open;
        c.windowEnd = uint64(block.timestamp) + challengeWindow;
        emit Claimed(id, msg.sender, netlistHash2, T, K, inputRoot, c.checkpointRoot, c.windowEnd);
    }

    /// @notice Accept claim `id`: only after its window has ended, with no dispute open and none won by a
    ///         challenger (a rejected claim is never accepted). Returns the claimant's bond. Anyone may call.
    function accept(uint256 id) external {
        Claim storage c = _claims[id];
        if (c.status != Status.Open) revert ClaimNotOpen();
        if (block.timestamp <= c.windowEnd) revert TooEarly();
        if (c.openDisputes != 0) revert DisputesOpen();
        c.status = Status.Accepted;
        credit[c.claimant] += bond;
        emit Accepted(id);
    }

    // ================================================================== §7.2 level 1

    /// @notice Challenge claim `id` at checkpoint index `x.index`, the first index whose leaf differs from the
    ///         challenger's own (see `ChallengeArgs`). Opens the dispute (id, msg.sender).
    function challenge(uint256 id, ChallengeArgs calldata x) external payable {
        Claim storage c = _claims[id];
        if (c.status != Status.Open) revert ClaimNotOpen();
        if (block.timestamp > c.windowEnd) revert TooLate();
        if (msg.value != bond) revert WrongBond();
        if (msg.sender == c.claimant) revert NotYourTurn();
        Dispute storage d = _disputes[id][msg.sender];
        if (d.phase != Phase.None) revert AlreadyChallenged();
        c.openDisputes += 1;
        d.phase = Phase.BeatCommit; // provisional; set again by _next or _resolve below
        uint256 i = x.index;
        emit Challenged(id, msg.sender, x.index);

        if (i == 0 || i >= c.nCp) return _resolve(id, msg.sender, Role.Claimant, Reason.IndexOutOfRange);
        uint64 tPrev = _cpTime(c, i - 1);
        bytes32 leafPrev = TapLargeHash.checkpointLeaf(tPrev, x.prevStateRoot, x.prevIo);
        if (
            !TapLargeHash.verify(c.checkpointRoot, c.nCp, i - 1, leafPrev, x.proofPrev)
                || !TapLargeHash.verify(c.checkpointRoot, c.nCp, i, x.leafCur, x.proofCur)
        ) return _resolve(id, msg.sender, Role.Claimant, Reason.BadCheckpointProof);
        uint64 tCur = _cpTime(c, i);
        bytes32 posted = TapLargeHash.checkpointLeaf(tCur, x.stateRoot, x.io);
        if (posted == x.leafCur) return _resolve(id, msg.sender, Role.Claimant, Reason.LeavesEqual);

        // disputed interval [t_{i-1}, t_i]: both stand by L_{i-1}; at t_i the claimant by L_i, the challenger by
        // its posted leaf
        d.base = tPrev;
        d.top = tCur;
        d.loLeaf = leafPrev;
        d.hiLeaf[0] = x.leafCur;
        d.hiLeaf[1] = posted;
        _next(c, d, Phase.BeatCommit, Role.Claimant);
    }

    // ================================================================== §7.3 level 2

    /// @notice Commit the beat leaves of [t_{i-1}, t_i] (tree size t_i − t_{i-1} + 1, fixed by the game), proving
    ///         that index 0 is L_{i-1} and the last index is the party's own leaf at t_i.
    function commitBeats(
        uint256 id,
        address challenger,
        bytes32 root,
        bytes32[] calldata proofLo,
        bytes32[] calldata proofHi
    ) external {
        Dispute storage d = _disputes[id][challenger];
        Role who = _enter(id, challenger, d, Phase.BeatCommit);
        uint256 n = d.top - d.base + 1;
        uint8 w = uint8(who);
        if (
            !TapLargeHash.verify(root, n, 0, d.loLeaf, proofLo)
                || !TapLargeHash.verify(root, n, n - 1, d.hiLeaf[w], proofHi)
        ) return _resolve(id, challenger, _other(who), Reason.BadEndpoints);
        d.roots[w] = root;
        if (who == Role.Claimant) return _next(_claims[id], d, Phase.BeatCommit, Role.Challenger);
        d.lo = d.base;
        d.hi = d.top;
        _afterBeatRound(_claims[id], d);
    }

    /// @notice Reveal the party's beat leaf at the beat the game picks, the midpoint ⌊(lo + hi)/2⌋, with its
    ///         preimage and a proof against the party's beat root. The game puts the time into the leaf itself.
    function revealBeat(uint256 id, address challenger, bytes32 stateRoot, bytes32 io, bytes32[] calldata proof)
        external
    {
        Dispute storage d = _disputes[id][challenger];
        Role who = _enter(id, challenger, d, Phase.BeatBisect);
        uint64 mid = (d.lo + d.hi) / 2;
        bytes32 leaf = TapLargeHash.checkpointLeaf(mid, stateRoot, io);
        if (!TapLargeHash.verify(d.roots[uint8(who)], d.top - d.base + 1, mid - d.base, leaf, proof)) {
            return _resolve(id, challenger, _other(who), Reason.BadProof);
        }
        if (who == Role.Claimant) {
            d.pending = leaf;
            return _next(_claims[id], d, Phase.BeatBisect, Role.Challenger);
        }
        if (leaf == d.pending) {
            d.lo = mid;
            d.loLeaf = leaf;
        } else {
            d.hi = mid;
            d.hiLeaf[0] = d.pending;
            d.hiLeaf[1] = leaf;
        }
        _afterBeatRound(_claims[id], d);
    }

    function _afterBeatRound(Claim storage c, Dispute storage d) internal {
        // agreed leaf at lo, different leaves at hi; done when they are adjacent beats
        _next(c, d, d.hi - d.lo > 1 ? Phase.BeatBisect : Phase.TupleCommit, Role.Claimant);
    }

    // ================================================================== §7.4 level 3

    /// @notice Commit the three tuple lists of the disputed beat t = lo and reveal their first and last tuples.
    ///         The claimant moves first and also reveals packedInputs_t with a proof against inputRoot (the
    ///         challenger passes empty values for both). Reveal checks 1–5 of §7.4 are applied to the mover.
    function commitTuples(
        uint256 id,
        address challenger,
        bytes calldata packedInputs,
        bytes32[] calldata inputProof,
        TupleLists calldata x
    ) external {
        Claim storage c = _claims[id];
        Dispute storage d = _disputes[id][challenger];
        Role who = _enter(id, challenger, d, Phase.TupleCommit);
        uint64 t = d.lo;
        // re-passed preimages of the agreed leaf at t and the party's own leaf at t + 1
        if (
            TapLargeHash.checkpointLeaf(t, x.stateRootT, x.ioT) != d.loLeaf
                || TapLargeHash.checkpointLeaf(t + 1, x.stateRootT1, x.ioT1) != d.hiLeaf[uint8(who)]
        ) revert BadPreimage();

        if (who == Role.Claimant) {
            if (!_inputsOk(c, t, packedInputs, inputProof)) {
                return _resolve(id, challenger, Role.Challenger, Reason.BadInputs);
            }
            // §7.4: the roots of the final call; a file with MEM opens stateRoot_t ≠ EMPTY
            bytes32 mr = ZERO_MEM;
            d.latchRootT = x.stateRootT;
            if (c.hasMem && x.stateRootT != EMPTY) {
                if (TapLargeHash.memStateRoot(x.latchRootT, x.memRootT) != x.stateRootT) {
                    return _resolve(id, challenger, Role.Challenger, Reason.BadOpening);
                }
                d.latchRootT = x.latchRootT;
                mr = x.memRootT;
            }
            d.memRootT = mr;
            bytes32 p0 = TapLargeGameLib.seg0P(packedInputs, c.nIn);
            d.seg0Leaf = TapLargeHash.segLeafOf(c.hasMem, 0, uint64(HEADER_LEN), 2 + c.nIn, 0, p0, EMPTY, mr);
        }
        Reason r = _revealChecks(c, d, x);
        if (r != Reason.None) return _resolve(id, challenger, _other(who), r);

        bytes32[3] memory lastHash =
            [keccak256(abi.encode(x.segLast)), keccak256(abi.encode(x.outLast)), keccak256(abi.encode(x.stateLast))];
        if (who == Role.Claimant) {
            d.clRoots = x.roots;
            d.clLengths = x.lengths;
            d.clLastHash = lastHash;
            d.segMIndex = x.lengths[0] - 1; // lengths[0] ≥ 1 after the reveal checks
            d.segMLeaf = _segLeaf(c, x.lengths[0] - 1, x.segLast);
            return _next(c, d, Phase.TupleCommit, Role.Challenger);
        }

        // both passed: pick the first list (segment, output, state) whose last tuples differ
        uint8 k;
        while (k < 3 && lastHash[k] == d.clLastHash[k]) ++k;
        if (k == 3) revert UnreachableEqualEnds(); // equal ends would mean equal leaves at t + 1
        d.kind = k;
        d.roots[0] = d.clRoots[k];
        d.roots[1] = x.roots[k];
        d.sizes[0] = d.clLengths[k];
        d.sizes[1] = x.lengths[k];
        uint32 h = d.sizes[0] < d.sizes[1] ? d.sizes[0] - 1 : d.sizes[1] - 1;
        d.ask = h;
        emit ListPicked(id, challenger, k, h);
        _next(c, d, Phase.TupleAtH, Role.Claimant);
    }

    /// @notice Reveal tuple `ask` of the picked segment list.
    function revealSeg(
        uint256 id,
        address challenger,
        TapLargeVerifier.SegTuple calldata s,
        bytes32[] calldata proof
    ) external {
        Dispute storage d = _disputes[id][challenger];
        if (d.kind != KIND_SEG) revert WrongListKind();
        _revealTuple(id, challenger, _segLeaf(_claims[id], d.ask, s), proof);
    }

    /// @notice Reveal tuple `ask` of the picked output or state list.
    function revealEnd(
        uint256 id,
        address challenger,
        TapLargeVerifier.EndTuple calldata e,
        bytes32[] calldata proof
    ) external {
        Dispute storage d = _disputes[id][challenger];
        uint8 k = d.kind;
        if (k == KIND_SEG) revert WrongListKind();
        _revealTuple(id, challenger, TapLargeHash.endLeaf(k, d.ask, e.ordinal, e.root), proof);
    }

    function _revealTuple(uint256 id, address challenger, bytes32 leaf, bytes32[] calldata proof) internal {
        Claim storage c = _claims[id];
        Dispute storage d = _disputes[id][challenger];
        Phase ph = d.phase;
        if (ph != Phase.TupleAtH && ph != Phase.TupleBisect && ph != Phase.TupleExtra) revert WrongPhase();
        Role who = _enter(id, challenger, d, ph);
        uint8 w = uint8(who);
        if (!TapLargeHash.verify(d.roots[w], d.sizes[w], d.ask, leaf, proof)) {
            return _resolve(id, challenger, _other(who), Reason.BadProof);
        }
        if (ph == Phase.TupleExtra) {
            // the longer list's party reveals tuple h + 1; the final call checks h → h + 1
            d.hiLeaf[w] = leaf;
            return _next(c, d, Phase.Prove, who);
        }
        if (who == Role.Claimant) {
            d.pending = leaf;
            return _next(c, d, ph, Role.Challenger);
        }
        uint64 ask = d.ask;
        if (ph == Phase.TupleAtH) {
            if (leaf == d.pending) {
                // equal at h = min(lastA, lastB): the longer list must justify h → h + 1 (it cannot)
                Role longer = d.sizes[0] > d.sizes[1] ? Role.Claimant : Role.Challenger;
                d.prover = longer;
                d.lo = ask;
                d.loLeaf = leaf;
                d.hi = ask + 1;
                d.ask = ask + 1;
                return _next(c, d, Phase.TupleExtra, longer);
            }
            // tuples differ at h: bisect over [0, h]; index 0 is agreed after the reveal checks
            d.lo = 0;
            d.loLeaf = _firstLeaf(d);
            d.hi = ask;
            d.hiLeaf[0] = d.pending;
        } else if (leaf == d.pending) {
            d.lo = ask;
            d.loLeaf = leaf;
        } else {
            d.hi = ask;
            d.hiLeaf[0] = d.pending;
        }
        if (d.hi - d.lo > 1) {
            d.ask = (d.lo + d.hi) / 2; // the game picks the midpoint (floor)
            return _next(c, d, Phase.TupleBisect, Role.Claimant);
        }
        d.prover = Role.Claimant; // the claimant's tuple hi is checked from the agreed tuple lo
        _next(c, d, Phase.Prove, Role.Claimant);
    }

    // ------------------------------------------------------------------ the final call

    /// @notice Final call for the segment list: verifySegment(netlistHash2, latchRoot_t, memRoot_t, a, b, witness,
    ///         budget), with the two roots recorded at the claimant's commitTuples.
    ///         `a` and `b` must be the agreed tuple at lo and the prover's tuple at hi (checked by their leaves).
    function proveSegment(
        uint256 id,
        address challenger,
        TapLargeVerifier.SegTuple calldata a,
        TapLargeVerifier.SegTuple calldata b,
        bytes calldata witness
    ) external {
        Claim storage c = _claims[id];
        Dispute storage d = _disputes[id][challenger];
        Role who = _enter(id, challenger, d, Phase.Prove);
        if (d.kind != KIND_SEG) revert WrongListKind();
        if (_segLeaf(c, d.lo, a) != d.loLeaf || _segLeaf(c, d.hi, b) != d.hiLeaf[uint8(who)]) revert BadPreimage();
        bool ok = verifier.verifySegment(c.netlistHash2, d.latchRootT, d.memRootT, a, b, witness, c.budget);
        emit VerifierCalled(id, challenger, KIND_SEG, d.hi, ok);
        _resolve(id, challenger, ok ? who : _other(who), ok ? Reason.VerifierTrue : Reason.VerifierFalse);
    }

    /// @notice Final call for the output or state list: verifyBeatEnd(netlistHash2, nOut, seg_m, a, b, witness).
    ///         seg_m is the agreed last segment tuple (checked against the claimant's last segment leaf). The
    ///         witness's kind byte must match the picked list (the verifier takes the kind from the witness).
    function proveBeatEnd(
        uint256 id,
        address challenger,
        TapLargeVerifier.SegTuple calldata segM,
        TapLargeVerifier.EndTuple calldata a,
        TapLargeVerifier.EndTuple calldata b,
        bytes calldata witness
    ) external {
        Claim storage c = _claims[id];
        Dispute storage d = _disputes[id][challenger];
        Role who = _enter(id, challenger, d, Phase.Prove);
        uint8 k = d.kind;
        if (k == KIND_SEG) revert WrongListKind();
        if (
            _segLeaf(c, d.segMIndex, segM) != d.segMLeaf || TapLargeHash.endLeaf(k, d.lo, a.ordinal, a.root) != d.loLeaf
                || TapLargeHash.endLeaf(k, d.hi, b.ordinal, b.root) != d.hiLeaf[uint8(who)]
        ) revert BadPreimage();
        if (witness.length == 0 || uint8(witness[0]) != k) revert WrongListKind();
        bool ok = verifier.verifyBeatEnd(c.netlistHash2, c.nOut, segM, a, b, witness);
        emit VerifierCalled(id, challenger, k, d.hi, ok);
        _resolve(id, challenger, ok ? who : _other(who), ok ? Reason.VerifierTrue : Reason.VerifierFalse);
    }

    // ================================================================== timeouts, voided disputes, payouts

    /// @notice After the deadline of dispute (id, challenger): the party to move loses. Anyone may call.
    function timeout(uint256 id, address challenger) external {
        Dispute storage d = _disputes[id][challenger];
        if (_claims[id].status != Status.Open) revert ClaimNotOpen();
        if (!_active(d.phase)) revert WrongPhase();
        if (block.timestamp <= d.deadline) revert TooEarly();
        _resolve(id, challenger, _other(d.turn), Reason.Timeout);
    }

    /// @notice Dispute (id, challenger) was still open when another challenger won and the claim was rejected:
    ///         it ends without a winner and the challenger's bond is returned. Anyone may call.
    function refund(uint256 id, address challenger) external {
        Claim storage c = _claims[id];
        Dispute storage d = _disputes[id][challenger];
        if (c.status != Status.Rejected) revert ClaimNotOpen();
        if (!_active(d.phase)) revert WrongPhase();
        d.phase = Phase.Voided;
        c.openDisputes -= 1;
        credit[challenger] += bond;
        emit DisputeVoided(id, challenger);
    }

    function withdraw() external {
        uint256 v = credit[msg.sender];
        credit[msg.sender] = 0;
        (bool ok,) = msg.sender.call{value: v}("");
        if (!ok) revert TransferFailed();
    }

    // ================================================================== views

    /// @notice §5.1: the budget of claims made now (the verifier's immutable budget; each claim keeps the value it
    ///         was made with in `claimOf(id).budget`).
    function segmentBudget() external view returns (uint32 gates, uint32 extWords, uint32 chunks) {
        return verifier.segmentBudget();
    }

    function claimOf(uint256 id) external view returns (Claim memory) {
        return _claims[id];
    }

    /// @notice (phase, turn, deadline, lo, hi, ask, kind) of dispute (id, challenger).
    function stateOf(uint256 id, address challenger)
        external
        view
        returns (Phase phase, Role turn, uint64 deadline, uint64 lo, uint64 hi, uint64 ask, uint8 kind)
    {
        Dispute storage d = _disputes[id][challenger];
        return (d.phase, d.turn, d.deadline, d.lo, d.hi, d.ask, d.kind);
    }

    /// @notice (winner address, winner role, reason) of dispute (id, challenger) once resolved.
    function outcomeOf(uint256 id, address challenger)
        external
        view
        returns (address winner, Role winnerRole, Reason reason)
    {
        Dispute storage d = _disputes[id][challenger];
        winnerRole = d.winner;
        reason = d.reason;
        winner = d.phase == Phase.Resolved ? _party(id, challenger, winnerRole) : address(0);
    }

    // ================================================================== internals

    function _active(Phase ph) internal pure returns (bool) {
        return ph != Phase.None && ph != Phase.Resolved && ph != Phase.Voided;
    }

    /// Checks claim status, phase, turn and deadline of a move; returns the mover's role. Moves of a dispute
    /// whose claim was rejected in another dispute revert (the dispute is voided through `refund`).
    function _enter(uint256 id, address challenger, Dispute storage d, Phase ph) internal view returns (Role who) {
        if (_claims[id].status != Status.Open) revert ClaimNotOpen();
        if (d.phase != ph) revert WrongPhase();
        who = d.turn;
        if (msg.sender != _party(id, challenger, who)) revert NotYourTurn();
        if (block.timestamp > d.deadline) revert TooLate();
    }

    /// Hands the move to `turn`. Every move gets `moveTime` seconds; a commitBeats move additionally
    /// `perBeatTime · K`, since the party recomputes up to K beats and builds the K+1-leaf beat tree.
    function _next(Claim storage c, Dispute storage d, Phase ph, Role turn) internal {
        d.phase = ph;
        d.turn = turn;
        uint256 span = moveTime;
        if (ph == Phase.BeatCommit) span += uint256(perBeatTime) * c.K; // ≤ 2^32 − 1 (constructor), K ≤ maxK
        d.deadline = uint64(block.timestamp + span);
    }

    /// Ends dispute (id, challenger). The claimant winning takes the challenger's bond and leaves the claim
    /// open; a challenger winning rejects the claim and takes both bonds. Only reachable while the claim is
    /// open (every caller checks), so the claimant's bond is paid out at most once.
    function _resolve(uint256 id, address challenger, Role winner, Reason reason) internal {
        Claim storage c = _claims[id];
        Dispute storage d = _disputes[id][challenger];
        d.phase = Phase.Resolved;
        d.winner = winner;
        d.reason = reason;
        c.openDisputes -= 1;
        address w;
        address l;
        if (winner == Role.Claimant) {
            (w, l) = (c.claimant, challenger);
            credit[w] += bond;
        } else {
            (w, l) = (challenger, c.claimant);
            c.status = Status.Rejected;
            c.rejectedBy = challenger;
            credit[w] += 2 * bond;
            emit ClaimRejected(id, challenger);
        }
        emit Resolved(id, challenger, w, l, reason);
    }

    function _party(uint256 id, address challenger, Role r) internal view returns (address) {
        return r == Role.Claimant ? _claims[id].claimant : challenger;
    }

    function _other(Role r) internal pure returns (Role) {
        return r == Role.Claimant ? Role.Challenger : Role.Claimant;
    }

    /// Checkpoint time t_k: k·K for k < n − 1, and T for the last one. The cast cannot truncate: for
    /// k ≤ n − 2 = ⌈T/K⌉ − 1 we have k·K < T ≤ 2^64 − 1.
    function _cpTime(Claim storage c, uint256 k) internal view returns (uint64) {
        return k + 1 == c.nCp ? c.T : uint64(k * c.K);
    }

    /// segLeaf of §4.5: M is hashed only for a file with MEM.
    function _segLeaf(Claim storage c, uint256 j, TapLargeVerifier.SegTuple calldata s)
        internal
        view
        returns (bytes32)
    {
        return TapLargeHash.segLeafOf(c.hasMem, j, s.byteOffset, s.firstSignal, s.latchOrdinal, s.p, s.d, s.m);
    }

    /// The MEM flag of a stored file: bit 0 of byte 7 of chunk 0 (§1.1, §7.1), read from largeChunk(nh2, 0)
    /// without copying the chunk.
    function _memFlag(bytes32 nh2) internal view returns (bool f) {
        address st = address(store);
        assembly ("memory-safe") {
            let q := mload(0x40)
            mstore(q, shl(224, 0x75077164)) // largeChunk(bytes32,uint32)
            mstore(add(q, 4), nh2)
            mstore(add(q, 36), 0)
            if iszero(staticcall(gas(), st, q, 68, 0, 0)) { revert(0, 0) }
            returndatacopy(q, 0, 32)
            let o := mload(q)
            returndatacopy(q, o, 32) // length (≥ 16: chunk 0 holds the header)
            if lt(mload(q), 16) { revert(0, 0) }
            returndatacopy(q, add(o, 32), 32)
            f := and(byte(7, mload(q)), 1)
        }
    }

    /// Leaf 0 of the picked list: the public seg_0, or (0, EMPTY).
    function _firstLeaf(Dispute storage d) internal view returns (bytes32) {
        uint8 k = d.kind;
        return k == KIND_SEG ? d.seg0Leaf : TapLargeHash.endLeaf(k, 0, 0, EMPTY);
    }

    /// §7.4 reveal checks 1–5 for the mover (first and last tuples of its three lists).
    function _revealChecks(Claim storage c, Dispute storage d, TupleLists calldata x) internal view returns (Reason) {
        uint32 nSeg = x.lengths[0];
        uint32 nOutL = x.lengths[1];
        uint32 nSt = x.lengths[2];
        if (nSeg == 0 || nOutL == 0 || nSt == 0) return Reason.BadFirstTuple;
        // checks 1 and 3: the first tuples are public values; their leaves must be at index 0
        if (
            !TapLargeHash.verify(x.roots[0], nSeg, 0, d.seg0Leaf, x.proofSeg0)
                || !TapLargeHash.verify(x.roots[1], nOutL, 0, TapLargeHash.endLeaf(KIND_OUT, 0, 0, EMPTY), x.proofOut0)
                || !TapLargeHash.verify(
                    x.roots[2], nSt, 0, TapLargeHash.endLeaf(KIND_STATE, 0, 0, EMPTY), x.proofState0
                )
        ) return Reason.BadFirstTuple;
        // the last tuples, against the party's own roots
        if (
            !TapLargeHash.verify(x.roots[0], nSeg, nSeg - 1, _segLeaf(c, nSeg - 1, x.segLast), x.proofSegLast)
                || !TapLargeHash.verify(
                    x.roots[1],
                    nOutL,
                    nOutL - 1,
                    TapLargeHash.endLeaf(KIND_OUT, nOutL - 1, x.outLast.ordinal, x.outLast.root),
                    x.proofOutLast
                )
                || !TapLargeHash.verify(
                    x.roots[2],
                    nSt,
                    nSt - 1,
                    TapLargeHash.endLeaf(KIND_STATE, nSt - 1, x.stateLast.ordinal, x.stateLast.root),
                    x.proofStateLast
                )
        ) return Reason.BadLastTuple;
        // check 2
        if (x.segLast.byteOffset != c.fileLen) return Reason.SegNotAtEof;
        // check 4: io_{t+1} = SHA-256(0x08 ‖ io_t ‖ root)
        if (x.outLast.ordinal != c.nOut || TapLargeHash.ioNext(x.ioT, x.outLast.root) != x.ioT1) {
            return Reason.OutputMismatch;
        }
        // check 5: stateRoot_{t+1} is the last state root, combined with the last M for a file with MEM
        bytes32 sr = c.hasMem ? TapLargeHash.memStateRoot(x.stateLast.root, x.segLast.m) : x.stateLast.root;
        if (x.stateLast.ordinal != x.segLast.latchOrdinal || sr != x.stateRootT1) return Reason.StateMismatch;
        return Reason.None;
    }

    /// packedInputs_t is ⌈nIn/8⌉ bytes with zero unused bits, and inputLeaf(t) is at index t of the tree of
    /// size T under inputRoot (§4.4, §7.4).
    function _inputsOk(Claim storage c, uint64 t, bytes calldata packed, bytes32[] calldata proof)
        internal
        view
        returns (bool)
    {
        uint256 nIn = c.nIn;
        if (packed.length != (nIn + 7) / 8) return false;
        if (nIn % 8 != 0 && uint8(packed[packed.length - 1]) >> (nIn % 8) != 0) return false;
        return TapLargeHash.verify(c.inputRoot, c.T, t, TapLargeHash.inputLeaf(t, packed), proof);
    }
}
