<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# 🤖 GitHub Bots Template

<!-- prettier-ignore-start -->
<!-- markdownlint-disable-next-line MD013 -->
[![Linux Foundation](https://img.shields.io/badge/Linux-Foundation-blue)](https://linuxfoundation.org/) [![Source Code](https://img.shields.io/badge/GitHub-100000?logo=github&logoColor=white&color=blue)](https://github.com/lfreleng-actions/bots-template) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0) [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lfreleng-actions/bots-template/badge)](https://scorecard.dev/viewer/?uri=github.com/lfreleng-actions/bots-template)
<!-- prettier-ignore-end -->

The starting point for every GitHub bot in the `lfreleng-actions`
organisation. A bot is a reusable GitHub Actions workflow that scans
an organisation for targets, runs one **Copilot CLI** agent session
per target in an **untrusted** job, and makes its one GitHub write
from a **trusted** job under a dedicated **GitHub App**. Evidence
passes between jobs by artifact ID and SHA-256 digest, a run-time
pre-flight gate checks the trust boundary before any token exists,
and a ledger artifact carries memory from run to run.

Three bots share this base: one labels issues, one approves trivial
pull requests, one authors pull requests from issues. Each replaced
the same three placeholders and kept everything else.

## 📚 Documentation

- [Setup](docs/setup/README.md): the App, variables and secrets, the
  model credential, validation, the first dry run and going live.
- [Customising](docs/CUSTOMISING.md): what a bot keeps, adapts,
  replaces and deletes, and how to extend the shared pieces.
- [Design](docs/DESIGN.md): the pattern, the trust model, the
  pre-flight gate, the shared modules and the data contracts.

## What the template provides

<!-- markdownlint-disable MD013 -->

| Path                                   | Contents                                                                                   | Guidance                                                                      |
| -------------------------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| `.github/workflows/bot.yaml`           | The reusable workflow: prepare, agent, apply, report                                       | Keep the shape; edit the steps marked `Replace` and the write mint's grant    |
| `.github/workflows/bot-cron.yaml`      | Scheduled and dispatch caller                                                              | Adapt: rename, add the schedule, forward new inputs                           |
| `.github/workflows/testing.yaml`       | Secretless PR plumbing, manual agent dry run, offline suite, docs build                    | Keep; forward any input the manual dry run needs                              |
| `.github/workflows/documentation.yaml` | MkDocs build and GitHub Pages deployment                                                   | Keep                                                                          |
| `prompt/task.md`                       | The agent's task, with `replace` markers and the verdict block                             | Replace the marked parts; keep the trust framing and the block                |
| `config/bot.json`                      | The App slug the pre-flight gate checks every token against                                | Replace the slug                                                              |
| `config/excluded-repos.txt`            | Repositories the scan skips                                                                | Adapt                                                                         |
| `tools/copilot-cli/`                   | `package.json`, `package-lock.json` and `.npmrc` pinning the CLI                           | Keep; bump through the lockfile                                               |
| `scripts/prepare.py`                   | Placeholder selection: writes `selection.json`, `matrix.json`, `selection-summary.md`      | Replace `select_targets`; keep the files and the entry shape                  |
| `scripts/apply.py`                     | Placeholder verdict check and the one write                                                | Replace `perform_write` and extend `check_verdict`; keep `approvable=` output |
| `scripts/report.py`                    | Placeholder report and ledger writer                                                       | Extend `row_for` and `describe`                                               |
| `scripts/bot_github.py`                | `gh` wrapper: REST, GraphQL, retries, typed field access                                   | Keep verbatim                                                                 |
| `scripts/bot_evidence.py`              | Digest verification and the cap table for session files                                    | Keep verbatim; extend `SESSION_FILES` if a session must hand over more        |
| `scripts/artifact_fetch.py`            | Bounded artifact download and extraction                                                   | Keep verbatim                                                                 |
| `scripts/preflight.py`                 | Run-time checks of the workflow, config, credentials, App identity and token grants        | Keep verbatim                                                                 |
| `scripts/ledger.py`                    | Run-to-run memory of handled targets                                                       | Keep verbatim; edit `RECORDED_VERDICTS` if the bot's verdict tokens differ    |
| `tests/`                               | Offline suite, one file per module, plus `test_workflow.py` pinning the workflow contracts | Keep; extend with the bot's own tests                                         |
| `docs/`, `mkdocs.yml`                  | This documentation and the site configuration                                              | Replace with the bot's own; keep the structure                                |
| `.pre-commit-config.yaml`, `.aislop/`  | The lint hooks and the `aislop` gate at threshold 100                                      | Keep                                                                          |

<!-- markdownlint-enable MD013 -->

## The four-job pattern

```text
prepare (trusted)         agent (untrusted, matrix)    apply (trusted, matrix)
  App read token            model PAT, no App            verify evidence
  prior ledger              offline packet               bounded session fetch
  select + cap              Copilot CLI session          offline check
  selection.json            one JSON verdict block       write token, one write
  ID + digests --------->   session artifact --------->  result.json
                                                              |
                                              report (trusted) <--+
                                                report.md/json, ledger
```

The agent job holds a model credential and nothing else: no App key,
no App token, `contents: read` on its own token. It emits text. The
apply job treats that text as hostile input, bounds it on fetch,
checks it against the trusted selection, and alone holds a token
that can write. The write mint in the template grants `metadata:
read`; a bot swaps in the one write permission its action needs.

## Creating a bot from this template

1. **Use the template.** On GitHub, *Use this template* creates the
   new repository with the history squashed. Clone it.
2. **Rename the pieces.** Rename `bot.yaml` and `bot-cron.yaml` after
   the bot, update the `name:` fields and the `uses:` paths in the
   callers, and change the `bot-` artifact prefix in `bot.yaml`
   (evidence, session, result, report and ledger names, plus the
   `--artifact-name` the ledger fetch passes). Put the bot's App slug
   in `config/bot.json`. Replace the `assets_repository` default
   with the new repository.
3. **Write the task.** Fill in every `replace` marker in
   `prompt/task.md`. Keep the trust framing, the tool list and the
   verdict block; the apply job reads the block.
4. **Replace the placeholders.** `scripts/prepare.py` selects
   targets, `scripts/apply.py` corroborates the verdict and makes the
   one write, `scripts/report.py` renders the columns the results
   carry. Keep the arguments and the files each writes; the workflow
   and the shared modules depend on them. Set the write mint's
   permission in `bot.yaml` and add the read grants the selection
   needs to the read mint.
5. **Create the App** with the least permissions the bot needs
   (Metadata read plus the bot's own) and install it on the
   organisation. [Setup](docs/setup/README.md) walks through it.
6. **Wire the credentials.** Set the repository variable
   `BOT_APP_CLIENT_ID` and the repository secret
   `BOT_APP_PRIVATE_KEY`, and confirm the organisation secret
   `COPILOT_CLI_TOKEN` is visible to the new repository.
7. **Dry-run.** Dispatch `testing.yaml` against a reviewed ref. It
   runs the agent with read access, no App credential, and reports what
   the bot would have done.
8. **Enable the schedule.** Uncomment the `schedule:` block in the
   cron caller, keep `dry_run` true until the reports look right,
   then flip the expression in the `bot` job.

## Bots built on this base

<!-- markdownlint-disable MD013 -->

| Bot                                                                                        | One line                                                                                      |
| ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| [`github-issues-triage-bot`](https://github.com/lfreleng-actions/github-issues-triage-bot) | Labels issues: the write is a label set, the App holds `issues: write`                        |
| [`github-code-review-bot`](https://github.com/lfreleng-actions/github-code-review-bot)     | Approves trivial and low-risk pull requests: the write is an APPROVE review                   |
| [`github-code-monkey-bot`](https://github.com/lfreleng-actions/github-code-monkey-bot)     | Authors pull requests from issues, publishing signed commits through bot fork organisations   |

<!-- markdownlint-enable MD013 -->

## Development

```bash
uv run python -B -m unittest discover -s tests -v
prek run --all-files
zizmor --persona auditor .github/workflows/
aislop ci
```

The suite runs offline and needs no credentials. `tests/test_workflow.py`
pins which job holds which credential, which step gates the write
token and how artifacts travel; the pre-flight gate re-runs it at
run time from the pinned assets. Expect zero findings from `zizmor`
and a clean `aislop` gate at threshold 100.
