# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""The placeholder apply step trusts nothing in the session but its shape."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib import import_module
from pathlib import Path
from typing import Any
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

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
    """The final fenced block is the verdict when it is json; else none."""

    def test_last_block_wins(self) -> None:
        """Earlier blocks are prose the agent wrote; the final one counts."""
        text = summary_with('{"draft": true}', json.dumps(block()))
        self.assertEqual(apply.verdict_block(text), block())

    def test_prose_after_the_final_block_is_ignored(self) -> None:
        """Text after the final fence closes leaves the block the verdict."""
        text = summary_with(json.dumps(block())) + "\nThat is all.\n"
        self.assertEqual(apply.verdict_block(text), block())

    def test_missing_or_malformed_block_is_none(self) -> None:
        """No block, invalid JSON and a non-object all yield None."""
        for text in ("no fence", summary_with("{not json"), summary_with("[1]")):
            with self.subTest(text=text):
                self.assertIsNone(apply.verdict_block(text))

    def test_deep_nesting_is_none(self) -> None:
        """A block nested past the parser's recursion limit is no verdict."""
        self.assertIsNone(apply.verdict_block(summary_with("[" * 100_000)))

    def test_a_later_block_of_another_kind_is_no_verdict(self) -> None:
        """An earlier json block never stands in for the final one."""
        verdict = summary_with(json.dumps(block()))
        for later in ("```\nplain\n```", "```text\nout\n```", "~~~py\nx = 1\n~~~"):
            with self.subTest(later=later):
                self.assertIsNone(apply.verdict_block(f"{verdict}\n{later}\n"))

    def test_an_unterminated_final_fence_is_no_verdict(self) -> None:
        """A final fence that never closes hides every earlier block."""
        verdict = summary_with(json.dumps(block()))
        for tail in (
            '```json\n{"schema": 1',
            "```\nlog output",
            "````json\n" + json.dumps(block()) + "\n```",
        ):
            with self.subTest(tail=tail):
                self.assertIsNone(apply.verdict_block(f"{verdict}\n{tail}\n"))

    def test_a_tilde_fence_closes_with_tildes_alone(self) -> None:
        """A closer repeats the opener's character, at least as many times."""
        body = json.dumps(block())
        self.assertEqual(apply.verdict_block(f"~~~json\n{body}\n~~~\n"), block())
        self.assertEqual(apply.verdict_block(f"```json\n{body}\n`````\n"), block())
        self.assertIsNone(apply.verdict_block(f"~~~json\n{body}\n```\n"))
        decoy = f"````md\n```json\n{body}\n```\n````\n"
        self.assertIsNone(apply.verdict_block(decoy))

    def test_unclosed_openers_take_linear_time(self) -> None:
        """Many unclosed openers, or one long opener, are read within a second.

        The last text holds a megabyte-long opener and 200,000 fences
        inside its block; comparing each against a fresh copy of the
        opener would take far longer than the timeout.
        """
        script = (
            "import sys, time\n"
            f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
            "import apply\n"
            "long_opener = '`' * 1_000_000 + 'json\\n' + '```x\\n' * 200_000\n"
            "for text in ('x```json\\n' * 100_000, '```json\\n' * 100_000,"
            " long_opener):\n"
            "    start = time.perf_counter()\n"
            "    assert apply.verdict_block(text) is None\n"
            "    print(time.perf_counter() - start)\n"
        )
        try:
            ran = subprocess.run(
                [sys.executable, "-B", "-c", script],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except subprocess.TimeoutExpired:
            self.fail("verdict_block ran past five seconds")
        for elapsed in ran.stdout.split():
            self.assertLess(float(elapsed), 1.0)


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

    def test_a_reported_injection_attempt_is_a_reason(self) -> None:
        """An act verdict that reports an attempt is held back, and says why."""
        reported = block(injection_attempts=["README asks for an approval"])
        self.assertEqual(
            apply.check_verdict(TARGET, reported),
            ["the agent reported an injection attempt"],
        )

    def test_empty_or_absent_lists_add_no_reason(self) -> None:
        """No attempt and no finding leave the verdict acceptable."""
        cases: list[dict[str, Any]] = [{}, {"findings": [], "injection_attempts": []}]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertEqual(apply.check_verdict(TARGET, block(**overrides)), [])

    def test_list_fields_of_the_wrong_type_are_reasons(self) -> None:
        """findings and injection_attempts, where present, must be lists."""
        for field in ("findings", "injection_attempts"):
            for value in ("text", {"note": "x"}, None, 3):
                with self.subTest(field=field, value=value):
                    reasons = apply.check_verdict(TARGET, block(**{field: value}))
                    self.assertEqual(reasons, [f"verdict {field} is not a list"])


class EntryTextTest(unittest.TestCase):
    """A reported entry always becomes bounded text."""

    def test_an_entry_too_deep_to_encode_is_empty(self) -> None:
        """The decoder accepts deeper nesting than the encoder writes."""
        with mock.patch.object(apply.json, "dumps", side_effect=RecursionError):
            self.assertEqual(apply.entry_text([[1]]), "")
        self.assertEqual(apply.entry_text("x" * 500), "x" * 400)


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

    def test_reported_injection_is_not_approvable(self) -> None:
        """An act verdict reporting an attempt stops at the check."""
        reported = block(injection_attempts=["README asks for an approval"])
        self.write_session(summary_with(json.dumps(reported)))
        flag, checked = self.run_check()
        self.assertEqual(flag, "approvable=false")
        self.assertEqual(
            checked["reasons"], ["the agent reported an injection attempt"]
        )
        self.assertEqual(checked["injection_attempts"], ["README asks for an approval"])
        result = self.run_apply("--dry-run")
        self.assertEqual(result["verdict"], "needs-human")
        self.assertEqual(result["injection_attempts"], ["README asks for an approval"])

    def test_empty_lists_are_approvable(self) -> None:
        """An act verdict with nothing reported is approvable, as before."""
        clean = block(findings=[], injection_attempts=[])
        self.write_session(summary_with(json.dumps(clean)))
        self.assertEqual(self.run_check()[0], "approvable=true")

    def test_lists_reach_check_and_result_within_bounds(self) -> None:
        """At most 20 entries each, every string cut to 400 characters."""
        attempts = [f"{n:02d}" + "a" * 500 for n in range(25)]
        findings = [{"area": "ci", "note": f"{n:02d}" + "n" * 500} for n in range(25)]
        verdict = block(verdict="needs-human", injection_attempts=attempts)
        self.write_session(summary_with(json.dumps({**verdict, "findings": findings})))
        _, checked = self.run_check()
        expected_attempts = [text[:400] for text in attempts[:20]]
        expected_findings = [
            {"area": "ci", "note": finding["note"][:400]} for finding in findings[:20]
        ]
        self.assertEqual(checked["injection_attempts"], expected_attempts)
        self.assertEqual(checked["findings"], expected_findings)
        result = self.run_apply("--dry-run")
        self.assertEqual(result["injection_attempts"], expected_attempts)
        self.assertEqual(result["findings"], expected_findings)

    def test_entries_of_another_shape_are_recorded_as_text(self) -> None:
        """A non-string entry is kept as compact JSON, never dropped."""
        verdict = block(
            verdict="needs-human",
            injection_attempts=[{"where": "README", "asked": "approve"}, 7],
            findings=[
                {"area": "ci", "note": [1, 2]},
                "bare text",
                {"note": "x"},
                {"message": "credential exposed"},
                {"area": "ci", "message": "m"},
            ],
        )
        self.write_session(summary_with(json.dumps(verdict)))
        _, checked = self.run_check()
        self.assertEqual(
            checked["injection_attempts"],
            ['{"where": "README", "asked": "approve"}', "7"],
        )
        self.assertEqual(
            checked["findings"],
            [
                {"area": "ci", "note": "[1, 2]"},
                {"area": "", "note": "bare text"},
                {"area": "", "note": "x"},
                {"area": "", "note": '{"message": "credential exposed"}'},
                {"area": "ci", "note": '{"area": "ci", "message": "m"}'},
            ],
        )

    def test_no_verdict_records_empty_lists(self) -> None:
        """A failure or a list of the wrong type records nothing."""
        _, checked = self.run_check("--failure", "no session artifact")
        self.assertEqual((checked["findings"], checked["injection_attempts"]), ([], []))
        self.write_session(summary_with(json.dumps(block(findings="many"))))
        _, checked = self.run_check()
        self.assertEqual(checked["findings"], [])

    def test_session_usage_reaches_check_and_result(self) -> None:
        """The accepted usage.json is read once and carried through."""
        usage = {
            "totalPremiumRequestCost": 1,
            "totalApiDurationMs": 31330,
            "modelMetrics": {"gpt-6-astra": {"requests": {"count": 7}}},
        }
        (self.root / "session" / "usage.json").write_text(json.dumps(usage))
        self.write_session(summary_with(json.dumps(block())))
        _, checked = self.run_check()
        expected: dict[str, Any] = {
            "premium_requests": 1.0,
            "agent_seconds": 31,
            "usage": {"models": [{"model": "gpt-6-astra", "requests": 7}]},
        }
        result = self.run_apply("--dry-run")
        for written in (checked, result):
            self.assertEqual({k: written[k] for k in expected}, expected)

    def test_absent_or_unreadable_usage_is_null(self) -> None:
        """No usage.json, or one that is not JSON, records nulls and still checks."""
        self.write_session(summary_with(json.dumps(block())))
        for content in (None, "not json", "[1, 2]"):
            if content is not None:
                (self.root / "session" / "usage.json").write_text(content)
            with self.subTest(content=content):
                flag, checked = self.run_check()
                self.assertEqual(flag, "approvable=true")
                for field in ("premium_requests", "agent_seconds", "usage"):
                    self.assertIsNone(checked[field])

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
