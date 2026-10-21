#!/usr/bin/env bash
# One-command testnet deployment of the TAP-7007 reference contracts (DEPLOY.md §5).
# Needs PRIVATE_KEY (a TEST key) and RPC_URL in the environment; never prints the key.
#
#   export PRIVATE_KEY=…  RPC_URL=…
#   contracts/script/deploy-testnet.sh            # DRY=1 simulates without --broadcast; UPLOAD_LARGE=true adds the 6-chunk example
set -euo pipefail
: "${PRIVATE_KEY:?set PRIVATE_KEY (a test key) in the environment}"
: "${RPC_URL:?set RPC_URL (see DEPLOY.md §2)}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BROADCAST="--broadcast --slow"
[ "${DRY:-0}" = "1" ] && BROADCAST=""
EXTRA="${FORGE_SCRIPT_FLAGS:-}"   # e.g. "--legacy"

CHAIN=$(cast chain-id --rpc-url "$RPC_URL")
echo "chain id $CHAIN, deployer $(cast wallet address --private-key "$PRIVATE_KEY")"
case "$CHAIN" in
  1|56|196|8453) echo "refusing a mainnet chain id ($CHAIN); this package is for testnets" >&2; exit 1 ;;
esac

cd "$ROOT/contracts"
[ -e lib/forge-std ] || { echo "install forge-std first (contracts/lib/README.md)" >&2; exit 1; }
mkdir -p deployments
forge script script/DeployLargeNetlists.s.sol:DeployLargeNetlists --rpc-url "$RPC_URL" $BROADCAST $EXTRA

echo
echo "Done. Send back (DEPLOY.md §7):"
echo "  $ROOT/contracts/deployments/$CHAIN.json"
echo "  $ROOT/contracts/broadcast/DeployLargeNetlists.s.sol/$CHAIN/run-latest.json"
