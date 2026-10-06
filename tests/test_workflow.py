# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Structural contracts the workflows must keep.

The reusable workflow splits trust between jobs: the agent job holds
the model credential alone; the trusted jobs hold the App key and
make every write. These tests pin that boundary, the provenance chain
(pinned assets, evidence verified by digest before use) and the shape
the callers rely on. They read workflow structure, never comments.

``scripts/preflight.py workflow`` runs this module again inside the
prepare job, so a workflow edit that breaks a contract stops the
pipeline before any session starts.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any, ClassVar, cast

import yaml  # pyright: ignore[reportMissingModuleSource]

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
REUSABLE = WORKFLOWS / "bot.yaml"
CRON = WORKFLOWS / "bot-cron.yaml"
TESTING = WORKFLOWS / "testing.yaml"

# PyYAML reads the bare `on:` key as boolean true.
TRIGGERS = True

HARDEN_RUNNER = "step-security/harden-runner"
BLOCK_ACTION = "lfreleng-actions/harden-runner-block-action"
CHECKOUT = "actions/checkout"
APP_TOKEN = "actions/create-github-app-token"
DOWNLOAD = "actions/download-artifact"
UPLOAD = "actions/upload-artifact"
REUSABLE_CALL = "$/.github/workflows/bot.yaml"
TRUSTED_JOBS = ("prepare", "apply", "report")
CALLER_PERMISSIONS = {"contents": "read", "actions": "read"}

COMMIT_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
SECRET_REFERENCE = re.compile(r"secrets\.[A-Za-z0-9_]+")
ALLOW_CONFIG = re.compile(r"^@[0-9a-f]{40}$")


def squash(text: str) -> str:
    """Collapse runs of whitespace so block scalars compare line-wrap free."""
    return re.sub(r"\s+", " ", text).strip()


def flatten(script: str) -> str:
    """Join backslash continuations, then squash, for shell substring checks."""
    return squash(re.sub(r"\\\n\s*", " ", script))


def uses_of(step: dict[str, Any]) -> str:
    """Return the action coordinate of a step, or an empty string."""
    return str(step.get("uses", ""))


def is_action(step: dict[str, Any], action: str) -> bool:
    """Match an action invocation independently of its commit pin."""
    return uses_of(step).startswith(action + "@")


def dumped(node: object) -> str:
    """Serialise a YAML subtree so a regex can scan every value in it."""
    return json.dumps(node, sort_keys=True)


class WorkflowCase(unittest.TestCase):
    """Read workflow structure, never comments."""

    WORKFLOW: ClassVar[Path]

    def setUp(self) -> None:
        """Load a fresh document for each contract."""
        loaded = yaml.safe_load(self.WORKFLOW.read_text(encoding="utf-8"))
        self.workflow: dict[str | bool, Any] = cast(dict[str | bool, Any], loaded)
        self.jobs: dict[str, dict[str, Any]] = cast(
            dict[str, dict[str, Any]], self.workflow["jobs"]
        )
        self.triggers: dict[str, Any] = cast(dict[str, Any], self.workflow[TRIGGERS])

    def steps(self, job: str) -> list[dict[str, Any]]:
        """Return the step list of a job that runs on a runner."""
        return cast(list[dict[str, Any]], self.jobs[job]["steps"])

    def step(self, job: str, identity: str) -> dict[str, Any]:
        """Find exactly one step by id or name."""
        matches = [
            s for s in self.steps(job) if identity in (s.get("id"), s.get("name"))
        ]
        self.assertEqual(len(matches), 1, f"{job}: expected one {identity!r} step")
        return matches[0]

    def position(self, job: str, identity: str) -> int:
        """Return the index of a step so ordering contracts can compare."""
        return self.steps(job).index(self.step(job, identity))

    def actions(self, job: str, action: str) -> list[dict[str, Any]]:
        """Select action invocations independently of their commit pin."""
        return [s for s in self.steps(job) if is_action(s, action)]

    def action(self, job: str, action: str) -> dict[str, Any]:
        """Return the single invocation of an action within a job."""
        matches = self.actions(job, action)
        self.assertEqual(len(matches), 1, f"{job}: expected one {action} step")
        return matches[0]

    def assert_pinned_steps(self) -> None:
        """Every step-level `uses:` names a 40-hex commit, never a tag or branch."""
        for job in self.jobs:
            if "steps" not in self.jobs[job]:
                continue
            for step in self.steps(job):
                if "uses" not in step:
                    continue
                with self.subTest(job=job, uses=step["uses"]):
                    self.assertRegex(uses_of(step), COMMIT_PIN)


