# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Bounded reads of regular files and verification of trusted evidence."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from importlib import import_module
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

evidence = import_module("bot_evidence")

SELECTION = b'{"schema": 1, "targets": []}\n'
LEDGER = b'{"schema": 1, "entries": []}\n'
LIMIT = evidence.MAX_EVIDENCE_BYTES


def digest(content: bytes) -> str:
    """The hex SHA-256 of some bytes."""
    return hashlib.sha256(content).hexdigest()


def expect(selection: str, ledger: str) -> list[tuple[str, str, int]]:
    """Expectations for the two sample files at the default limit."""
    return [("selection.json", selection, LIMIT), ("ledger.json", ledger, LIMIT)]


def sample_usage() -> dict[str, Any]:
    """What @github/copilot 1.0.105 wrote in one session, cut to the fields read."""
    return {
        "totalPremiumRequestCost": 1,
        "totalNanoAiu": 51939650000,
        "totalApiDurationMs": 31330,
        "lastCallInputTokens": 30972,
        "modelMetrics": {
            "gpt-6-astra": {
                "requests": {"count": 7, "cost": 1},
                "usage": {
                    "inputTokens": 122814,
                    "outputTokens": 805,
                    "cacheReadTokens": 91824,
                    "cacheWriteTokens": 30969,
                    "reasoningTokens": 80,
                },
                "totalNanoAiu": 51939650000,
            }
        },
    }


SAMPLE_RECORD: dict[str, Any] = {
    "nano_aiu": 51939650000,
    "last_call_input_tokens": 30972,
    "models": [
        {
            "model": "gpt-6-astra",
            "requests": 7,
            "premium_requests": 1.0,
            "input_tokens": 122814,
            "cache_read_tokens": 91824,
            "cache_write_tokens": 30969,
            "output_tokens": 805,
            "reasoning_tokens": 80,
            "nano_aiu": 51939650000,
        }
    ],
}


def model_usage(name: str, **figures: Any) -> dict[str, Any]:
    """One model's record as a result file carries it."""
    row: dict[str, Any] = {
        "model": name,
        "requests": 7,
        "premium_requests": 1.0,
        "input_tokens": 100,
        "cache_read_tokens": 60,
        "cache_write_tokens": 30,
        "output_tokens": 8,
        "reasoning_tokens": 2,
        "nano_aiu": 1000,
    }
    row.update(figures)
    return row


