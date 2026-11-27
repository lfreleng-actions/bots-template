# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""The pre-flight gate refuses drift in config, credentials, identity and tokens."""

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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
preflight = import_module("preflight")
github = import_module("bot_github")

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ID = "Iv23liB4s8f6cI7i0oQB"
# Built from the module's own markers so the fixture never contains a
# literal key header: a secret scanner would flag one, and the shape
# check is all that reads it.
PEM = "\n".join(
    [preflight.PEM_HEAD, *(["MIIEpAIBAAKCAQEA" + "x" * 60] * 25), preflight.PEM_TAIL]
)


def write_config(directory: Path, **overrides: Any) -> Path:
    """A bot.json with the given overrides applied to a valid base."""
    config: dict[str, Any] = {"app_slug": "lf-releng-code-review-bot"}
    config.update(overrides)
    path = directory / "bot.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


class ConfigTest(unittest.TestCase):
    """``check_config`` admits one lower-case slug and nothing unexpected."""

    def setUp(self) -> None:
        """A scratch directory for config files."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)

    def test_valid_config_returns_slug(self) -> None:
        """The slug comes back for the identity check."""
        with redirect_stdout(io.StringIO()):
            config = preflight.check_config(write_config(self.root))
        self.assertEqual(config["app_slug"], "lf-releng-code-review-bot")

    def test_invalid_configs_are_drift(self) -> None:
        """Missing file, bad JSON, wrong shape, bad slug and extra keys all refuse."""
        cases: list[tuple[str, Path]] = [("missing", self.root / "absent.json")]
        bad = self.root / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        cases.append(("not json", bad))
        array = self.root / "array.json"
        array.write_text("[]", encoding="utf-8")
        cases.append(("array", array))
        cases.append(("upper slug", write_config(self.root, app_slug="LF-Bot")))
        cases.append(("extra key", write_config(self.root, owner="someone")))
        for name, path in cases:
            with self.subTest(name=name), self.assertRaises(preflight.Drift):
                preflight.check_config(path)

    def test_forbidden_permissions_are_read_from_the_config(self) -> None:
        """A list of permission names comes back; absent means none."""
        names = ["administration", "organization_administration"]
        with redirect_stdout(io.StringIO()):
            listed = preflight.check_config(
                write_config(self.root, forbidden_permissions=names)
            )
            bare = preflight.check_config(write_config(self.root))
        self.assertEqual(preflight.forbidden_permissions(listed), names)
        self.assertEqual(preflight.forbidden_permissions(bare), [])

    def test_malformed_forbidden_permissions_are_drift(self) -> None:
        """Only a list of lower-case words joined by underscores passes."""
        with redirect_stdout(io.StringIO()):
            preflight.check_config(
                write_config(self.root, forbidden_permissions=["administration"])
            )
        for value in (
            "administration",
            {"administration": True},
            ["Administration"],
            ["pull-requests"],
            ["administration2"],
            ["_administration"],
            [""],
            [7],
            None,
        ):
            with self.subTest(value=value), self.assertRaises(preflight.Drift):
                preflight.check_config(
                    write_config(self.root, forbidden_permissions=value)
                )

    def test_template_config_forbids_administration(self) -> None:
        """The template ships with the grant no bot of this shape needs."""
        with redirect_stdout(io.StringIO()):
            config = preflight.check_config(ROOT / "config" / "bot.json")
        self.assertEqual(preflight.forbidden_permissions(config), ["administration"])


class CredentialTest(unittest.TestCase):
    """``check_credentials`` validates shape alone and never echoes a value."""

    def test_well_formed_credentials_pass(self) -> None:
        """A client id, a PEM key and a fine-grained PAT are accepted."""
        out = io.StringIO()
        with redirect_stdout(out):
            preflight.check_credentials(CLIENT_ID, PEM, "github_pat_abc")
        self.assertNotIn(CLIENT_ID, out.getvalue())
        self.assertNotIn("BEGIN", out.getvalue())

    def test_secretless_run_passes(self) -> None:
        """Empty credentials are a dry-run plumbing test, not drift."""
        with redirect_stdout(io.StringIO()):
            preflight.check_credentials("", "", "")

    def test_malformed_credentials_are_drift(self) -> None:
        """Each credential is checked against the shape it should have."""
        cases = [
            ("bad client id", ("ghp_notanappid", PEM, "")),
            ("id without key", (CLIENT_ID, "", "")),
            ("key not pem", (CLIENT_ID, "not a key", "")),
            (
                "key too short",
                (CLIENT_ID, f"{preflight.PEM_HEAD}\nx\n{preflight.PEM_TAIL}", ""),
            ),
            ("classic pat", ("", "", "ghp_classic")),
        ]
        for name, args in cases:
            with self.subTest(name=name), self.assertRaises(preflight.Drift):
                preflight.check_credentials(*args)


class EgressAndSkewTest(unittest.TestCase):
    """Block mode needs a pinned list; live runs need matching assets."""

    def test_block_mode_requires_pinned_and_loaded_list(self) -> None:
        """A commit-pinned coordinate and a non-empty list both matter."""
        sha = "@" + "a" * 40
        with redirect_stdout(io.StringIO()):
            preflight.check_egress("block", sha, "github.com:443")
            preflight.check_egress("audit", "", "")
        for policy, config, allow in [
            ("block", "@main", "github.com:443"),
            ("block", sha, ""),
            ("open", "", ""),
        ]:
            with (
                self.subTest(policy=policy, config=config),
                self.assertRaises(preflight.Drift),
            ):
                preflight.check_egress(policy, config, allow)

    def test_live_run_refuses_asset_skew(self) -> None:
        """Live runs execute the scripts of the workflow's own commit."""
        with redirect_stdout(io.StringIO()):
            preflight.check_live_skew(False, "a" * 40, "a" * 40)
            preflight.check_live_skew(True, "a" * 40, "b" * 40)
            preflight.check_live_skew(False, "", "b" * 40)
        with self.assertRaises(preflight.Drift):
            preflight.check_live_skew(False, "a" * 40, "b" * 40)