class ReusableWorkflowCase(WorkflowCase):
    """Shared fixture for the reusable workflow contracts."""

    WORKFLOW = REUSABLE


class TopLevelContracts(ReusableWorkflowCase):
    """Permissions, concurrency, pinning and runner hardening."""

    def test_default_permissions_are_empty(self) -> None:
        """Every job declares its own grants from a zero baseline."""
        self.assertEqual(self.workflow["permissions"], {})

    def test_concurrency_separates_dry_runs_from_live_runs(self) -> None:
        """Dry runs lock within their own run; live runs share one group."""
        group = squash(str(self.workflow["concurrency"]["group"]))
        self.assertIn("inputs.dry_run", group)
        self.assertIn("github.run_id", group)
        self.assertIn("github.repository", group)
        self.assertFalse(self.workflow["concurrency"]["cancel-in-progress"])

    def test_every_action_is_pinned_to_a_commit(self) -> None:
        """No tag or branch reference anywhere in the reusable workflow."""
        self.assert_pinned_steps()

    def test_every_checkout_drops_credentials(self) -> None:
        """No job leaves the native token in a checked-out tree."""
        for job in self.jobs:
            for step in self.actions(job, CHECKOUT):
                with self.subTest(job=job, step=step.get("name")):
                    self.assertIs(step["with"]["persist-credentials"], False)

    def test_harden_runner_leads_every_job(self) -> None:
        """Trusted jobs load the allow-list, then harden; the agent job audits."""
        for job in TRUSTED_JOBS:
            steps = self.steps(job)
            self.assertTrue(is_action(steps[0], BLOCK_ACTION), job)
            self.assertEqual(steps[0]["if"], "inputs.egress_policy == 'block'")
            self.assertTrue(is_action(steps[1], HARDEN_RUNNER), job)
            self.assertEqual(
                steps[1]["with"]["egress-policy"], "${{ inputs.egress_policy }}"
            )
        agent = self.steps("agent")
        self.assertTrue(is_action(agent[0], HARDEN_RUNNER))
        self.assertEqual(agent[0]["with"]["egress-policy"], "audit")
        self.assertEqual(self.actions("agent", BLOCK_ACTION), [])


class PrepareContracts(ReusableWorkflowCase):
    """The trusted selection job and its pre-flight gate."""

    def test_live_runs_require_the_app(self) -> None:
        """A live run without a client id fails before anything is minted."""
        gate = self.step("prepare", "Require App for live runs")
        self.assertIn("!inputs.dry_run", gate["if"])
        self.assertIn("inputs.github_app_client_id == ''", gate["if"])

    def test_read_token_is_read_only(self) -> None:
        """The prepare mint grants reads and nothing else."""
        mint = self.action("prepare", APP_TOKEN)
        self.assertEqual(mint["if"], "inputs.github_app_client_id != ''")
        grants = {k: v for k, v in mint["with"].items() if k.startswith("permission-")}
        self.assertEqual(set(grants.values()), {"read"})

    def test_gate_runs_after_pinning_and_before_the_read_mint(self) -> None:
        """Pin assets, audit the workflow, check config, mint, then verify identity."""
        order = [
            self.position("prepare", "Pin assets commit"),
            self.position("prepare", "Pre-flight: workflow contracts and audit"),
            self.position("prepare", "Pre-flight: configuration and credentials"),
            self.position("prepare", "app-token"),
            self.position("prepare", "Pre-flight: App identity and token grants"),
        ]
        self.assertEqual(order, sorted(order))

    def test_config_gate_sees_the_credentials_under_template_names(self) -> None:
        """The gate checks the same names every bot repository uses."""
        env = self.step("prepare", "Pre-flight: configuration and credentials")["env"]
        self.assertEqual(env["BOT_APP_CLIENT_ID"], "${{ inputs.github_app_client_id }}")
        self.assertEqual(
            env["BOT_APP_PRIVATE_KEY"], "${{ secrets.github_app_private_key }}"
        )
        self.assertEqual(env["COPILOT_GITHUB_TOKEN"], "${{ secrets.copilot_token }}")
        self.assertEqual(env["ASSETS_SHA"], "${{ steps.pinned.outputs.sha }}")

    def test_zizmor_is_pinned(self) -> None:
        """The audit tool installs at one exact version."""
        run = self.step("prepare", "Install zizmor")["run"]
        self.assertRegex(run, r"zizmor==\d+\.\d+\.\d+")

    def test_ledger_comes_from_the_native_token_before_selection(self) -> None:
        """The ledger fetch reads this repository's artifacts with the native token."""
        fetch = self.step("prepare", "Fetch prior ledger")
        self.assertEqual(fetch["env"]["GH_TOKEN"], "${{ github.token }}")
        self.assertLess(
            self.position("prepare", "Fetch prior ledger"),
            self.position("prepare", "Prepare evidence"),
        )

    def test_skip_agent_and_empty_selection_stop_the_sessions(self) -> None:
        """run_agent is false when the plumbing test asks or nothing was chosen."""
        run = self.step("prepare", "chosen")["run"]
        self.assertIn('[ "$SKIP_AGENT" != true ] && [ "$count" != 0 ]', run)
        self.assertEqual(
            self.jobs["agent"]["if"], "needs.prepare.outputs.run_agent == 'true'"
        )

    def test_prepare_publishes_the_full_provenance_set(self) -> None:
        """Downstream jobs receive the digests, the evidence id and the pin."""
        outputs = self.jobs["prepare"]["outputs"]
        self.assertEqual(outputs["assets_sha"], "${{ steps.pinned.outputs.sha }}")
        self.assertEqual(
            outputs["evidence_id"], "${{ steps.evidence.outputs.artifact-id }}"
        )
        self.assertEqual(
            outputs["selection_sha256"], "${{ steps.digests.outputs.selection }}"
        )
        self.assertEqual(
            outputs["ledger_sha256"], "${{ steps.digests.outputs.ledger }}"
        )
        evidence = self.step("prepare", "evidence")
        self.assertTrue(is_action(evidence, UPLOAD))
        self.assertEqual(evidence["with"]["if-no-files-found"], "error")