class SessionUsageTest(unittest.TestCase):
    """``session_usage`` reads an untrusted usage.json into three fields."""

    def test_the_sample_reads_into_the_record(self) -> None:
        """Every figure of the sample lands under its own name."""
        got = evidence.session_usage(sample_usage())
        self.assertEqual(
            got,
            {"premium_requests": 1.0, "agent_seconds": 31, "usage": SAMPLE_RECORD},
        )
        self.assertIsInstance(got["premium_requests"], float)
        self.assertIsInstance(got["usage"]["models"][0]["premium_requests"], float)

    def test_a_bad_figure_is_left_out(self) -> None:
        """A non-finite, negative, boolean or non-numeric figure is dropped alone."""
        for bad in (float("inf"), float("nan"), -1, True, "7", None, 10**400):
            usage = sample_usage()
            usage["totalNanoAiu"] = bad
            usage["totalPremiumRequestCost"] = bad
            usage["totalApiDurationMs"] = bad
            metrics = usage["modelMetrics"]["gpt-6-astra"]
            metrics["requests"]["count"] = bad
            metrics["usage"]["outputTokens"] = bad
            with self.subTest(bad=bad):
                got = evidence.session_usage(usage)
                self.assertIsNone(got["premium_requests"])
                self.assertIsNone(got["agent_seconds"])
                self.assertNotIn("nano_aiu", got["usage"])
                model = got["usage"]["models"][0]
                self.assertNotIn("requests", model)
                self.assertNotIn("output_tokens", model)
                self.assertEqual(model["reasoning_tokens"], 80)

    def test_a_count_is_stored_as_an_integer(self) -> None:
        """A count written as a float is kept as an int; a cost stays a float."""
        usage = sample_usage()
        usage["lastCallInputTokens"] = 30972.0
        usage["totalApiDurationMs"] = 31999.9
        got = evidence.session_usage(usage)
        self.assertEqual(got["agent_seconds"], 31)
        self.assertIsInstance(got["usage"]["last_call_input_tokens"], int)

    def test_a_model_name_outside_the_pattern_is_left_out(self) -> None:
        """Only a lower-case model identifier of at most 100 characters is kept."""
        usage = sample_usage()
        metrics = usage["modelMetrics"]
        sample = metrics["gpt-6-astra"]
        for name in ("GPT-6", "gpt 6", "@someone", "x" * 101, "", "a|b", "a\n"):
            metrics[name] = sample
        metrics["y" * 100] = sample
        metrics["not-an-object"] = [1, 2]
        names = [m["model"] for m in evidence.session_usage(usage)["usage"]["models"]]
        self.assertEqual(names, ["gpt-6-astra", "y" * 100])

    def test_a_ninth_model_is_dropped(self) -> None:
        """At most eight models are kept, sorted by name."""
        usage = sample_usage()
        sample = usage["modelMetrics"].pop("gpt-6-astra")
        usage["modelMetrics"] = {f"model-{n}": sample for n in range(9, 0, -1)}
        names = [m["model"] for m in evidence.session_usage(usage)["usage"]["models"]]
        self.assertEqual(names, [f"model-{n}" for n in range(1, 9)])

    def test_a_model_with_no_figure_is_left_out(self) -> None:
        """An empty model is dropped before the limit and cannot displace one."""
        usage = sample_usage()
        usage["modelMetrics"].update(
            {f"a-{n}": {"requests": {"count": True}} for n in range(8)}
        )
        got = evidence.session_usage(usage)
        self.assertEqual(got["usage"]["models"], SAMPLE_RECORD["models"])

    def test_no_file_yields_nulls(self) -> None:
        """No file, a file of another shape, or no usage figure gives nulls."""
        nulls = {"premium_requests": None, "agent_seconds": None, "usage": None}
        odd: list[Any] = [None, [], "usage", 3, {}, {"modelMetrics": "nope"}]
        for value in odd:
            with self.subTest(value=value):
                self.assertEqual(evidence.session_usage(value), nulls)
        cost_alone = evidence.session_usage({"totalPremiumRequestCost": 2})
        self.assertEqual(cost_alone, {**nulls, "premium_requests": 2.0})

    def test_odd_shapes_never_raise(self) -> None:
        """Every nested object may be anything; nothing raises."""
        shapes: list[Any] = [
            {"gpt-6-astra": {"requests": "x", "usage": 5, "totalNanoAiu": []}},
            {"gpt-6-astra": {"requests": {"count": {}}, "usage": {"inputTokens": []}}},
            ["gpt-6-astra"],
        ]
        for metrics in shapes:
            with self.subTest(metrics=metrics):
                got = evidence.session_usage({"modelMetrics": metrics})
                self.assertIsNone(got["usage"])


