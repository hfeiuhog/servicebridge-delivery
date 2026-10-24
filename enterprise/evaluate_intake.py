"""Reproducible rule/live-model comparison. Human time is never fabricated."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from lfx.components.enterprise_delivery.candidate_contracts import (
    TicketCandidate,
    missing_information,
    validate_candidate,
)
from lfx.components.enterprise_delivery.candidate_model import extract_candidate_sync
from lfx.components.enterprise_delivery.deepseek_model import DeepSeekModel
from lfx.components.enterprise_delivery.intake_rules import extract_with_rules


def run(args):
    cases_path = args.dataset
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines()]
    cases = [row for row in cases if args.split == "all" or row["split"] == args.split]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        raise ValueError("No cases selected")
    key = sys.stdin.readline().strip() if args.api_key_stdin else os.environ.get("DEEPSEEK_API_KEY", "")
    if args.mode == "deepseek" and not args.allow_external_model:
        raise ValueError("Live calls require --allow-external-model")
    model = DeepSeekModel(key, args.model) if args.mode == "deepseek" else None
    output = args.output
    output.mkdir(parents=True, exist_ok=True)

    def one(case):
        result = (
            extract_candidate_sync(case["source"], model, model_label=args.model)
            if model
            else extract_with_rules(case["source"])
        )
        candidate = TicketCandidate.model_validate(result["candidate"])
        valid = result["model_run"].get("error") is None
        if valid:
            validate_candidate(result["source"], candidate)
        facts = candidate.facts.model_dump()
        presence = sum((facts[name] is None) == (expected is None) for name, expected in case["expected_facts"].items())
        anchor_hits, anchor_total = 0, 0
        for field, tokens in case["anchors"].items():
            if tokens:
                anchor_total += 1
                anchor_hits += int(all(token in (facts[field] or "") for token in tokens))
        row = {
            "id": case["id"],
            "split": case["split"],
            "category": case["category"],
            "contract_pass": valid,
            "presence_matches": presence,
            "presence_total": 5,
            "missing_set_match": valid and missing_information(candidate) == case["expected_missing"],
            "anchor_hits": anchor_hits,
            "anchor_total": anchor_total,
            "candidate": candidate.model_dump(),
            "model_run": result["model_run"],
        }
        with (output / "predictions.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps({"completed": case["id"], "contract_pass": valid}), flush=True)
        return row

    predictions = output / "predictions.jsonl"
    if predictions.exists():
        raise ValueError("Use a new output directory; existing predictions are never overwritten")
    rows = [one(case) for case in cases]
    summary = {
        "mode": args.mode,
        "model": args.model if model else "narrow-rules-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "split": args.split,
        "cases": len(rows),
        "dataset_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "contract_pass": sum(row["contract_pass"] for row in rows),
        "missing_set_matches": sum(row["missing_set_match"] for row in rows),
        "fact_presence_matches": sum(row["presence_matches"] for row in rows),
        "fact_presence_total": 5 * len(rows),
        "anchor_hits": sum(row["anchor_hits"] for row in rows),
        "anchor_total": sum(row["anchor_total"] for row in rows),
        "latency_ms_median": round(statistics.median(row["model_run"]["elapsed_ms"] for row in rows), 3),
        "latency_ms_max": max(row["model_run"]["elapsed_ms"] for row in rows),
        "total_tokens": sum(row["model_run"].get("usage", {}).get("total_tokens", 0) for row in rows),
        "human_time_measured": False,
        "limitations": [
            "Synthetic inputs only",
            "Anchors and presence are automated proxies, not semantic accuracy",
            "No real ServiceNow calls or customer ROI measurement",
            "Model alias can change",
        ],
    }
    root = Path(__file__).resolve().parents[1]
    summary["implementation_sha256"] = {
        name: hashlib.sha256((root / "src/lfx/src/lfx/components/enterprise_delivery" / name).read_bytes()).hexdigest()
        for name in ("candidate_contracts.py", "candidate_model.py", "deepseek_model.py", "intake_rules.py")
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["rules", "deepseek"], required=True)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).parent / "evaluation" / "intake-cases.jsonl")
    parser.add_argument("--split", choices=["all", "development", "holdout"], default="all")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--allow-external-model", action="store_true")
    parser.add_argument("--api-key-stdin", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
