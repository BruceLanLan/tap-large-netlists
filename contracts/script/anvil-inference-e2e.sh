#!/usr/bin/env bash
# End-to-end test of the on-chain inference runner against a local anvil node (no public chain, no real key):
# deploys with DeployLargeNetlists (default parameters, then the FAST_PARAMS variant), runs RunInferenceDispute
# for scenario B (the class lie must be Rejected) and scenario A (the honest claim must be Accepted after the
# window, which anvil's clock is moved past), and asserts the outcomes read back from the chain. The keys are
# anvil's public default accounts 0 and 1 (published constants; they hold nothing anywhere else).
#   ./script/anvil-inference-e2e.sh      # from contracts/ (lib/forge-std installed, see lib/README.md)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(cd .. && pwd)"
PORT="${ANVIL_PORT:-8545}"
export RPC_URL="http://127.0.0.1:${PORT}"
export PRIVATE_KEY="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"   # anvil account 0
export CLAIMANT_KEY="$PRIVATE_KEY"                                                         # deployer = store owner = claimant
export CHALLENGER_KEY="0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"  # anvil account 1
rm -f deployments/31337.json deployments/31337.inference.json "$ROOT/demo/inference/onchain/evidence-31337.json"
rm -rf broadcast/RunInferenceDispute.s.sol/31337
mkdir -p deployments
anvil --port "$PORT" --silent --chain-id 31337 &
ANVIL_PID=$!
trap 'kill $ANVIL_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do cast chain-id --rpc-url "$RPC_URL" >/dev/null 2>&1 && break; sleep 0.2; done

FILTER='^\s*$|Compiling|Solc|Compiler run|warning|─|│|╭|╰|help:|lint|foundry-pp|Detected artifacts|No files changed|Estimated|Sensitive|Transactions saved|Setting up|=====|ONCHAIN EXECUTION|Waiting for receipts|Sequence #|Simulated On-chain|SIMULATION COMPLETE|If you wish|Paid:|Total Paid|Chain 31337|Script ran|Run with|Enter'
run() {  # run <label> <forge args…>
  echo; echo "### $1"; shift
  forge script "$@" --rpc-url "$RPC_URL" --broadcast --slow -vv 2>&1 | grep -vE "$FILTER" || true
}

run "deploy (default parameters)" script/DeployLargeNetlists.s.sol:DeployLargeNetlists
FAST_PARAMS=true run "deploy (FAST_PARAMS: challenge window 10 minutes, section largeNetlistsFast)" script/DeployLargeNetlists.s.sol:DeployLargeNetlists

# scenario B on the default deployment: upload (8 txs), claim, 16 dispute moves; the claim must be Rejected
SCENARIO=B run "scenario B: class lie, honest challenger" script/RunInferenceDispute.s.sol:RunInferenceDispute
# scenario A on the default deployment: claim now, accept after the 1-day window (anvil's clock moved forward)
SCENARIO=A run "scenario A: honest claim (claim)" script/RunInferenceDispute.s.sol:RunInferenceDispute
cast rpc evm_increaseTime 86401 --rpc-url "$RPC_URL" >/dev/null && cast rpc evm_mine --rpc-url "$RPC_URL" >/dev/null
SCENARIO=A STEP=accept run "scenario A: honest claim (accept after the window)" script/RunInferenceDispute.s.sol:RunInferenceDispute
# scenario A on the FAST_PARAMS deployment (another store: the netlist is uploaded again), window 10 minutes
DEPLOYMENT_SECTION=largeNetlistsFast SCENARIO=A run "scenario A on largeNetlistsFast (claim)" script/RunInferenceDispute.s.sol:RunInferenceDispute
cast rpc evm_increaseTime 601 --rpc-url "$RPC_URL" >/dev/null && cast rpc evm_mine --rpc-url "$RPC_URL" >/dev/null
DEPLOYMENT_SECTION=largeNetlistsFast SCENARIO=A STEP=accept run "scenario A on largeNetlistsFast (accept after 10 minutes)" script/RunInferenceDispute.s.sol:RunInferenceDispute

echo
python3 "$ROOT/demo/inference/onchain/collect_evidence.py" --chain 31337 --rpc "$RPC_URL" --section largeNetlists
python3 "$ROOT/demo/inference/onchain/collect_evidence.py" --chain 31337 --rpc "$RPC_URL" --section largeNetlistsFast \
  --out "$ROOT/demo/inference/onchain/evidence-31337-fast.json" | head -12

python3 - "$ROOT/demo/inference/onchain/evidence-31337.json" "$ROOT/demo/inference/onchain/evidence-31337-fast.json" <<'PY'
import json, sys
ev = json.load(open(sys.argv[1])); fast = json.load(open(sys.argv[2]))
b, a, af = ev["scenarios"]["B"], ev["scenarios"]["A"], fast["scenarios"]["A"]
assert b["finalStatus"] == "Rejected", b["finalStatus"]
assert b["outcome"]["winnerRole"] == "challenger" and b["outcome"]["reason"] == "VerifierFalse", b["outcome"]
assert b["rejectedBy"].lower() == b["challenger"].lower()
assert b["transactions"] == 17 and all(t["status"] == "success" for t in b["txs"]), b["transactions"]
assert [t["function"] for t in b["txs"]][-1] == "proveSegment" and b["txs"][-1]["role"] == "claimant"
assert a["finalStatus"] == "Accepted" and a["transactions"] == 2, (a["finalStatus"], a["transactions"])
assert af["finalStatus"] == "Accepted" and af["transactions"] == 2, (af["finalStatus"], af["transactions"])
up = ev["scenarios"]["upload"]
assert up["transactions"] == 8 and [t["function"] for t in up["txs"]] == ["addKnown", "beginLarge"] + ["putChunk"] * 6
print("anvil-inference-e2e: OK — B rejected by the challenger (VerifierFalse, %d txs, %s gas); A accepted (%d txs); "
      "A on FAST_PARAMS accepted after 10 minutes; upload %d txs, %s gas"
      % (b["transactions"], "{:,}".format(b["gasUsed"]), a["transactions"], up["transactions"], "{:,}".format(up["gasUsed"])))
PY
rm -f "$ROOT/demo/inference/onchain/evidence-31337-fast.json"
