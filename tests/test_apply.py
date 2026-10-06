# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""The placeholder apply step trusts nothing in the session but its shape."""

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

apply = import_module("apply")

SHA = "a" * 40
TARGET: dict[str, Any] = {
    "key": "repo-157",
    "repository": "lfreleng-actions/repo",
    "repo_name": "repo",
    "number": 157,
    "head_sha": SHA,
}
SELECTION: dict[str, Any] = {"schema": 1, "dry_run": True, "targets": [TARGET]}


def block(**overrides: Any) -> dict[str, Any]:
    """A valid verdict block with overrides applied."""
    verdict: dict[str, Any] = {
        "schema": 1,
        "repository": TARGET["repository"],
        "number": 157,
        "head_sha": SHA,
        "verdict": "act",
        "summary": "Looks fine.",
    }
    verdict.update(overrides)
    return verdict


def summary_with(*blocks: str) -> str:
    """A session summary carrying the given fenced json blocks."""
    return "# Session\n\n" + "\n\n".join(f"```json\n{b}\n```" for b in blocks) + "\n"


class VerdictBlockTest(unittest.TestCase):
    """The last fenced json block is the verdict; anything else is none."""

    def test_last_block_wins(self) -> None:
        """Earlier blocks are prose the agent wrote; the final one counts."""
        text = summary_with('{"draft": true}', json.dumps(block()))
        self.assertEqual(apply.verdict_block(text), block())

    def test_missing_or_malformed_block_is_none(self) -> None:
        """No block, invalid JSON and a non-object all yield None."""
        for text in ("no fence", summary_with("{not json"), summary_with("[1]")):
            with self.subTest(text=text):
                self.assertIsNone(apply.verdict_block(text))


class CheckVerdictTest(unittest.TestCase):
    """Every identity field and the token are checked against the target."""

    def test_valid_block_has_no_reasons(self) -> None:
        """A block naming this target with a known token is acceptable."""
        self.assertEqual(apply.check_verdict(TARGET, block()), [])

    def test_each_fault_is_named(self) -> None:
        """Wrong schema, target, token or summary each add a reason."""
        faults: list[tuple[dict[str, Any], str]] = [
            ({"schema": 2}, "schema"),
            ({"repository": "o/other"}, "repository"),
            ({"number": 158}, "number"),
            ({"head_sha": "b" * 40}, "head_sha"),
            ({"verdict": "approve"}, "token"),
            ({"summary": 5}, "summary"),
        ]
        for overrides, word in faults:
            with self.subTest(word=word):
                reasons = apply.check_verdict(TARGET, block(**overrides))
                self.assertEqual(len(reasons), 1)
                self.assertIn(word, reasons[0])
        self.assertEqual(len(apply.check_verdict(TARGET, None)), 1)


class MainTest(unittest.TestCase):
    """check and apply round-trip through files as the workflow runs them."""

    def setUp(self) -> None:
        """A scratch directory holding the selection and a session."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        (self.root / "selection.json").write_text(json.dumps(SELECTION))
        (self.root / "session").mkdir()

    def write_session(self, text: str) -> None:
        """Store the session summary the agent would have written."""
        (self.root / "session" / "session-summary.md").write_text(text)

    def run_check(self, *extra: str) -> tuple[str, dict[str, Any]]:
        """Run check; return the printed flag and the check file."""
        argv = [
            "check",
            "--selection",
            str(self.root / "selection.json"),
            "--key",
            "repo-157",
            "--session-dir",
            str(self.root / "session"),
            "--output",
            str(self.root / "out" / "check.json"),
            "--summary",
            str(self.root / "out" / "check-summary.md"),
            *extra,
        ]
        with redirect_stdout(io.StringIO()) as out:
            apply.main(argv)
        checked = json.loads((self.root / "out" / "check.json").read_text())
        return out.getvalue().strip(), checked

    def run_apply(self, *extra: str) -> dict[str, Any]:
        """Run apply on the check file; return result.json."""
        argv = [
            "apply",
            "--check",
            str(self.root / "out" / "check.json"),
            "--output",
            str(self.root / "out" / "result.json"),
            "--run-url",
            "https://example.test/run/1",
            "--run-attempt",
            "2",
            *extra,
        ]
        with redirect_stdout(io.StringIO()):
            apply.main(argv)
        return json.loads((self.root / "out" / "result.json").read_text())

    def test_act_verdict_is_approvable_and_dry_run_records_would_do(self) -> None:
        """A clean act verdict flips the flag; a dry run makes no write."""
        self.write_session(summary_with(json.dumps(block())))
        flag, checked = self.run_check()
        self.assertEqual(flag, "approvable=true")
        self.assertEqual(checked["target"]["key"], "repo-157")
        result = self.run_apply("--dry-run")
        self.assertEqual(result["verdict"], "would-do")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["run_attempt"], 2)
        self.assertEqual(result["repository"], TARGET["repository"])

    def test_live_placeholder_makes_no_write(self) -> None:
        """The template's live path records needs-human and says why."""
        self.write_session(summary_with(json.dumps(block())))
        self.run_check()
        result = self.run_apply()
        self.assertEqual(result["verdict"], "needs-human")
        self.assertIsNone(result["write_url"])
        self.assertIn("placeholder", result["reasons"][0])

    def test_needs_human_and_mismatch_are_not_approvable(self) -> None:
        """An agent asking for a human, or naming another target, stops here."""
        for verdict in (block(verdict="needs-human"), block(number=1)):
            with self.subTest(verdict=verdict):
                self.write_session(summary_with(json.dumps(verdict)))
                flag, checked = self.run_check()
                self.assertEqual(flag, "approvable=false")
                self.assertEqual(checked["verdict"], "needs-human")
                self.assertEqual(self.run_apply("--dry-run")["verdict"], "needs-human")

    def test_failure_text_becomes_a_failed_result(self) -> None:
        """A missing or refused session yields a typed failure, no read."""
        flag, checked = self.run_check("--failure", "no session artifact")
        self.assertEqual(flag, "approvable=false")
        self.assertEqual(checked["verdict"], "failed")
        self.assertEqual(checked["reasons"], ["no session artifact"])
        self.assertEqual(self.run_apply()["verdict"], "failed")

    def test_unknown_key_and_missing_session_exit_one(self) -> None:
        """Operational failures exit 1 with a prefixed message."""
        cases = (
            lambda: self.run_check(),
            lambda: self.run_check("--key", "other"),
        )
        for case in cases:
            with (
                redirect_stderr(io.StringIO()) as err,
                self.assertRaises(SystemExit) as caught,
            ):
                case()
            self.assertEqual(caught.exception.code, 1)
            self.assertIn("apply: ", err.getvalue())


if __name__ == "__main__":
    unittest.main()