class MintProvenanceContracts(ReusableWorkflowCase):
    """Every App token is minted for the trusted owner input."""

    def test_every_mint_owner_is_the_trusted_org_input(self) -> None:
        """No mint takes its owner from the matrix, the packet or the agent."""
        mints = [
            (job, step) for job in self.jobs for step in self.actions(job, APP_TOKEN)
        ]
        self.assertEqual({job for job, _ in mints}, {"prepare", "apply"})
        for job, mint in mints:
            with self.subTest(job=job):
                self.assertEqual(mint["with"]["owner"], "${{ inputs.org }}")
                self.assertEqual(
                    mint["with"]["client-id"], "${{ inputs.github_app_client_id }}"
                )
                self.assertEqual(
                    mint["with"]["private-key"], "${{ secrets.github_app_private_key }}"
                )

    def test_write_mint_scope_is_one_recorded_repository(self) -> None:
        """The write token covers the matrix entry's repository and no other."""
        mint = self.action("apply", APP_TOKEN)
        self.assertEqual(mint["with"]["repositories"], "${{ matrix.repo_name }}")


class AgentContracts(ReusableWorkflowCase):
    """The untrusted session job holds the model credential and nothing else."""

    def test_agent_holds_no_app_key(self) -> None:
        """No mint, no key reference, and copilot_token is the one secret."""
        self.assertEqual(self.actions("agent", APP_TOKEN), [])
        text = dumped(self.jobs["agent"])
        self.assertNotIn("github_app_private_key", text)
        self.assertEqual(set(SECRET_REFERENCE.findall(text)), {"secrets.copilot_token"})

    def test_agent_permissions_are_read_only(self) -> None:
        """The agent job checks out the prompt and holds no other grant."""
        self.assertEqual(self.jobs["agent"]["permissions"], {"contents": "read"})

    def test_budget_and_fan_out_come_from_prepare(self) -> None:
        """Timeouts and parallelism are trusted outputs, never inputs read here."""
        agent = self.jobs["agent"]
        self.assertEqual(
            agent["timeout-minutes"],
            "${{ fromJSON(needs.prepare.outputs.job_timeout) }}",
        )
        self.assertEqual(
            agent["strategy"]["max-parallel"],
            "${{ fromJSON(needs.prepare.outputs.max_parallel) }}",
        )
        self.assertEqual(
            agent["strategy"]["matrix"], "${{ fromJSON(needs.prepare.outputs.matrix) }}"
        )
        self.assertFalse(agent["strategy"]["fail-fast"])

    def test_evidence_arrives_by_id_and_is_verified_before_use(self) -> None:
        """Every consumer downloads by artifact id and checks digests first."""
        for job in ("agent", "apply", "report"):
            with self.subTest(job=job):
                fetch = self.step(job, "Fetch selection evidence")
                self.assertTrue(is_action(fetch, DOWNLOAD))
                self.assertEqual(
                    fetch["with"]["artifact-ids"],
                    "${{ needs.prepare.outputs.evidence_id }}",
                )
                verify = self.step(job, "Verify evidence bytes")
                self.assertEqual(
                    verify["env"]["SELECTION_SHA"],
                    "${{ needs.prepare.outputs.selection_sha256 }}",
                )
                self.assertIn("bot_evidence.py verify", flatten(verify["run"]))
                self.assertEqual(
                    self.position(job, "Verify evidence bytes"),
                    self.position(job, "Fetch selection evidence") + 1,
                )

    def test_prompt_comes_from_the_pinned_assets(self) -> None:
        """The session prompt is the trusted prompt file plus the runtime context."""
        packet = self.step("agent", "packet")
        self.assertIn("cat bot-assets/prompt/task.md", packet["run"])
        self.assertIn("## Runtime context", packet["run"])

    def test_agent_invocation_is_confined(self) -> None:
        """The session takes a model PAT alone and times out from the budget."""
        run = self.step("agent", "agent")
        self.assertEqual(
            run["timeout-minutes"],
            "${{ fromJSON(needs.prepare.outputs.agent_timeout) }}",
        )
        self.assertEqual(
            run["env"]["COPILOT_GITHUB_TOKEN"], "${{ secrets.copilot_token }}"
        )
        self.assertIn("github_pat_*)", run["run"])

    def test_session_artifact_is_stable_per_key(self) -> None:
        """One name per target; a rerun overwrites its own earlier session."""
        upload = self.step("agent", "Preserve session output")
        self.assertTrue(is_action(upload, UPLOAD))
        self.assertEqual(
            squash(upload["with"]["name"]),
            "bot-session-${{ needs.prepare.outputs.namespace }}-${{ matrix.key }}",
        )
        self.assertTrue(upload["with"]["overwrite"])
        self.assertIn("always()", upload["if"])

    def test_cleanup_never_gates_apply(self) -> None:
        """Scratch deletion runs always and cannot fail the job."""
        cleanup = self.step("agent", "Clear session scratch files")
        self.assertEqual(cleanup["if"], "always()")
        self.assertTrue(cleanup["continue-on-error"])


