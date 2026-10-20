// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {Test, console} from "forge-std/Test.sol";
import {TapLargeNetlistStoreOwned} from "../src/TapLargeNetlistStore.sol";
import {TapLargeVerifier} from "../src/TapLargeVerifier.sol";
import {Upload} from "./utils/Upload.sol";
import {Cases} from "./utils/Cases.sol";
import {VerifierHarness} from "./Vectors.t.sol";

/// Measures a call from a small, fresh frame (the test contract's own memory is tens of MB after loading the
/// files, which distorts gasleft() deltas taken there).
contract GasMeter {
    function measure(address target, bytes calldata data) external view returns (uint256 used, bool ok, bytes memory ret) {
        uint256 g0 = gasleft();
        (ok, ret) = target.staticcall(data);
        used = g0 - gasleft();
    }
}

/// Gas of verifySegment / verifyBeatEnd on the synthetic cases of test/data/gas_cases.bin (gen_gas.py).
/// Each call is measured from a fresh small frame (GasMeter) as the gas the STATICCALL to the verifier costs its
/// caller. In Foundry 1.8.3 every top-level call from the test is its own transaction, so the store, the verifier
/// and the chunk data contracts are cold and the precompiles warm (the vm.cool calls are kept but redundant). Calldata gas is computed from the ABI calldata of
/// the call at 16 gas per nonzero and 4 per zero byte. Writes <cases file>.csv.
contract GasVerifyTest is Test {
    GasMeter internal meter;
    TapLargeNetlistStoreOwned internal store;
    TapLargeVerifier internal verifier;
    VerifierHarness internal harness;
    bytes32[] internal nh2s;
    uint256[] internal nchunks;

    function setUp() public {
        (, bytes[] memory files) = Cases.open(vm.readFileBinary(_path()));
        store = new TapLargeNetlistStoreOwned(address(this));
        for (uint256 i; i < files.length; ++i) {
            nh2s.push(Upload.upload(store, files[i], true, 0));
            nchunks.push(Upload.nChunks(files[i].length));
        }
        verifier = new TapLargeVerifier(store, 6000, 256, 16);
        harness = new VerifierHarness(store);
        meter = new GasMeter();
    }

    function _path() internal view returns (string memory) {
        return vm.envOr("GAS_CASES", string("test/data/gas_cases.bin"));
    }

    function _calldata(Cases.Case memory c) internal view returns (bytes memory) {
        if (c.kind == 0) {
            return abi.encodeCall(
                TapLargeVerifier.verifySegment, (nh2s[c.fileIdx], c.latchRoot, c.memRoot, c.a, c.b, c.witness, c.budget)
            );
        }
        return abi.encodeCall(TapLargeVerifier.verifyBeatEnd, (nh2s[c.fileIdx], c.nOut, c.a, c.ea, c.eb, c.witness));
    }

    function _cool(uint256 fileIdx) internal {
        bytes32 h = nh2s[fileIdx];
        for (uint256 i; i < nchunks[fileIdx]; ++i) vm.cool(store.largeChunkPointer(h, uint32(i)));
        vm.cool(address(store));
        vm.cool(address(verifier));
    }

    function _cdGas(bytes memory cd) internal pure returns (uint256 g, uint256 floorTokens) {
        for (uint256 i; i < cd.length; ++i) {
            if (cd[i] == 0) {
                g += 4;
                floorTokens += 1;
            } else {
                g += 16;
                floorTokens += 4;
            }
        }
    }

    function test_gasTable() public {
        (Cases.Reader memory r,) = Cases.open(vm.readFileBinary(_path()));
        uint256 n = Cases.uintN(r, 4);
        string memory csv = "case,execGas,calldataBytes,calldataGas,txEstimate,floor7623,pctOf16777216,pctOf30M\n";
        for (uint256 i; i < n; ++i) {
            Cases.Case memory c = Cases.next(r);
            bytes memory cd = _calldata(c);
            if (c.kind == 0) {
                (bool okH, uint256 reads) =
                    harness.verifySegmentReads(nh2s[c.fileIdx], c.latchRoot, c.memRoot, c.a, c.b, c.witness, c.budget);
                assertTrue(okH, c.name);
                assertEq(reads, c.info, string.concat("chunks read differ from Python: ", c.name));
            }
            _cool(c.fileIdx);
            (uint256 used, bool s, bytes memory ret) = meter.measure(address(verifier), cd);
            (uint256 warm,,) = meter.measure(address(verifier), cd);
            assertTrue(s && abi.decode(ret, (bool)), c.name);
            (uint256 cdGas, uint256 tokens) = _cdGas(cd);
            uint256 txGas = 21000 + cdGas + used;
            uint256 floorGas = 21000 + 10 * tokens;
            console.log(c.name);
            console.log("    exec gas %d (warm %d), calldata %d bytes", used, warm, cd.length);
            console.log("    tx estimate %d (%d per mille of 16,777,216)", txGas, txGas * 1000 / 16777216);
            csv = string.concat(
                csv,
                '"',
                c.name,
                '",',
                vm.toString(used),
                ",",
                vm.toString(cd.length),
                ",",
                vm.toString(cdGas),
                ",",
                vm.toString(txGas),
                ",",
                vm.toString(floorGas),
                ",",
                vm.toString(txGas * 10000 / 16777216),
                ",",
                vm.toString(txGas * 10000 / 30000000),
                "\n"
            );
        }
        vm.writeFile(string.concat(_path(), ".csv"), csv);
    }
}