class IdentityAndTokenTest(unittest.TestCase):
    """The minted App and the token's grants are checked against expectations."""

    def test_identity_must_match_configured_slug(self) -> None:
        """A token from another App, or no slug at all, is drift."""
        with redirect_stdout(io.StringIO()):
            preflight.check_identity(
                "lf-releng-code-review-bot", "lf-releng-code-review-bot"
            )
        for minted in ("", "lf-releng-issues-triage-bot"):
            with self.subTest(minted=minted), self.assertRaises(preflight.Drift):
                preflight.check_identity("lf-releng-code-review-bot", minted)

    def test_read_token_must_not_push(self) -> None:
        """The repository probe's permissions decide."""
        with (
            patch.object(
                github,
                "api_object",
                return_value={
                    "permissions": {"pull": True, "push": False, "admin": False}
                },
            ),
            redirect_stdout(io.StringIO()),
        ):
            preflight.check_token_read_only("org/repo")
        for perms in (
            {"pull": True, "push": True},
            {"pull": True, "admin": True},
            {"pull": True, "maintain": True},
        ):
            with (
                self.subTest(perms=perms),
                patch.object(github, "api_object", return_value={"permissions": perms}),
                self.assertRaises(preflight.Drift),
            ):
                preflight.check_token_read_only("org/repo")
        with (
            patch.object(github, "api_object", return_value={}),
            self.assertRaises(preflight.Drift),
        ):
            preflight.check_token_read_only("org/repo")

    def test_probe_targets_a_repository_in_the_token_scope(self) -> None:
        """Without a named repository the installation listing picks the probe."""
        replies: dict[str, dict[str, Any]] = {
            "installation/repositories?per_page=1": {
                "repositories": [{"full_name": "org/in-scope"}]
            },
            "repos/org/in-scope": {"permissions": {"pull": True, "push": False}},
        }
        seen: list[str] = []

        def fake(endpoint: str) -> dict[str, Any]:
            seen.append(endpoint)
            return replies[endpoint]

        with (
            patch.object(github, "api_object", side_effect=fake),
            redirect_stdout(io.StringIO()),
        ):
            preflight.check_token_read_only()
        self.assertEqual(seen, list(replies))
        listings: list[dict[str, Any]] = [
            {"repositories": []},
            {},
            {"repositories": ["x"]},
        ]
        for listing in listings:
            with (
                patch.object(github, "api_object", return_value=listing),
                self.assertRaises(preflight.Drift),
            ):
                preflight.check_token_read_only()


