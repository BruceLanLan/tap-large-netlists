#!/usr/bin/env bash
# One command: replay the inference demo's dispute against the DEPLOYED TAP-7007 contracts of a testnet
# (demo/inference/onchain/README.md). Needs CLAIMANT_KEY and CHALLENGER_KEY (two different TEST keys) and RPC_URL;
# never prints a key. Then collects the transaction hashes and gas into demo/inference/onchain/evidence-<chainId>.json.
#
#   export CLAIMANT_KEY=…  CHALLENGER_KEY=…  RPC_URL=…
#   contracts/script/run-inference-dispute.sh                 # SCENARIO=B (default): the class lie is rejected
#   SCENARIO=A contracts/script/run-inference-dispute.sh      # honest claim; then, after the window:
#   SCENARIO=A STEP=accept contracts/script/run-inference-dispute.sh
# Optional: DEPLOYMENT_SECTION=largeNetlistsFast (a FAST_PARAMS deployment), OWNER_KEY, PROVIDER_KEY, CLAIM_ID,
# START_MOVE, DRY=1 (simulate only), FORGE_SCRIPT_FLAGS (e.g. "--resume" or "--legacy").
set -euo pipefail
: "${CLAIMANT_KEY:?set CLAIMANT_KEY (a test key)}"
: "${CHALLENGER_KEY:?set CHALLENGER_KEY (a second, different test key)}"
: "${RPC_URL:?set RPC_URL (DEPLOY.md §2)}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BROADCAST="--broadcast --slow"
[ "${DRY:-0}" = "1" ] && BROADCAST=""
EXTRA="${FORGE_SCRIPT_FLAGS:-}"
SECTION="${DEPLOYMENT_SECTION:-largeNetlists}"

CHAIN=$(cast chain-id --rpc-url "$RPC_URL")
case "$CHAIN" in
  1|56|196|8453) echo "refusing a mainnet chain id ($CHAIN); this package is for testnets" >&2; exit 1 ;;
esac
echo "chain id $CHAIN, claimant $(cast wallet address --private-key "$CLAIMANT_KEY"), challenger $(cast wallet address --private-key "$CHALLENGER_KEY"), scenario ${SCENARIO:-B}, step ${STEP:-all}, deployment $SECTION"

cd "$ROOT/contracts"
[ -e lib/forge-std ] || { echo "install forge-std first (contracts/lib/README.md)" >&2; exit 1; }
[ -e "deployments/$CHAIN.json" ] || { echo "no deployments/$CHAIN.json: deploy first (DEPLOY.md)" >&2; exit 1; }
forge script script/RunInferenceDispute.s.sol:RunInferenceDispute --rpc-url "$RPC_URL" $BROADCAST $EXTRA -vv

if [ -n "$BROADCAST" ]; then
  echo
  python3 "$ROOT/demo/inference/onchain/collect_evidence.py" --chain "$CHAIN" --rpc "$RPC_URL" --section "$SECTION"
  echo
  echo "Send back (demo/inference/onchain/README.md):"
  echo "  $ROOT/demo/inference/onchain/evidence-$CHAIN.json"
  echo "  $ROOT/contracts/deployments/$CHAIN.inference.json"
  echo "  $ROOT/contracts/broadcast/RunInferenceDispute.s.sol/$CHAIN/run-*.json"
fi
