// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {TapLargeNetlistStoreOwned} from "../src/TapLargeNetlistStore.sol";
import {TapLargeVerifier} from "../src/TapLargeVerifier.sol";
import {Upload} from "./utils/Upload.sol";
import {Cases} from "./utils/Cases.sol";

/// Differential test against the Python reference: test/data/diff_cases.bin (gen_diff.py) holds random valid
/// files, random beats and budgets, honest witnesses and random mutations, each with the Python verdict.
/// A fuzz test then mutates single bytes of the honest witnesses, which must always be rejected (except the
/// state tree size field, which the draft deliberately leaves unbound; see its Rationale).
contract DifferentialTest is Test {
    TapLargeNetlistStoreOwned internal store;
    TapLargeVerifier internal verifier;
    bytes32[] internal nh2s;
    Cases.Case[] internal honest;

    function setUp() public {
        (Cases.Reader memory r, bytes[] memory files) = Cases.open(vm.readFileBinary("test/data/diff_cases.bin"));
        store = new TapLargeNetlistStoreOwned(address(this));
        for (uint256 i; i < files.length; ++i) nh2s.push(Upload.upload(store, files[i], true, 0));
        verifier = new TapLargeVerifier(store, 6000, 256, 16);
        uint256 n = Cases.uintN(r, 4);
        for (uint256 i; i < n; ++i) {
            Cases.Case memory c = Cases.next(r);
            bytes memory nm = bytes(c.name);
            if (nm.length >= 6 && nm[0] == "h" && nm[1] == "o" && nm[2] == "n") honest.push(c);
        }
    }

    function _run(Cases.Case memory c) internal view returns (bool) {
        if (c.kind == 0) {
            return verifier.verifySegment(nh2s[c.fileIdx], c.latchRoot, c.memRoot, c.a, c.b, c.witness, c.budget);
        }
        return verifier.verifyBeatEnd(nh2s[c.fileIdx], c.nOut, c.a, c.ea, c.eb, c.witness);
    }

    function test_differentialCorpus() public view {
        (Cases.Reader memory r,) = Cases.open(vm.readFileBinary("test/data/diff_cases.bin"));
        uint256 n = Cases.uintN(r, 4);
        uint256 agreeTrue;
        uint256 agreeFalse;
        for (uint256 i; i < n; ++i) {
            Cases.Case memory c = Cases.next(r);
            bool got = _run(c);
            assertEq(got, c.expect == 1, string.concat("case ", vm.toString(i), ": ", c.name));
            if (got) ++agreeTrue;
            else ++agreeFalse;
        }
        assertGt(agreeTrue, 300);
        assertGt(agreeFalse, 1000);
    }

    /// Offset of the u32 state tree size in a segment witness (§6.3).
    function _sSizeOffset(bytes memory w) internal pure returns (uint256 p) {
        p = 1 + 32 * uint8(w[0]);
        p += uint8(w[p]) == 1 ? 33 : 1;
        p += 2 + 36 * (uint256(uint8(w[p])) << 8 | uint8(w[p + 1]));
        p += 2 + 32 * (uint256(uint8(w[p])) << 8 | uint8(w[p + 1]));
        p += 1 + 32 * uint8(w[p]);
        p += uint8(w[p]) == 1 ? 33 : 1;
    }

    function testFuzz_singleByteMutationRejected(uint256 which, uint256 pos, uint8 x) public view {
        Cases.Case memory c = honest[bound(which, 0, honest.length - 1)];
        assertTrue(_run(c), "honest case must verify");
        pos = bound(pos, 0, c.witness.length - 1);
        if (c.kind == 0) {
            uint256 s = _sSizeOffset(c.witness);
            vm.assume(pos < s || pos >= s + 4);
        }
        vm.assume(x != 0);
        c.witness[pos] = bytes1(uint8(c.witness[pos]) ^ x);
        assertFalse(_run(c), "mutated witness must be rejected");
    }

    function testFuzz_truncationAndTrailingRejected(uint256 which, uint256 cut, bool trailing) public view {
        Cases.Case memory c = honest[bound(which, 0, honest.length - 1)];
        bytes memory w = c.witness;
        if (trailing) {
            c.witness = bytes.concat(w, bytes1(uint8(cut)));
        } else {
            cut = bound(cut, 1, w.length);
            bytes memory t = new bytes(w.length - cut);
            for (uint256 i; i < t.length; ++i) t[i] = w[i];
            c.witness = t;
        }
        assertFalse(_run(c));
    }
}