class AppPermissionsTest(unittest.TestCase):
    """The App's own record must grant no forbidden permission at any level."""

    def test_forbidden_grant_at_any_level_is_drift(self) -> None:
        """Read and write both fail, naming the permission."""
        for level in ("read", "write"):
            with (
                self.subTest(level=level),
                self.assertRaises(preflight.Drift) as caught,
            ):
                preflight.check_app_permissions(
                    ["administration"],
                    {"administration": level, "contents": "write"},
                )
            self.assertIn("administration", str(caught.exception))

    def test_absent_grant_passes(self) -> None:
        """Other permissions, at any level, are not this check's business."""
        out = io.StringIO()
        with redirect_stdout(out):
            preflight.check_app_permissions(
                ["administration"], {"contents": "write", "metadata": "read"}
            )
            preflight.check_app_permissions([], {"administration": "write"})
        self.assertIn("preflight: ok", out.getvalue())


class WorkflowAuditTest(unittest.TestCase):
    """The checked-in workflows pass their own contracts and zizmor."""

    def test_contract_tests_pass_against_this_checkout(self) -> None:
        """The gate re-runs tests/test_workflow.py from the assets root."""
        with redirect_stdout(io.StringIO()):
            preflight.run_contract_tests(ROOT)

    def test_zizmor_findings_are_drift(self) -> None:
        """One unignored finding fails; ignored ones and none pass."""
        clean = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
        ignored = subprocess.CompletedProcess(
            [], 14, stdout='[{"ident": "x", "ignored": true}]', stderr=""
        )
        dirty = subprocess.CompletedProcess(
            [],
            14,
            stdout='[{"ident": "template-injection", "ignored": false}]',
            stderr="",
        )
        with patch.object(preflight.shutil, "which", return_value="/bin/zizmor"):
            for proc in (clean, ignored):
                with (
                    patch.object(subprocess, "run", return_value=proc),
                    redirect_stdout(io.StringIO()),
                ):
                    preflight.run_zizmor(ROOT)
            with (
                patch.object(subprocess, "run", return_value=dirty),
                self.assertRaises(preflight.Drift),
            ):
                preflight.run_zizmor(ROOT)
        with (
            patch.object(preflight.shutil, "which", return_value=None),
            self.assertRaises(preflight.Drift),
        ):
            preflight.run_zizmor(ROOT)


