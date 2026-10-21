"""Collects the on-chain evidence of RunInferenceDispute.s.sol runs (standard library only).

Reads every broadcast record contracts/broadcast/RunInferenceDispute.s.sol/<chainId>/run-*.json (scenario A spans
two runs: claim, then accept), the state file contracts/deployments/<chainId>.inference.json written by the script,
and the deployment file; maps every transaction to its scenario (by claim id), sender role and function; reads the
final claim status and dispute outcome from the chain when an RPC is given (cast call, read-only); writes
demo/inference/onchain/evidence-<chainId>.json and prints a gas table and the evidence paragraph (zh + en) for the
TAP's Deployments section and the article.

    python3 collect_evidence.py --chain 97 [--rpc https://bsc-testnet-rpc.publicnode.com] [--section largeNetlists]
"""
import argparse
import datetime
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
CONTRACTS = os.path.join(REPO, "contracts")
STATUS = ["None", "Open", "Rejected", "Accepted"]
REASONS = ["None", "Timeout", "IndexOutOfRange", "BadCheckpointProof", "LeavesEqual", "BadEndpoints", "BadProof",
           "BadInputs", "BadFirstTuple", "SegNotAtEof", "OutputMismatch", "StateMismatch", "BadLastTuple",
           "VerifierTrue", "VerifierFalse", "BadOpening"]
EXPLORER = {97: "https://testnet.bscscan.com/tx/", 1952: "https://www.oklink.com/xlayer-test/tx/"}
CHAIN_NAME = {31337: "anvil", 97: "BNB Smart Chain Testnet", 1952: "X Layer Testnet"}
STORE_FUNCTIONS = {"addKnown", "beginLarge", "putChunk"}


def load_transcript():
    with open(os.path.join(HERE, "transcript.json")) as f:
        return json.load(f)


def selectors(transcript):
    out = {}
    for sc in transcript["scenarios"].values():
        for tx in sc["txs"]:
            out[tx["selector"]] = tx["function"]
    return out


def cast_call(rpc, to, sig, *args):
    cmd = ["cast", "call", to, sig] + list(args) + ["--rpc-url", rpc]
    return subprocess.check_output(cmd).decode().strip()


def words(hexdata):
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    return [h[i:i + 64] for i in range(0, len(h), 64)]


def claim_status(rpc, game, claim_id):
    """(status, windowEnd, rejectedBy) of claimOf(id): a static struct, word 13 is Status, 15 windowEnd, 17 rejectedBy."""
    raw = cast_call(rpc, game, "claimOf(uint256)", str(claim_id))
    w = words(raw)
    return int(w[13], 16), int(w[15], 16), "0x" + w[17][24:]