class ApplyContracts(ReusableWorkflowCase):
    """The trusted job that corroborates a verdict and makes the one write."""

    def test_apply_runs_for_every_entry_after_the_agent(self) -> None:
        """Failed agent entries still get a verdict; cancellation stops it."""
        apply = self.jobs["apply"]
        self.assertEqual(apply["needs"], ["prepare", "agent"])
        condition = squash(apply["if"])
        self.assertIn("!cancelled()", condition)
        self.assertIn("needs.prepare.result == 'success'", condition)
        self.assertFalse(apply["strategy"]["fail-fast"])

    def test_evidence_is_verified_before_the_session_is_fetched(self) -> None:
        """Provenance, evidence, verification, session, offline check, mint."""
        order = [
            self.position("apply", "Require trusted provenance"),
            self.position("apply", "Fetch selection evidence"),
            self.position("apply", "Verify evidence bytes"),
            self.position("apply", "fetch"),
            self.position("apply", "check"),
            self.position("apply", "write-token"),
            self.position("apply", "Pre-flight: write token identity"),
            self.position("apply", "Apply verdict"),
        ]
        self.assertEqual(order, sorted(order))
        fetch = self.step("apply", "fetch")
        self.assertEqual(fetch["env"]["GH_TOKEN"], "${{ github.token }}")

    def test_write_token_is_minted_on_the_live_path_alone(self) -> None:
        """The mint needs a live run, an approvable verdict and a client id."""
        mint = self.step("apply", "write-token")
        condition = squash(mint["if"])
        self.assertIn("!inputs.dry_run", condition)
        self.assertIn("steps.check.outputs.approvable == 'true'", condition)
        self.assertIn("inputs.github_app_client_id != ''", condition)

    def test_every_entry_uploads_a_typed_result(self) -> None:
        """A result artifact per entry per attempt, kept for the ledger window."""
        self.assertEqual(self.step("apply", "Ensure a result exists")["if"], "always()")
        upload = self.step("apply", "Attach apply result")
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(
            squash(upload["with"]["name"]),
            "bot-result-${{ needs.prepare.outputs.namespace }}"
            "-${{ matrix.key }}-${{ github.run_attempt }}",
        )
        self.assertEqual(upload["with"]["retention-days"], 90)


