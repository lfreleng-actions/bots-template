# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Template placeholder: gather every target's result into one report and the ledger.

The report job runs this from the trusted assets checkout once the
apply matrix has finished. It reads the selection evidence, every
``result.json`` the apply entries uploaded, and the prior ledger, and
writes ``report.md`` for the step summary, ``report.json`` for the
run artifact, and the next run's ``ledger.json`` through ``ledger``.

A selected target with no result is reported as ``missing`` and not
recorded, so the next run looks at it again. Extend ``row_for`` and
``describe`` with the columns the bot's results carry; the shape of
the ledger entries stays as ``ledger.py`` defines it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

import ledger
from bot_evidence import MAX_SUMMARY_BYTES, read_regular

SCHEMA = 1
MAX_RESULT_BYTES = 1024 * 1024
# The verdicts that make a target a skip next run; a bot names its own.
RECORDED_VERDICTS = frozenset({"done", "would-do", "needs-human"})


def load_object(path: Path, limit: int) -> dict[str, Any]:
    """Read one bounded JSON object."""
    parsed: Any = json.loads(read_regular(path, limit))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} is not an object")
    return cast("dict[str, Any]", parsed)


def gather_results(directory: Path) -> dict[str, dict[str, Any]]:
    """Every ``result.json`` under the results directory, by target key.

    One artifact per key and attempt is downloaded into its own
    folder; the highest run attempt wins when a retry uploaded twice.
    """
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*/result.json")):
        result = load_object(path, MAX_RESULT_BYTES)
        key = result.get("key")
        if not isinstance(key, str) or result.get("schema") != SCHEMA:
            continue
        held = found.get(key)
        if held is None or int(held.get("run_attempt") or 0) <= int(
            result.get("run_attempt") or 0
        ):
            found[key] = result
    return found


def row_for(target: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    """One report row: the target and what became of it."""
    verdict = str(result["verdict"]) if result else "missing"
    reasons = list(cast("list[Any]", result.get("reasons", []))) if result else []
    return {
        "key": target["key"],
        "repository": target["repository"],
        "number": target["number"],
        "head_sha": target["head_sha"],
        "verdict": verdict,
        "reasons": [str(reason) for reason in reasons],
        "write_url": result.get("write_url") if result else None,
    }


def describe(rows: list[dict[str, Any]], dry_run: bool) -> str:
    """The markdown step summary."""
    mode = "dry run" if dry_run else "live"
    lines = [f"## Run report ({mode})", "", f"{len(rows)} target(s).", ""]
    if rows:
        lines += ["| Target | Verdict | Notes |", "| --- | --- | --- |"]
    for row in rows:
        notes = "; ".join(row["reasons"]).replace("|", "\\|")
        lines.append(
            f"| `{row['repository']}#{row['number']}` | {row['verdict']} | {notes} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    """Write the report files and the next ledger."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-ledger", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        selection = load_object(args.selection, MAX_SUMMARY_BYTES)
        prior = ledger.load_ledger(args.ledger)
        results = gather_results(args.results) if args.results.is_dir() else {}
    except (OSError, ValueError, ledger.LedgerError) as exc:
        parser.exit(1, f"report: {ascii(str(exc))}\n")
    targets = cast("list[dict[str, Any]]", selection.get("targets", []))
    dry_run = bool(selection.get("dry_run", True))
    rows = [row_for(target, results.get(str(target["key"]))) for target in targets]
    recorded = sum(
        ledger.record(
            prior,
            results[str(t["key"])],
            run_id=args.run_id,
            recorded=RECORDED_VERDICTS,
        )
        for t in targets
        if str(t["key"]) in results
    )
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "run_id": args.run_id,
        "dry_run": dry_run,
        "targets": rows,
        "recorded": recorded,
    }
    for path in (args.output_md, args.output_json, args.output_ledger):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(describe(rows, dry_run), encoding="utf-8")
    args.output_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    args.output_ledger.write_text(
        json.dumps(ledger.merge([prior]), indent=2) + "\n", encoding="utf-8"
    )
    print(f"report: {len(rows)} row(s), {recorded} recorded")


if __name__ == "__main__":
    main()