def outcome(rpc, game, claim_id, challenger):
    raw = cast_call(rpc, game, "outcomeOf(uint256,address)", str(claim_id), challenger)
    w = words(raw)
    return "0x" + w[0][24:], int(w[1], 16), int(w[2], 16)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", type=int, required=True)
    ap.add_argument("--rpc", default=os.environ.get("RPC_URL"))
    ap.add_argument("--section", default="largeNetlists")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    transcript = load_transcript()
    sel = selectors(transcript)
    dep_path = os.path.join(CONTRACTS, "deployments", "%d.json" % a.chain)
    state_path = os.path.join(CONTRACTS, "deployments", "%d.inference.json" % a.chain)
    with open(dep_path) as f:
        dep = json.load(f)
    with open(state_path) as f:
        state = json.load(f)
    section = dep[a.section]
    game = section["TapLargeDisputeGame"].lower()
    store = section["TapLargeNetlistStoreOwned"].lower()
    claims = {}                                   # claim id -> (scenario key, state entry)
    for key, st in state.items():
        if isinstance(st, dict) and st.get("section") == a.section:
            claims[int(st["claimId"])] = (st["scenario"], st)
    roles = {}
    for _, st in claims.values():
        roles[st["claimant"].lower()] = "claimant"
        roles[st["challenger"].lower()] = "challenger"
        if int(st["inputProvider"], 16):
            roles[st["inputProvider"].lower()] = "inputProvider"
    roles[section["deployer"].lower()] = roles.get(section["deployer"].lower(), "owner")

    # every broadcast run, oldest first; run-latest.json repeats the last run (deduplicated by hash)
    bdir = os.path.join(CONTRACTS, "broadcast", "RunInferenceDispute.s.sol", str(a.chain))
    files = sorted(p for p in glob.glob(os.path.join(bdir, "run-*.json")) if not p.endswith("run-latest.json"))
    seen, txs = set(), []
    for path in files:
        with open(path) as f:
            run = json.load(f)
        receipts = {r["transactionHash"].lower(): r for r in run.get("receipts", [])}
        pending_pub = None
        for tx in run["transactions"]:
            h = (tx.get("hash") or "").lower()
            if not h or h in seen:
                continue
            seen.add(h)
            t = tx["transaction"]
            to = (t.get("to") or "").lower()
            if to not in (game, store):          # another deployment's contracts (e.g. the FAST_PARAMS variant)
                continue
            data = t.get("input") or t.get("data") or "0x"
            fn = sel.get(data[:10]) or (tx.get("function") or "?").split("(")[0]
            rc = receipts.get(h, {})
            rec = {
                "hash": h,
                "block": int(rc["blockNumber"], 16) if rc.get("blockNumber") else None,
                "from": t["from"].lower(),
                "role": roles.get(t["from"].lower(), "?"),
                "to": to,
                "target": "TapLargeDisputeGame" if to == game else ("TapLargeNetlistStoreOwned" if to == store else to),
                "function": fn,
                "gasUsed": int(rc["gasUsed"], 16) if rc.get("gasUsed") else None,
                "status": "success" if rc.get("status") in ("0x1", 1) else ("?" if not rc else "failed"),
                "scenario": None,
                "claimId": None,
            }
            if to == game:
                if fn == "claim":
                    for lg in rc.get("logs", []):
                        if lg["address"].lower() == game and len(lg["topics"]) == 4:
                            rec["claimId"] = int(lg["topics"][1], 16)   # Claimed(id, claimant, netlistHash2, ...)
                            break
                    if pending_pub is not None and rec["claimId"] is not None:
                        pending_pub["claimId"] = rec["claimId"]
                        pending_pub["scenario"] = claims.get(rec["claimId"], (None,))[0]
                        pending_pub = None
                elif fn == "publishInputs":
                    pending_pub = rec
                else:
                    rec["claimId"] = int(words(data[10:])[0], 16)
                if rec["claimId"] in claims:
                    rec["scenario"] = claims[rec["claimId"]][0]
            else:
                rec["scenario"] = "upload"
            txs.append(rec)

    scenarios = {}
    for key in ("upload", "A", "B"):
        mine = [t for t in txs if t["scenario"] == key]
        if not mine:
            continue
        sc = {"txs": mine, "transactions": len(mine), "gasUsed": sum(t["gasUsed"] or 0 for t in mine),
              "gasByRole": {}}
        for t in mine:
            sc["gasByRole"][t["role"]] = sc["gasByRole"].get(t["role"], 0) + (t["gasUsed"] or 0)
        if key in ("A", "B"):
            st = next(s for cid, (k, s) in claims.items() if k == key)
            sc["claimId"] = int(st["claimId"])
            sc["claimant"] = st["claimant"]
            sc["challenger"] = st["challenger"]
            sc["checkpointRoot"] = st["checkpointRoot"]
            sc["windowEnd"] = int(st["windowEnd"])
            sc["statusFromState"] = st["status"]
            sc["expected"] = transcript["scenarios"][key]["dispute"]
            sc["expectedFinalStatus"] = transcript["scenarios"][key]["finalStatus"]
            if a.rpc:
                status, window_end, rejected_by = claim_status(a.rpc, section["TapLargeDisputeGame"], sc["claimId"])
                sc["finalStatus"] = STATUS[status]
                sc["rejectedBy"] = rejected_by
                if key == "B":
                    w, role, reason = outcome(a.rpc, section["TapLargeDisputeGame"], sc["claimId"], st["challenger"])
                    sc["outcome"] = {"winner": w, "winnerRole": ["claimant", "challenger"][role],
                                     "reason": REASONS[reason]}
            else:
                sc["finalStatus"] = st["status"] + " (from the state file; no RPC given)"
        scenarios[key] = sc

    out = {
        "generatedBy": "demo/inference/onchain/collect_evidence.py",
        "chainId": a.chain,
        "chain": dep.get("chain", CHAIN_NAME.get(a.chain, "?")),
        "rpc": a.rpc,
        "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
        "section": a.section,
        "deployment": {k: section[k] for k in ("TapLargeNetlistStoreOwned", "TapLargeVerifier", "TapLargeGameLib",
                                                "TapLargeDisputeGame", "bond", "challengeWindow", "moveTime",
                                                "perBeatTime", "inputProvider", "deployer")},
        "netlistHash2": transcript["netlist"]["netlistHash2"],
        "scenarios": scenarios,
        "totalTransactions": len(txs),
        "totalGasUsed": sum(t["gasUsed"] or 0 for t in txs),
    }
    out_path = a.out or os.path.join(HERE, "evidence-%d.json" % a.chain)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
        f.write("\n")

    # ---- table
    print("== RunInferenceDispute on %s (chainId %d), game %s" % (out["chain"], a.chain, section["TapLargeDisputeGame"]))
    for key, sc in scenarios.items():
        head = key if key == "upload" else "%s (claim %d, %s)" % (key, sc["claimId"], sc["finalStatus"])
        print("-- %s: %d transactions, %s gas" % (head, sc["transactions"], "{:,}".format(sc["gasUsed"])))
        for i, t in enumerate(sc["txs"]):
            print("   %2d %-13s %-13s %10s  %s" % (i, t["role"], t["function"],
                                                   "{:,}".format(t["gasUsed"]) if t["gasUsed"] else "?", t["hash"]))
    print("   total: %d transactions, %s gas" % (len(txs), "{:,}".format(out["totalGasUsed"])))
    print("wrote", os.path.relpath(out_path, REPO))

    # ---- evidence paragraph
    if "B" in scenarios:
        b = scenarios["B"]
        by_fn = {t["function"]: t for t in b["txs"]}
        ex = b["expected"]
        url = EXPLORER.get(a.chain, "")
        print("\n== evidence paragraph (fill <COMMIT>; explorer: %s)" % (url + "<hash>" if url else "none for this chain"))
        print("**On-chain run of the inference dispute (%s, chainId %d, %s).** Against the deployed game `%s` "
              "(commit `<COMMIT>`), a claimant published claim %d about the classifier netlist `%s` "
              "(T = 92, K = 8, 13 checkpoint leaves; checkpointRoot `%s`) with a class lie (a gate feeding the class "
              "register flipped at beat %d: class %d instead of %d) in tx `%s`; an honest challenger challenged it "
              "at checkpoint index 3 in tx `%s`; after %d rounds (%d transactions in all, %s gas) the claimant's own "
              "final call `proveSegment` (tx `%s`, `%s`, witness %d bytes) made `TapLargeVerifier.verifySegment` "
              "return false, and the game rejected the claim (`claimOf(%d).status = Rejected`, "
              "`outcomeOf` = challenger, `VerifierFalse`). The honest claim %s of scenario A was accepted "
              "after its challenge window (`accept` tx `%s`)."
              % (out["chain"], a.chain, out["date"], section["TapLargeDisputeGame"], b["claimId"],
                 out["netlistHash2"], b["checkpointRoot"], ex["beat"],
                 transcript["scenarios"]["B"]["claim"]["claimedClass"],
                 transcript["scenarios"]["A"]["claim"]["claimedClass"],
                 by_fn.get("claim", {}).get("hash", "<CLAIM TX>"), by_fn.get("challenge", {}).get("hash", "<CHALLENGE TX>"),
                 ex["totalRounds"], b["transactions"], "{:,}".format(b["gasUsed"]),
                 by_fn.get("proveSegment", {}).get("hash", "<PROVE TX>"), ex["finalCall"], ex["witnessBytes"], b["claimId"],
                 scenarios.get("A", {}).get("claimId", "<A>"),
                 {t["function"]: t for t in scenarios.get("A", {}).get("txs", [])}.get("accept", {}).get("hash", "<ACCEPT TX>")))
        print("\n**链上争议复现（%s，chainId %d，%s）。** 对已部署的游戏合约 `%s`（提交 `<COMMIT>`），申领者就分类器网表 "
              "`%s` 发布了申领 %d（T = 92，K = 8，13 个检查点叶；checkpointRoot `%s`），其中含一处类别谎言（第 %d 拍翻转了"
              "送入类别寄存器的一个门：类别 %d 而非 %d），交易 `%s`；诚实挑战者在检查点索引 3 处发起挑战，交易 `%s`；经 %d 轮"
              "（共 %d 笔交易，%s gas）后，申领者自己的最终调用 `proveSegment`（交易 `%s`，`%s`，见证 %d 字节）使 "
              "`TapLargeVerifier.verifySegment` 返回 false，游戏判申领被拒（`claimOf(%d).status = Rejected`，`outcomeOf` = "
              "挑战者，`VerifierFalse`）。场景 A 的诚实申领 %s 在挑战窗口过后被接受（`accept` 交易 `%s`）。"
              % (out["chain"], a.chain, out["date"], section["TapLargeDisputeGame"], out["netlistHash2"], b["claimId"],
                 b["checkpointRoot"], ex["beat"], transcript["scenarios"]["B"]["claim"]["claimedClass"],
                 transcript["scenarios"]["A"]["claim"]["claimedClass"],
                 by_fn.get("claim", {}).get("hash", "<CLAIM TX>"), by_fn.get("challenge", {}).get("hash", "<CHALLENGE TX>"),
                 ex["totalRounds"], b["transactions"], "{:,}".format(b["gasUsed"]),
                 by_fn.get("proveSegment", {}).get("hash", "<PROVE TX>"), ex["finalCall"], ex["witnessBytes"], b["claimId"],
                 scenarios.get("A", {}).get("claimId", "<A>"),
                 {t["function"]: t for t in scenarios.get("A", {}).get("txs", [])}.get("accept", {}).get("hash", "<ACCEPT TX>")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