class UsageTotalsTest(unittest.TestCase):
    """Totals across result files, each figure checked again as it is summed."""

    def test_totals_by_model_across_results(self) -> None:
        """Each model counts the sessions that used it and sums each figure."""
        results: list[dict[str, Any]] = [
            {"usage": {"models": [model_usage("gpt-6-astra"), model_usage("b-model")]}},
            {"usage": {"models": [model_usage("gpt-6-astra", premium_requests=0.5)]}},
        ]
        doubled = model_usage(
            "gpt-6-astra",
            requests=14,
            premium_requests=1.5,
            input_tokens=200,
            cache_read_tokens=120,
            cache_write_tokens=60,
            output_tokens=16,
            reasoning_tokens=4,
            nano_aiu=2000,
        )
        self.assertEqual(
            evidence.usage_totals(results),
            [
                {**model_usage("b-model"), "sessions": 1},
                {**doubled, "sessions": 2},
            ],
        )

    def test_absent_or_malformed_usage_adds_nothing(self) -> None:
        """A result file is a claim: anything malformed is skipped, alone."""
        forged = model_usage(
            "gpt-6-astra",
            requests=True,
            premium_requests=float("inf"),
            input_tokens=-1,
            cache_read_tokens="9",
            cache_write_tokens=1.5,
            output_tokens=None,
            reasoning_tokens=float("nan"),
            nano_aiu=10**400,
        )
        bad_rows = ["gpt-6-astra", {"model": "Bad Name", "requests": 5}, forged]
        results: list[dict[str, Any]] = [
            {"usage": {"models": [model_usage("gpt-6-astra")]}},
            {},
            {"usage": None},
            {"usage": "lots"},
            {"usage": {"models": "many"}},
            {"usage": {"models": bad_rows}},
        ]
        self.assertEqual(
            evidence.usage_totals(results),
            [{**model_usage("gpt-6-astra"), "sessions": 1}],
        )

    def test_a_total_stays_finite_and_bounded(self) -> None:
        """A total never passes a finite number; a result reads eight models."""
        big = model_usage("gpt-6-astra", premium_requests=1e308, nano_aiu=10**308)
        totals = evidence.usage_totals([{"usage": {"models": [big]}}] * 2)
        self.assertEqual(totals[0]["premium_requests"], 1e308)
        self.assertEqual(totals[0]["nano_aiu"], 10**308)
        self.assertEqual(totals[0]["requests"], 14)
        json.dumps(totals, allow_nan=False)
        many = [model_usage(f"model-{n}") for n in range(1, 21)]
        self.assertEqual(len(evidence.usage_totals([{"usage": {"models": many}}])), 8)

    def test_spend_sums_checked_figures(self) -> None:
        """Premium requests and agent seconds add up; odd values add nothing."""
        results: list[dict[str, Any]] = [
            {"premium_requests": 1.0, "agent_seconds": 31},
            {"premium_requests": 0.333, "agent_seconds": 9},
            {"premium_requests": True, "agent_seconds": 2.5},
            {"premium_requests": float("nan"), "agent_seconds": -4},
            {"premium_requests": None, "agent_seconds": None},
            {},
        ]
        self.assertEqual(evidence.spend_totals(results), (1.33, 40))

    def test_agent_seconds_stay_within_a_finite_total(self) -> None:
        """An integer past float range, or one that would carry the total there, adds nothing."""
        results: list[dict[str, Any]] = [
            {"agent_seconds": 10**400},
            {"agent_seconds": 10**308},
            {"agent_seconds": 10**308},
            {"agent_seconds": 5},
        ]
        self.assertEqual(evidence.spend_totals(results), (0.0, 10**308 + 5))


