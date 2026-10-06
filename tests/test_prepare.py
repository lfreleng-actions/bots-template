# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""The placeholder selection writes evidence of the right shape."""

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

prepare = import_module("prepare")


class ExclusionsTest(unittest.TestCase):
    """``load_exclusions`` reads the bundled list unless the input overrides it."""

    def setUp(self) -> None:
        """A bundled exclusion file with comments and blanks."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.path = Path(holder.name) / "excluded-repos.txt"
        self.path.write_text("# header\n\nalpha\nbeta  # trailing\n", encoding="utf-8")

    def test_file_names_are_read_without_comments(self) -> None:
        """Comment and blank lines are not repository names."""
        self.assertEqual(prepare.load_exclusions(self.path, ""), {"alpha", "beta"})

    def test_input_overrides_the_file(self) -> None:
        """A non-empty input replaces the bundled list entirely."""
        self.assertEqual(
            prepare.load_exclusions(self.path, "gamma, delta epsilon"),
            {"gamma", "delta", "epsilon"},
        )

    def test_bundled_file_parses(self) -> None:
        """The repository's own list is readable and non-empty."""
        bundled = Path(__file__).resolve().parents[1] / "config" / "excluded-repos.txt"
        self.assertTrue(prepare.load_exclusions(bundled, ""))


class ShapeTest(unittest.TestCase):
    """The matrix and summary follow the selection."""

    def test_matrix_projects_the_identity_keys_alone(self) -> None:
        """Extra selection fields never reach the matrix."""
        target: dict[str, Any] = {
            "key": "repo-1",
            "repository": "o/repo",
            "repo_name": "repo",
            "number": 1,
            "head_sha": "a" * 40,
            "title": "dropped",
        }
        matrix = prepare.matrix_for([target])
        self.assertEqual(set(matrix["include"][0]), set(prepare.TARGET_KEYS))

    def test_placeholder_selects_nothing(self) -> None:
        """The template's selection is empty whatever it is given."""
        self.assertEqual(prepare.select_targets("o", ["r"], {"x"}, 5), [])

    def test_summary_names_mode_and_targets(self) -> None:
        """The step summary says dry run or live and lists each target."""
        selection: dict[str, Any] = {
            "dry_run": False,
            "targets": [{"repository": "o/r", "number": 7}],
        }
        text = prepare.summary_for(selection)
        self.assertIn("(live)", text)
        self.assertIn("`o/r#7`", text)


class MainTest(unittest.TestCase):
    """The CLI writes three files or refuses bad arguments."""

    def setUp(self) -> None:
        """A scratch directory with a ledger and an exclusion file."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        (self.root / "ledger.json").write_text('{"schema": 1, "entries": []}\n')
        (self.root / "excluded.txt").write_text("alpha\n")

    def argv(self, *extra: str, ledger: str = "ledger.json") -> list[str]:
        """A complete command line over the scratch directory."""
        return [
            "--org",
            "lfreleng-actions",
            "--output-dir",
            str(self.root / "out"),
            "--model",
            "claude-opus-5.5",
            "--ledger",
            str(self.root / ledger),
            "--exclude-file",
            str(self.root / "excluded.txt"),
            *extra,
        ]

    def test_writes_selection_matrix_and_summary(self) -> None:
        """An empty selection still yields every file the workflow reads."""
        with redirect_stdout(io.StringIO()):
            prepare.main(self.argv("--dry-run"))
        out = self.root / "out"
        selection = json.loads((out / "selection.json").read_text())
        self.assertEqual(selection["schema"], 1)
        self.assertEqual(selection["org"], "lfreleng-actions")
        self.assertTrue(selection["dry_run"])
        self.assertEqual(selection["targets"], [])
        self.assertEqual(json.loads((out / "matrix.json").read_text()), {"include": []})
        self.assertIn("0 target(s)", (out / "selection-summary.md").read_text())

    def test_negative_cap_and_missing_ledger_refused(self) -> None:
        """Bad arguments exit 1 before anything is written."""
        for argv in (
            self.argv("--max-targets", "-1"),
            self.argv(ledger="absent.json"),
        ):
            with (
                self.subTest(argv=argv),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as caught,
            ):
                prepare.main(argv)
            self.assertEqual(caught.exception.code, 1)
        self.assertFalse((self.root / "out").exists())


if __name__ == "__main__":
    unittest.main()
