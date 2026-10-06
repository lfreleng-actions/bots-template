<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# Customising the Template

A practical walkthrough of turning the template into a bot: what to
keep, what to adapt, what to replace and what to delete, and how to
extend the shared pieces without breaking the contracts the
[design](DESIGN.md) describes. Read the design's sections 2 and 3
first; every rule below follows from them.

## File by file

<!-- markdownlint-disable MD013 -->

| Path                                                       | Action      | Notes                                                                                                                                                 |
| ---------------------------------------------------------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.github/workflows/bot.yaml`                               | **Adapt**   | Rename; change the `bot-` artifact prefix; edit the four steps marked `Replace`; set the write mint's permission; add read grants to the read mint    |
| `.github/workflows/bot-cron.yaml`                          | **Adapt**   | Rename; uncomment and set the schedule; add dispatch inputs and forward them; the header comment lists the steps                                      |
| `.github/workflows/testing.yaml`                           | **Keep**    | Forward any new input the manual dry run should take; keep the pull request legs secretless                                                           |
| `.github/workflows/documentation.yaml`                     | **Keep**    | Deploys `docs/` to Pages from `main`                                                                                                                  |
| `.github/workflows/release*.yaml`                          | **Keep**    | Release drafter and tag release                                                                                                                       |
| `.github/workflows/openssf-scorecard.yaml`                 | **Keep**    | Scorecard badge                                                                                                                                       |
| `prompt/task.md`                                           | **Replace** | Fill every `replace` marker; keep the trust framing, the tool list and the verdict block                                                              |
| `config/bot.json`                                          | **Replace** | The bot's App slug; `description` is optional and the gate refuses any other key                                                                      |
| `config/excluded-repos.txt`                                | **Adapt**   | The repositories the scan skips                                                                                                                       |
| `tools/copilot-cli/`                                       | **Keep**    | Bump the CLI by regenerating the lockfile in a reviewed pull request                                                                                  |
| `scripts/prepare.py`                                       | **Replace** | `select_targets`; keep the arguments, the three output files and the five target keys                                                                 |
| `scripts/apply.py`                                         | **Replace** | `perform_write` and the bot's corroboration in `check_verdict`; keep `approvable=true\|false` on stdout and the `check.json` and `result.json` shapes |
| `scripts/report.py`                                        | **Adapt**   | `row_for` and `describe` for the bot's columns; the ledger entries stay as `ledger.py` defines them                                                   |
| `scripts/bot_github.py`                                    | **Keep**    | Shared, verbatim                                                                                                                                      |
| `scripts/bot_evidence.py`                                  | **Keep**    | Shared, verbatim; a bot that needs another session file extends `SESSION_FILES` here and proposes the change to the template                          |
| `scripts/artifact_fetch.py`                                | **Keep**    | Shared, verbatim                                                                                                                                      |
| `scripts/preflight.py`                                     | **Keep**    | Shared, verbatim; a further check is a new command (below)                                                                                            |
| `scripts/ledger.py`                                        | **Keep**    | Shared, verbatim; `RECORDED_VERDICTS` is the one constant a bot edits                                                                                 |
| `tests/test_workflow.py`                                   | **Adapt**   | Update the facts that change with the bot (artifact prefix, write permission, inputs) and nothing else; see below                                     |
| `tests/test_prepare.py`, `test_apply.py`, `test_report.py` | **Replace** | Tests for the bot's own selection, corroboration and report                                                                                           |
| other `tests/test_*.py`                                    | **Keep**    | Cover the shared modules                                                                                                                              |
| `docs/`, `mkdocs.yml`, `README.md`                         | **Replace** | The bot's own documentation; keep the Setup, Design and Pages structure                                                                               |
| `AGENTS.md`                                                | **Adapt**   | Keep the organisation stub; rewrite *Repository specifics* for the bot                                                                                |
| `.pre-commit-config.yaml`, `.aislop/`                      | **Keep**    | The hooks and the `aislop` gate at 100                                                                                                                |
| `pyproject.toml`, `uv.lock`                                | **Keep**    | Add dependencies through `uv` so `uv run --locked` keeps working                                                                                      |
| `REUSE.toml`                                               | **Adapt**   | Add any new file that cannot carry an inline header (below)                                                                                           |

<!-- markdownlint-enable MD013 -->

Nothing in the table is **Delete**: the template carries no file a
bot has no use for. A bot that drops a piece, the documentation site
for example, removes its workflow and its test together.

## Renaming

The template uses `bot` as the name of everything: the workflow
files, the `name:` fields, the concurrency groups, the artifact
prefix and the ledger artifact. Rename them in one pass:

1. `bot.yaml` and `bot-cron.yaml` after the bot, and the `uses:
   $/.github/workflows/bot.yaml` lines in both callers.
2. Every artifact name in `bot.yaml`: `bot-evidence-`,
   `bot-session-`, `bot-result-`, `bot-report-`, `bot-ledger`, and
   the `--artifact-name bot-ledger` the ledger fetch passes. The
   report job's `pattern:` must match the result prefix.
3. The concurrency group prefixes in `bot.yaml` and `bot-cron.yaml`.
4. The `assets_repository` default, to the new repository.
5. `tests/test_workflow.py`: the file names it loads and the artifact
   prefixes it asserts.

`grep -rn 'bot-' .github tests` after the pass finds anything left.

## Replacing the placeholders

### `prepare.py`

`select_targets(org, repositories, excluded, limit)` returns a list
of targets. The bot's version scans with `bot_github.api_list` and
`graphql`, applies its skip rules, drops what
`ledger.already_assessed` records at the same head, caps the list,
and returns entries that carry the five `TARGET_KEYS` plus whatever
the packet and the offline check need. Pass the ledger in: the
placeholder's `main` checks that the file exists and a bot loads it
with `ledger.load_ledger`.

Keep the arguments, the three output files and `schema: 1`. Keep the
script free of writes: the token it holds cannot write and the gate
has proved it, so a write here fails, but the design says it
never tries. Record skipped candidates and their reasons in
`selection.json`; the setup guide tells operators to look there.

### The packet step

The agent job's `Prepare packet and prompt` step projects the target
out of the verified `selection.json` by `key` and appends a runtime
context block to the prompt. A bot adds the packet files its task
needs there (a bounded diff, an issue body, a file list) and the
context lines that name them. Every field comes from the verified
selection, never from the matrix alone: the matrix is a job output
and carries no digest. A bot that checks out the target's head does
so with `persist-credentials: false` and a `repository` and `ref`
taken from the selection.

### `prompt/task.md`

Each `<!-- replace: ... -->` comment marks a part a bot writes. The
title, the role paragraph's first sentence, rule 1's packet fields,
the rules from 7 onwards, the environment list, the procedure and
the areas a finding may name are the bot's. The trust framing (no
credential, a separate trusted job checks the verdict, packet text
is data), the tool list and the verdict block stay, because the
apply job and the tool policy depend on them. The prompt must not
restate organisation policy on commits, tests or reviews; the agent
receives the organisation `AGENTS.md` for that in bots that need it,
and a copy drifts.

A bot that renames the verdict tokens changes `AGENT_VERDICTS` in
`apply.py` and the prompt together.

### `apply.py`

`check` reads the last fenced `json` block of the accepted summary,
refuses one about another target, and prints `approvable=true` when
the write mint may run. A bot extends `check_verdict` with its own
corroboration: a deterministic classifier over the packet, a veto
list, a cross-check of the agent's findings against the selection.
Everything the check reads comes from `selection.json` and the
bounded session; it has no token and makes no network call.

`apply` runs after the mint. `perform_write(target, summary)` holds
the bot's one write: re-read the live target with the token in
`GH_TOKEN`, refuse if it moved (a new head, a human already acted),
make the write through `bot_github.api_write` or `graphql`, and
return the URL. Return `None` and the result records `needs-human`
with a reason. Keep the `result.json` shape; `report.py` and
`ledger.record` read it.

### `report.py`

`row_for` and `describe` render the columns the bot's results carry:
a tier, a label set, a pull request URL. The ledger entries stay as
`ledger.py` defines them; a bot that wants to remember more extends
`check_entry` in the shared module and proposes it to the template.

## Adding an input

An input touches three files and one test:

1. **`bot.yaml`**: declare it under `on.workflow_call.inputs` with a
   description, a type and a default, and check it in the prepare
   job's `Check inputs` step before anything else reads it. Pass it
   to the script that needs it through an `env:` entry, never by
   interpolating `${{ inputs.x }}` into a `run:` line.
2. **`bot-cron.yaml`**: add the dispatch input and forward it in the
   `bot` job's `with:` block with a default for scheduled runs
   (`"${{ inputs.x || 'default' }}"`).
3. **`testing.yaml`**: forward it in the `dry-run` job if the manual
   dry run should take it. The plumbing legs take no new input unless
   it changes selection.
4. **`tests/test_workflow.py`**: add it to
   `InputContracts.test_declared_inputs_and_secrets` and
   `test_input_defaults`, and to `CronContracts.test_dispatch_form`
   when the form grows.

The pre-flight gate re-runs those tests at run time, so a bot that
forgets step 4 fails its next scheduled run rather than its pull
request alone.

## Adding a second mint

The template's write mint takes its scope from `matrix.repo_name`
and its owner from `inputs.org`, and runs on `!inputs.dry_run`,
`approvable == 'true'` and a client id. A bot that needs a second
write-capable token, to push to a fork organisation for example,
keeps the provenance contract:

- The `owner` is `inputs.org` or a constant the design names; never
  a value from the selection, the session or a config file.
- The `repositories` scope is a single name the trusted prepare job
  recorded in the matrix.
- The step runs in the apply job (or another trusted job that holds
  no session output), on the live path, after the offline check.
- Its `app-slug` output passes `preflight.py identity` before any
  step uses the token.
- `MintProvenanceContracts` in the tests enumerates every
  `create-github-app-token` step; the new one must pass
  `test_every_mint_owner_is_the_trusted_org_input` and the write
  scope test, or the tests change together with the design.

A mint in the agent job fails `test_agent_holds_no_app_key` and is
never acceptable.

## Extending the pre-flight gate

Every bot carries `preflight.py` verbatim, and it knows the shape
every bot has in common. A bot that needs a further run-time check
adds a command rather than editing the four that exist, so the
shared file stays verbatim and the bot's addition is visible as
such:

- Add a function that raises `Drift` with a message that names the
  check and quotes nothing it read, and calls `say` on success.
- Register it as a new subcommand in `main`.
- Call it from the workflow at the stage it belongs to: before the
  read mint for configuration, after a mint for the token.
- Pin the call in `PrepareContracts` or `ApplyContracts`.

Propose a check with no bot-specific content to the template; the
other bots then inherit it.

## Adding a configuration file

JSON has no comment syntax, so a new `config/*.json` cannot carry an
SPDX header. `REUSE.toml` already annotates `config/*.json`; a file
under another path or with another extension that cannot carry a
header needs its own `[[annotations]]` entry, or the `reuse` hook
fails. Keep configuration small and typed: `load_config` in
`preflight.py` refuses a `bot.json` over 64 KiB or with an
unexpected key, and a bot's own loader should refuse as much.

## When `aislop`'s file-length cap bites

The `aislop` gate runs at threshold 100 and scores, among other
measures, file length, with a cap of 400 lines. A bot's `prepare.py`
or `apply.py` grows past the cap when the selection rules or the
corroboration run long. Split by responsibility, not by line
count:

- GitHub reads for the selection into one module, the selection
  policy into another, the output files into a third; the review
  bot's `pull_reads`, `select_pulls` and `selection_outputs` follow
  this split.
- The verdict check into a policy module and the deterministic
  classifier or veto list into its own, with the apply script
  calling both.

Each new module with a contract of its own gets a test file; a
helper split out of a parent keeps its coverage through the parent.
Never lower the threshold or add an exclusion to pass the gate.

## Keeping `tests/test_workflow.py` honest

The contract tests exist to fail when the workflow drifts. When a
workflow change fails one:

1. Decide whether the design changed. If the change is a rename, a
   new input or the bot's write permission, update the fact the test
   asserts.
2. If the change moves a credential, a gate or an artifact flow, the
   design document changes first, the test second, the workflow
   third, in one pull request that a reviewer reads in that order.
3. Never weaken a test to make an edit pass, and never delete one
   because the step it pins went away without a replacement.

The pre-flight gate runs these tests again at run time from the
pinned assets, so a test that lies in the repository lies in
production too.

## Commits and pull requests

The organisation guidelines govern every contribution, to the
template and to a bot built from it:

<https://github.com/lfreleng-actions/.github/blob/main/AGENTS.md>

In short: signed commits with a DCO trailer, a `Type(scope):
Imperative description` subject, a `Co-authored-by` trailer naming
any agent used, the `prek` hooks installed and passing, and a PR
title identical to the commit subject on a single-commit pull
request. [`AGENTS.md`](https://github.com/lfreleng-actions/bots-template/blob/main/AGENTS.md)
in the repository root repeats the rules that most often block a
pull request and the commands to run before pushing.