class ReadRegularTest(unittest.TestCase):
    """``read_regular`` takes a bounded regular file and nothing else."""

    def setUp(self) -> None:
        """A scratch directory holding the files under test."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)

    def test_reads_a_regular_file_within_the_limit(self) -> None:
        """A file at exactly the limit is returned whole."""
        path = self.root / "f"
        path.write_bytes(b"x" * 16)
        self.assertEqual(evidence.read_regular(path, 16), b"x" * 16)

    def test_oversize_file_refused(self) -> None:
        """One byte past the limit is a refusal."""
        path = self.root / "f"
        path.write_bytes(b"x" * 17)
        with self.assertRaisesRegex(ValueError, "exceeds the 16-byte limit"):
            evidence.read_regular(path, 16)

    def test_symlink_refused(self) -> None:
        """A symlink in the final component is never followed."""
        target = self.root / "real"
        target.write_bytes(b"x")
        linked = self.root / "link"
        linked.symlink_to(target)
        with self.assertRaises(OSError):
            evidence.read_regular(linked, 16)

    def test_symlinked_parent_refused(self) -> None:
        """A symlink standing in for the directory is refused too."""
        real = self.root / "dir"
        real.mkdir()
        (real / "f").write_bytes(b"x")
        (self.root / "alias").symlink_to(real)
        with self.assertRaises(OSError):
            evidence.read_regular(self.root / "alias" / "f", 16)

    def test_directory_refused(self) -> None:
        """A directory where a file should be is refused."""
        (self.root / "d").mkdir()
        with self.assertRaises((OSError, ValueError)):
            evidence.read_regular(self.root / "d", 16)

    def test_fifo_refused_without_blocking(self) -> None:
        """A FIFO with no writer is rejected rather than waited on."""
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "regular non-symlink file"):
            evidence.read_regular(fifo, 16)

    def test_missing_file_is_an_os_error(self) -> None:
        """An absent file surfaces as OSError for the caller to report."""
        with self.assertRaises(OSError):
            evidence.read_regular(self.root / "absent", 16)


class VerifyTest(unittest.TestCase):
    """``verify`` authenticates every expected file against its digest."""

    def setUp(self) -> None:
        """A directory holding a selection and a ledger."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        (self.root / "selection.json").write_bytes(SELECTION)
        (self.root / "ledger.json").write_bytes(LEDGER)

    def test_matching_digests_accepted(self) -> None:
        """Correct digests pass; upper-case hex is the same digest."""
        evidence.verify(self.root, expect(digest(SELECTION), digest(LEDGER)))
        evidence.verify(self.root, expect(digest(SELECTION).upper(), digest(LEDGER)))

    def test_mismatch_rejected(self) -> None:
        """A wrong digest for either file names the file."""
        with self.assertRaisesRegex(ValueError, "mismatch for ledger.json"):
            evidence.verify(self.root, expect(digest(SELECTION), digest(b"other")))
        with self.assertRaisesRegex(ValueError, "mismatch for selection.json"):
            evidence.verify(self.root, expect(digest(b"other"), digest(LEDGER)))

    def test_malformed_digest_rejected_before_reading(self) -> None:
        """A digest that is not 64 hex digits is refused outright."""
        for bad in ("", "abc", "g" * 64, digest(SELECTION) + "0"):
            with (
                self.subTest(bad=bad),
                self.assertRaisesRegex(ValueError, "64 hex digits"),
            ):
                evidence.verify(self.root, expect(bad, digest(LEDGER)))

    def test_bad_name_and_empty_list_rejected(self) -> None:
        """A path-like name or no expectations at all is a usage error."""
        for name in ("../x", "a/b", ".hidden", ""):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "plain file name"),
            ):
                evidence.verify(self.root, [(name, digest(SELECTION), LIMIT)])
        with self.assertRaisesRegex(ValueError, "at least one"):
            evidence.verify(self.root, [])

    def test_limit_applies_per_expectation(self) -> None:
        """A file over its own cap is refused even with the right digest."""
        with self.assertRaisesRegex(ValueError, "exceeds the 4-byte limit"):
            evidence.verify(self.root, [("ledger.json", digest(LEDGER), 4)])

    def test_missing_file_is_an_os_error(self) -> None:
        """An absent evidence file is an operational failure."""
        (self.root / "ledger.json").unlink()
        with self.assertRaises(OSError):
            evidence.verify(self.root, expect(digest(SELECTION), digest(LEDGER)))


class MainTest(unittest.TestCase):
    """The CLI exits 1 with a safe message on any verification failure."""

    def setUp(self) -> None:
        """A directory holding both sample files."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        (self.root / "selection.json").write_bytes(SELECTION)
        (self.root / "ledger.json").write_bytes(LEDGER)

    def argv(self, *expectations: str) -> list[str]:
        """A verify command line over the scratch directory."""
        argv = ["verify", "--directory", str(self.root)]
        for item in expectations:
            argv += ["--expect", item]
        return argv

    def test_failure_exits_one_with_message(self) -> None:
        """The message is prefixed and carries no workflow command syntax."""
        argv = self.argv(
            f"selection.json={digest(b'wrong')}", f"ledger.json={digest(LEDGER)}"
        )
        with (
            redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as caught,
        ):
            evidence.main(argv)
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("evidence: 'SHA-256 mismatch for selection.json'", err.getvalue())

    def test_malformed_expect_and_limit_exit_one(self) -> None:
        """An --expect without '=' or a non-positive limit is refused."""
        for argv in (
            self.argv("selection.json"),
            self.argv(f"selection.json={digest(SELECTION)}") + ["--limit-bytes", "0"],
        ):
            with (
                self.subTest(argv=argv),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as caught,
            ):
                evidence.main(argv)
            self.assertEqual(caught.exception.code, 1)

    def test_success_is_silent(self) -> None:
        """Matching digests exit normally, with a custom limit honoured."""
        argv = self.argv(
            f"selection.json={digest(SELECTION)}", f"ledger.json={digest(LEDGER)}"
        )
        evidence.main(argv)
        evidence.main(argv + ["--limit-bytes", "64"])


if __name__ == "__main__":
    unittest.main()