class ReportContracts(ReusableWorkflowCase):
    """The trusted job that gathers results and publishes the ledger."""

    def test_report_gathers_namespaced_results(self) -> None:
        """The report sees this invocation's results and no other's."""
        gather = self.step("report", "Gather results")
        self.assertTrue(is_action(gather, DOWNLOAD))
        self.assertEqual(
            gather["with"]["pattern"],
            "bot-result-${{ needs.prepare.outputs.namespace }}-*",
        )

    def test_ledger_artifact_has_a_fixed_name_and_long_retention(self) -> None:
        """The next run finds the ledger by name; it outlives the evidence."""
        ledger = self.step("report", "Publish ledger")
        self.assertEqual(ledger["with"]["name"], "bot-ledger")
        self.assertEqual(ledger["with"]["retention-days"], 90)
        self.assertEqual(ledger["with"]["if-no-files-found"], "error")


class InputContracts(ReusableWorkflowCase):
    """The declared interface the callers depend on."""

    def test_declared_inputs_and_secrets(self) -> None:
        """The input and secret names are the set the callers pass."""
        call = self.triggers["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {
                "org",
                "dry_run",
                "model",
                "repositories",
                "exclude_repos",
                "max_targets",
                "max_concurrent_agents",
                "max_runtime_minutes",
                "skip_agent",
                "egress_policy",
                "egress_allow_config",
                "github_app_client_id",
                "assets_repository",
                "assets_ref",
            },
        )
        self.assertEqual(
            set(call["secrets"]), {"copilot_token", "github_app_private_key"}
        )
        for secret in call["secrets"].values():
            self.assertFalse(secret["required"])

    def test_input_defaults(self) -> None:
        """Safe defaults: dry, blocked egress, no App, the agent runs."""
        inputs = self.triggers["workflow_call"]["inputs"]
        self.assertTrue(inputs["org"]["required"])
        self.assertTrue(inputs["dry_run"]["default"])
        self.assertFalse(inputs["skip_agent"]["default"])
        self.assertEqual(inputs["egress_policy"]["default"], "block")
        self.assertEqual(inputs["github_app_client_id"]["default"], "")
        self.assertEqual(inputs["max_targets"]["default"], "20")
        self.assertEqual(inputs["assets_ref"]["default"], "")


