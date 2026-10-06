# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Template placeholder: choose the targets a run will hand to the agent.

The prepare job runs this from the trusted assets checkout with a
read-only token, and publishes what it writes as the run's evidence:

``selection.json``
    the targets this run works on, with everything a trusted job
    later needs to corroborate an agent's verdict
``matrix.json``
    ``{"include": [...]}``, one entry per target, for the agent and
    apply matrices; ``key`` names the target's artifacts
``selection-summary.md``
    the step summary

Replace ``select_targets`` with the bot's own selection: scan the
organisation, drop what the ledger already records at the same head,
cap the list. Keep the entry shape, which the workflow's provenance
checks and ``apply.py`` rely on, and keep this script free of writes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMA = 1
TARGET_KEYS = ("key", "repository", "repo_name", "number", "head_sha")


def load_exclusions(path: Path, extra: str) -> set[str]:
    """Bare repository names to skip: the bundled list plus the input.

    A non-empty ``extra`` replaces the file, as the workflow input
    documents, so an operator can name exactly what a run skips.
    """
    if extra.strip():
        return {name for name in extra.replace(",", " ").split() if name}
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            names.add(text)
    return names


def select_targets(
    org: str, repositories: list[str], excluded: set[str], limit: int
) -> list[dict[str, Any]]:
    """Template placeholder: the bot's selection goes here.

    Return at most ``limit`` entries (``0`` lifts the cap), each with
    the keys in ``TARGET_KEYS``: ``repository`` as ``owner/name``,
    ``repo_name`` as the bare name the write mint is scoped to,
    ``number`` the issue or pull request, ``head_sha`` the commit the
    agent looked at, and ``key`` a short ``[A-Za-z0-9_.-]+`` token
    unique within the run.
    """
    del org, repositories, excluded, limit
    return []


def matrix_for(targets: list[dict[str, Any]]) -> dict[str, Any]:
    """The matrix the agent and apply jobs fan out over."""
    return {"include": [{k: t[k] for k in TARGET_KEYS} for t in targets]}


def summary_for(selection: dict[str, Any]) -> str:
    """A short step summary of what was selected."""
    targets = selection["targets"]
    mode = "dry run" if selection["dry_run"] else "live"
    lines = [f"## Selection ({mode})", "", f"{len(targets)} target(s) selected."]
    for target in targets:
        lines.append(f"- `{target['repository']}#{target['number']}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    """Write the selection, the matrix and the summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--repositories", default="")
    parser.add_argument("--exclude-file", type=Path, required=True)
    parser.add_argument("--exclude-repos", default="")
    parser.add_argument("--max-targets", type=int, default=20)
    args = parser.parse_args(argv)
    if args.max_targets < 0:
        parser.exit(1, "prepare: --max-targets must be non-negative\n")
    if not args.ledger.is_file():
        parser.exit(1, f"prepare: ledger {args.ledger} is missing\n")
    repositories = [r for r in args.repositories.replace(",", " ").split() if r]
    excluded = load_exclusions(args.exclude_file, args.exclude_repos)
    targets = select_targets(args.org, repositories, excluded, args.max_targets)
    selection: dict[str, Any] = {
        "schema": SCHEMA,
        "org": args.org,
        "dry_run": args.dry_run,
        "model": args.model,
        "targets": targets,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "selection.json").write_text(
        json.dumps(selection, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "matrix.json").write_text(
        json.dumps(matrix_for(targets)) + "\n", encoding="utf-8"
    )
    (args.output_dir / "selection-summary.md").write_text(
        summary_for(selection), encoding="utf-8"
    )
    print(f"prepare: {len(targets)} target(s) -> {args.output_dir}")


if __name__ == "__main__":
    main()