class CliTest(unittest.TestCase):
    """Drift exits one with an error annotation that carries no secret."""

    def test_config_command_reads_credentials_from_env(self) -> None:
        """A bad client id in the environment fails the config command."""
        with tempfile.TemporaryDirectory() as holder:
            config = write_config(Path(holder))
            env = {"BOT_APP_CLIENT_ID": "ghp_wrong", "BOT_APP_PRIVATE_KEY": PEM}
            err = io.StringIO()
            with (
                patch.dict(preflight.os.environ, env, clear=False),
                redirect_stdout(io.StringIO()),
                redirect_stderr(err),
                self.assertRaises(SystemExit) as caught,
            ):
                preflight.main(
                    ["config", "--config", str(config), "--egress-policy", "audit"]
                )
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("::error::preflight:", err.getvalue())
        self.assertNotIn("ghp_wrong", err.getvalue())

    def run_app_permissions(
        self, env: dict[str, str], grants: Any, **config: Any
    ) -> tuple[int | str | None, str, list[tuple[str, str]]]:
        """Run app-permissions with the App record stubbed.

        Return the exit code, everything printed, and the credentials
        each read of the record was given. ``grants`` is the record's
        permissions, or an exception the read raises.
        """
        out = io.StringIO()
        calls: list[tuple[str, str]] = []
        code: int | str | None = 0

        def read(client_id: str, private_key: str) -> Any:
            calls.append((client_id, private_key))
            if isinstance(grants, Exception):
                raise grants
            return grants

        with tempfile.TemporaryDirectory() as holder:
            path = write_config(Path(holder), **config)
            with (
                patch.dict(preflight.os.environ, env, clear=True),
                patch.object(preflight.bot_app, "app_permissions", side_effect=read),
                redirect_stdout(out),
                redirect_stderr(out),
            ):
                try:
                    preflight.main(["app-permissions", "--config", str(path)])
                except SystemExit as exc:
                    code = exc.code
        return code, out.getvalue(), calls

    def test_app_permissions_command_refuses_a_forbidden_grant(self) -> None:
        """Exit one naming the grant; the key is read, never printed."""
        env = {"BOT_APP_CLIENT_ID": CLIENT_ID, "BOT_APP_PRIVATE_KEY": PEM}
        code, output, calls = self.run_app_permissions(
            env, {"administration": "read"}, forbidden_permissions=["administration"]
        )
        self.assertEqual(code, 1)
        self.assertIn("::error::preflight:", output)
        self.assertIn("administration", output)
        self.assertEqual(calls, [(CLIENT_ID, PEM)])
        for secret in (PEM, "MIIEpAIBAAKCAQEA", CLIENT_ID):
            self.assertNotIn(secret, output)

    def test_app_permissions_command_passes_a_clean_app(self) -> None:
        """An App without the grant passes and says what it checked."""
        env = {"BOT_APP_CLIENT_ID": CLIENT_ID, "BOT_APP_PRIVATE_KEY": PEM}
        code, output, calls = self.run_app_permissions(
            env, {"contents": "write"}, forbidden_permissions=["administration"]
        )
        self.assertEqual(code, 0)
        self.assertIn("preflight: ok", output)
        self.assertEqual(len(calls), 1)

    def test_app_permissions_command_fails_closed_on_a_failed_read(self) -> None:
        """A record that cannot be read fails the run, naming no secret."""
        env = {"BOT_APP_CLIENT_ID": CLIENT_ID, "BOT_APP_PRIVATE_KEY": PEM}
        code, output, _ = self.run_app_permissions(
            env,
            preflight.bot_app.AppError("GET /app answered HTTP 401"),
            forbidden_permissions=["administration"],
        )
        self.assertEqual(code, 1)
        self.assertIn("::error::preflight:", output)
        self.assertIn("HTTP 401", output)
        self.assertNotIn("MIIEpAIBAAKCAQEA", output)

    def test_app_permissions_command_needs_both_credentials(self) -> None:
        """Without a client id and a key there is no record to read."""
        for env in (
            {"BOT_APP_PRIVATE_KEY": PEM},
            {"BOT_APP_CLIENT_ID": CLIENT_ID},
        ):
            with self.subTest(env=sorted(env)):
                code, output, calls = self.run_app_permissions(
                    env, {}, forbidden_permissions=["administration"]
                )
                self.assertEqual(code, 1)
                self.assertIn("::error::preflight:", output)
                self.assertEqual(calls, [])

    def test_app_permissions_command_with_nothing_forbidden_reads_nothing(
        self,
    ) -> None:
        """A bot that forbids nothing makes no request and needs no key."""
        code, output, calls = self.run_app_permissions({}, {"administration": "write"})
        self.assertEqual(code, 0)
        self.assertIn("preflight: ok", output)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