class CronContracts(WorkflowCase):
    """The scheduled and dispatched caller."""

    WORKFLOW = CRON

    def test_dispatch_stays_dry_run_unless_the_operator_says_otherwise(self) -> None:
        """Scheduled runs pass dry_run true; a dispatch takes the operator's choice."""
        call = self.jobs["bot"]
        self.assertEqual(call["uses"], REUSABLE_CALL)
        self.assertEqual(call["with"]["org"], "${{ github.repository_owner }}")
        self.assertEqual(
            squash(str(call["with"]["dry_run"])),
            "${{ github.event_name != 'workflow_dispatch' || inputs.dry_run }}",
        )

    def test_dispatch_form(self) -> None:
        """The form offers the operator the template's tunables."""
        inputs = self.triggers["workflow_dispatch"]["inputs"]
        self.assertEqual(
            set(inputs), {"dry_run", "model", "repositories", "max_targets"}
        )
        self.assertTrue(inputs["dry_run"]["default"])
        self.assertEqual(inputs["model"]["default"], "Claude Opus 5.5")

    def test_model_display_names_map_to_identifiers(self) -> None:
        """Every choice in the form has a case arm mapping it to a CLI identifier."""
        options = self.triggers["workflow_dispatch"]["inputs"]["model"]["options"]
        run = self.step("options", "Map display name to identifier")["run"]
        for name in options:
            self.assertIn(f"'{name}') id='", run)
        self.assertEqual(
            self.jobs["bot"]["with"]["model"], "${{ needs.options.outputs.model }}"
        )

    def test_caller_forwards_credentials_and_pins_assets(self) -> None:
        """The template-named credentials reach the workflow; assets pin to the run."""
        call = self.jobs["bot"]
        self.assertEqual(
            call["with"]["github_app_client_id"], "${{ vars.BOT_APP_CLIENT_ID || '' }}"
        )
        self.assertEqual(
            call["secrets"],
            {
                "copilot_token": "${{ secrets.COPILOT_CLI_TOKEN }}",
                "github_app_private_key": "${{ secrets.BOT_APP_PRIVATE_KEY }}",
            },
        )
        self.assertEqual(call["with"]["assets_repository"], "${{ github.repository }}")
        self.assertEqual(call["with"]["assets_ref"], "${{ github.sha }}")
        self.assertEqual(call["with"]["egress_policy"], "block")
        self.assertRegex(call["with"]["egress_allow_config"], ALLOW_CONFIG)
        self.assertEqual(call["permissions"], CALLER_PERMISSIONS)

    def test_concurrency_keeps_dry_dispatches_out_of_the_live_group(self) -> None:
        """A dry dispatch never queues behind or cancels the schedule."""
        self.assertEqual(self.workflow["permissions"], {})
        group = squash(str(self.workflow["concurrency"]["group"]))
        self.assertIn("inputs.dry_run", group)
        self.assertIn("'bot'", group)
        self.assertFalse(self.workflow["concurrency"]["cancel-in-progress"])

    def test_caller_steps_are_pinned(self) -> None:
        """The options job's actions name commits and the job hardens first."""
        self.assert_pinned_steps()
        self.assertTrue(is_action(self.steps("options")[0], HARDEN_RUNNER))


class TestingContracts(WorkflowCase):
    """The pull request plumbing, the manual dry-run and the regression job."""

    WORKFLOW = TESTING

    def test_plumbing_is_secretless_selection_of_the_pr_head(self) -> None:
        """PR runs skip the agent, pass no secrets, and run the PR head's assets."""
        plumbing = self.jobs["plumbing"]
        self.assertEqual(plumbing["uses"], REUSABLE_CALL)
        self.assertEqual(plumbing["if"], "github.event_name == 'pull_request'")
        self.assertNotIn("secrets", plumbing)
        self.assertTrue(plumbing["with"]["dry_run"])
        self.assertTrue(plumbing["with"]["skip_agent"])
        self.assertEqual(plumbing["permissions"], CALLER_PERMISSIONS)
        self.assertEqual(
            squash(str(plumbing["with"]["assets_repository"])),
            "${{ github.event.pull_request.head.repo.full_name }}",
        )
        self.assertEqual(
            plumbing["with"]["assets_ref"], "${{ github.event.pull_request.head.sha }}"
        )
        self.assertEqual(
            plumbing["strategy"]["matrix"]["invocation"], ["first", "second"]
        )

    def test_manual_dry_run_holds_the_model_credential_alone(self) -> None:
        """The dispatch path runs the agent dry with no App key."""
        dry = self.jobs["dry-run"]
        self.assertEqual(dry["uses"], REUSABLE_CALL)
        self.assertEqual(dry["if"], "github.event_name == 'workflow_dispatch'")
        self.assertTrue(dry["with"]["dry_run"])
        self.assertEqual(
            dry["secrets"], {"copilot_token": "${{ secrets.COPILOT_CLI_TOKEN }}"}
        )
        self.assertNotIn("github_app_client_id", dry["with"])
        self.assertEqual(
            dry["with"]["repositories"], "${{ inputs.repositories || '' }}"
        )
        self.assertEqual(dry["with"]["max_targets"], "${{ inputs.max_targets || '2' }}")

    def test_regression_runs_the_locked_unittest_suite(self) -> None:
        """The offline suite runs from the lockfile with read-only permissions."""
        regression = self.jobs["regression"]
        self.assertEqual(regression["permissions"], {"contents": "read"})
        run = self.step("regression", "Run offline regression suite")["run"]
        self.assertIn("uv run --locked python -B -m unittest discover -s tests", run)

    def test_caller_steps_are_pinned(self) -> None:
        """Runner steps name commits and every checkout drops credentials."""
        self.assert_pinned_steps()
        for job in ("regression", "docs"):
            for step in self.actions(job, CHECKOUT):
                self.assertIs(step["with"]["persist-credentials"], False)


if __name__ == "__main__":
    unittest.main()
