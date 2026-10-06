# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""The placeholder report gathers results and carries the ledger forward."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib import import_module
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

report = import_module("report")
ledger = import_module("ledger")

SHA = "a" * 40


def target(key: str, number: int) -> dict[str, Any]:
    """One selection target."""
    return {
        "key": key,
        "repository": "lfreleng-actions/repo",
        "repo_name": "repo",
        "number": number,
        "head_sha": SHA,
    }


def result(key: str, number: int, verdict: str, attempt: int = 1) -> dict[str, Any]:
    """One result.json as apply.py writes it."""
    return {
        **target(key, number),
        "schema": 1,
        "verdict": verdict,
        "reasons": ["why"],
        "summary": None,
        "write_url": None,
        "dry_run": True,
        "run_attempt": attempt,
        "run_url": "https://example.test/run/1",
    }


class ReportTest(unittest.TestCase):
    """Rows, retries, missing results and the ledger."""

    def setUp(self) -> None:
        """A scratch tree shaped like the report job's working directory."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        (self.root / "results").mkdir()
        (self.root / "ledger.json").write_text('{"schema": 1, "entries": []}\n')

    def write_selection(self, *targets: dict[str, Any]) -> None:
        """Store the selection evidence."""
        selection = {"schema": 1, "dry_run": True, "targets": list(targets)}
        (self.root / "selection.json").write_text(json.dumps(selection))

    def write_result(self, folder: str, data: dict[str, Any]) -> None:
        """Store one downloaded result artifact."""
        (self.root / "results" / folder).mkdir()
        (self.root / "results" / folder / "result.json").write_text(json.dumps(data))

    def run_main(self) -> dict[str, Any]:
        """Run the CLI and return report.json."""
        argv = [
            "--selection",
            str(self.root / "selection.json"),
            "--results",
            str(self.root / "results"),
            "--ledger",
            str(self.root / "ledger.json"),
            "--run-id",
            "42",
            "--output-md",
            str(self.root / "out" / "report.md"),
            "--output-json",
            str(self.root / "out" / "report.json"),
            "--output-ledger",
            str(self.root / "out" / "ledger.json"),
        ]
        with redirect_stdout(io.StringIO()):
            report.main(argv)
        return json.loads((self.root / "out" / "report.json").read_text())

    def test_empty_selection_writes_every_file(self) -> None:
        """The zero-target plumbing run still produces a report and a ledger."""
        self.write_selection()
        (self.root / "results").rmdir()
        built = self.run_main()
        self.assertEqual(built["targets"], [])
        self.assertEqual(built["recorded"], 0)
        self.assertIn("0 target(s)", (self.root / "out" / "report.md").read_text())
        self.assertEqual(
            json.loads((self.root / "out" / "ledger.json").read_text()),
            ledger.empty_ledger(),
        )

    def test_rows_record_verdicts_and_note_missing_results(self) -> None:
        """Recordable verdicts reach the ledger; failed and missing do not."""
        self.write_selection(target("a", 1), target("b", 2), target("c", 3))
        self.write_result("a-1", result("a", 1, "would-do"))
        self.write_result("b-1", result("b", 2, "failed"))
        built = self.run_main()
        self.assertEqual(
            [(row["key"], row["verdict"]) for row in built["targets"]],
            [("a", "would-do"), ("b", "failed"), ("c", "missing")],
        )
        self.assertEqual(built["recorded"], 1)
        written = json.loads((self.root / "out" / "ledger.json").read_text())
        self.assertEqual([e["number"] for e in written["entries"]], [1])
        self.assertEqual(written["entries"][0]["run_id"], 42)
        text = (self.root / "out" / "report.md").read_text()
        self.assertIn("| `lfreleng-actions/repo#3` | missing |  |", text)

    def test_highest_attempt_wins_and_odd_files_are_ignored(self) -> None:
        """A retry's result supersedes the first; unknown shapes are skipped."""
        self.write_selection(target("a", 1))
        self.write_result("a-1", result("a", 1, "needs-human", attempt=1))
        self.write_result("a-2", result("a", 1, "would-do", attempt=2))
        self.write_result("junk", {"schema": 9, "key": "a"})
        self.write_result("nokey", {"schema": 1})
        gathered = report.gather_results(self.root / "results")
        self.assertEqual(gathered["a"]["verdict"], "would-do")

    def test_unreadable_inputs_exit_one(self) -> None:
        """A bad selection or ledger is an operational failure."""
        (self.root / "selection.json").write_text("[]")
        with (
            redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as caught,
        ):
            self.run_main()
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("report: ", err.getvalue())


if __name__ == "__main__":
    unittest.main()
