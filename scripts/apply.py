# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Template placeholder: corroborate an agent's verdict, then make the one write.

``check`` takes the final fenced ``json`` block of the accepted
``session-summary.md`` as the verdict, refuses one about another
target, writes ``check.json`` and prints ``approvable=true`` when the
write mint may run. ``apply`` turns the check into ``result.json``: a
dry run records what it would have done, a live run makes the write.
The session is hostile input whatever its name says; replace
``perform_write`` with the bot's live validation and its one write.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, cast

from bot_evidence import MAX_SUMMARY_BYTES, read_regular

SCHEMA = 1
AGENT_VERDICTS = frozenset({"act", "needs-human"})
FENCE_RE = re.compile(r"```json[ \t]*\n(.*?)\n```", re.DOTALL)
IDENTITY = ("key", "repository", "number", "head_sha")


def load_json(path: Path) -> dict[str, Any]:
    """Read a bounded JSON object written by a trusted step."""
    parsed: Any = json.loads(read_regular(path, MAX_SUMMARY_BYTES))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path.name} is not an object")
    return cast("dict[str, Any]", parsed)


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write one JSON object, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def find_target(selection: dict[str, Any], key: str) -> dict[str, Any]:
    """The selection entry this matrix key names; absence is a failure."""
    targets = cast("list[dict[str, Any]]", selection.get("targets", []))
    matches = [t for t in targets if t.get("key") == key]
    if len(matches) != 1:
        raise ValueError(f"selection has no single target {key!r}")
    return matches[0]


def verdict_block(summary: str) -> dict[str, Any] | None:
    """The last fenced json block of the session summary, or None."""
    blocks = FENCE_RE.findall(summary)
    try:
        parsed: Any = json.loads(blocks[-1]) if blocks else None
    except ValueError:
        return None
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None


def check_verdict(target: dict[str, Any], block: dict[str, Any] | None) -> list[str]:
    """Reasons the verdict cannot be acted on; empty means it may be."""
    if block is None:
        return ["the session ended without a verdict block"]
    faults = (
        (block.get("schema") != SCHEMA, "verdict schema is not 1"),
        *(
            (block.get(f) != target[f], f"verdict names a different {f}")
            for f in IDENTITY[1:]
        ),
        (block.get("verdict") not in AGENT_VERDICTS, "verdict token is unknown"),
        (not isinstance(block.get("summary"), str), "verdict has no summary text"),
    )
    return [reason for faulty, reason in faults if faulty]


def check(args: argparse.Namespace) -> None:
    """Write check.json and the step summary; print the approvable flag."""
    target = find_target(load_json(args.selection), args.key)
    block, reasons = None, [args.failure] if args.failure else []
    if not args.failure:
        text = read_regular(args.session_dir / "session-summary.md", MAX_SUMMARY_BYTES)
        block = verdict_block(text.decode("utf-8", errors="replace"))
        reasons = check_verdict(target, block)
    approvable = not reasons and block is not None and block["verdict"] == "act"
    checked: dict[str, Any] = {"schema": SCHEMA, "approvable": approvable}
    checked["target"] = {k: target[k] for k in IDENTITY}
    checked["verdict"] = "failed" if args.failure else "needs-human"
    checked["summary"] = str(block.get("summary", ""))[:2000] if block else None
    write_json(args.output, {**checked, "reasons": reasons})
    state = "approvable" if approvable else "not approvable"
    heading = f"### `{target['repository']}#{target['number']}`: {state}\n\n"
    notes = "".join(f"- {reason}\n" for reason in reasons)
    args.summary.write_text(heading + notes, encoding="utf-8")
    print(f"approvable={'true' if approvable else 'false'}")


def perform_write(target: dict[str, Any], summary: str | None) -> str | None:
    """Template placeholder: the bot's one write, returning its URL or None."""
    del target, summary
    return None


def apply(args: argparse.Namespace) -> None:
    """Write result.json from the check, making the write on the live path."""
    checked = load_json(args.check)
    target = cast("dict[str, Any]", checked["target"])
    verdict, url = str(checked["verdict"]), None
    reasons = [str(r) for r in cast("list[Any]", checked["reasons"])]
    if checked["approvable"] and args.dry_run:
        verdict = "would-do"
    elif checked["approvable"]:
        url = perform_write(target, checked.get("summary"))
        verdict = "done" if url else "needs-human"
        reasons += [] if url else ["the template placeholder makes no write"]
    result: dict[str, Any] = {"schema": SCHEMA, **target, "verdict": verdict}
    result.update(reasons=reasons, summary=checked.get("summary"), write_url=url)
    result.update(dry_run=args.dry_run, run_attempt=args.run_attempt)
    write_json(args.output, {**result, "run_url": args.run_url})
    print(f"apply: {target['repository']}#{target['number']} -> {verdict}")


def main(argv: list[str] | None = None) -> None:
    """Dispatch check or apply."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    checker = commands.add_parser("check", help="corroborate the session verdict")
    for flag in ("--selection", "--session-dir", "--output", "--summary"):
        checker.add_argument(flag, type=Path, required=True)
    checker.add_argument("--key", required=True)
    checker.add_argument("--failure", default="")
    applier = commands.add_parser("apply", help="write result.json; live runs act")
    for flag in ("--check", "--output"):
        applier.add_argument(flag, type=Path, required=True)
    applier.add_argument("--run-url", required=True)
    applier.add_argument("--run-attempt", type=int, required=True)
    applier.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        check(args) if args.command == "check" else apply(args)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"apply: {ascii(str(exc))}\n")


if __name__ == "__main__":
    main()
