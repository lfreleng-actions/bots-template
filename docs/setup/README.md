<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# Setup

A bot built from this template runs four jobs: trusted **Prepare**,
untrusted **Agent** (one per target), trusted **Apply** (one per
target) and trusted **Report**. Prepare selects targets and builds an
offline packet for each; Agent runs Copilot CLI against the packet;
Apply corroborates the verdict and makes the bot's one write; Report
writes the run summary and the ledger. [`../DESIGN.md`](../DESIGN.md)
explains why each job holds what it holds.

This guide covers what an operator configures and checks. The
template itself has no write to configure: its placeholders select
nothing and write nothing, so a run of the unmodified template proves
the plumbing and stops there.

## GitHub App

Each bot runs as its own GitHub App that does the bot's one thing
and nothing else. Create it under the organisation:

- **Name**: a name that says what the bot does, in the house style
  of the existing Apps (for example *LF/RelEng Code Review Bot*).
- **Slug**: GitHub derives it from the name. Put it in
  `config/bot.json`; the pre-flight gate refuses a token minted by
  any other App, so a mis-wired key fails before a session starts.
- **Visibility**: private to the organisation. Nothing else installs
  it.
- **Webhook**: off. The bot polls on a schedule and receives no
  events.
- **Repository permissions**: start from **Metadata: read**, which
  every installation token carries, and add what the bot's selection
  reads and what its one write needs. A labelling bot adds *Issues:
  read and write*; an approving bot adds *Pull requests: read and
  write* with the reads its CI gates use; a bot that pushes code adds
  *Contents: write* where it pushes. Grant no permission the
  workflow does not mint.
- **Installation**: install on the organisation, covering every
  repository the scan should see. "All repositories" matches an
  organisation-wide scan.

Generate a private key on the App's settings page and download the
PEM file; store it as the secret below, then delete the download.

Keep each bot's App separate. An identity that approves pull requests
must never be one that also labels issues or pushes code: a
permission added for one pipeline would widen the other, and the
ruleset exception one bot needs must stay as narrow as that bot. The
organisation learned this once by widening the triage App for the
first review bot approval; the per-bot App and the slug check in
`config/bot.json` are the result.

**Accept permission changes on the installation.** Adding
permissions to an App does not change its existing installations.
GitHub emails the organisation owners a request, and the
installation page under *Organization settings → GitHub Apps* shows
a pending review. Until an owner accepts it there, the prepare job's
token mint fails with a message naming the permission the
installation lacks.

The reusable workflow mints twice. Prepare mints `contents` and
`metadata` at read, scoped to the named repositories when any and
organisation-wide for a scan; a bot adds the read grants its
selection needs there. Apply mints for one repository, on the live
path alone, after the offline check accepted the verdict; the
template grants `metadata: read` and a bot replaces that with its
single write permission. Agent receives neither the App key nor any
App token.

## Variables and secrets

Every bot repository in the organisation uses the same names, so the
calling workflow is identical across them and the App behind a name
can change without a code change. Configure these on the repository
that runs the scheduled caller:

<!-- markdownlint-disable MD013 -->

| Name                  | Kind                | Value                                                 |
| --------------------- | ------------------- | ----------------------------------------------------- |
| `BOT_APP_CLIENT_ID`   | Repository variable | The App's client id; empty limits runs to dry-run     |
| `BOT_APP_PRIVATE_KEY` | Repository secret   | The App's private key (PEM)                           |
| `COPILOT_CLI_TOKEN`   | Organisation secret | Fine-grained PAT with Copilot Requests (next section) |

<!-- markdownlint-enable MD013 -->

`COPILOT_CLI_TOKEN` already exists at organisation level. Confirm
its repository access policy includes the new repository; a secret
scoped to selected repositories stays invisible to a new one until
an owner adds it, and the agent step then fails its `github_pat_`
check with an empty value.

The reusable workflow sees nothing but the values handed to its
named inputs, so another caller may hold them under other names.
Keep the App key out of any job that runs an agent; the bundled
callers do.

## The model credential

Copilot CLI authenticates with a personal fine-grained PAT. Create
one at
<https://github.com/settings/personal-access-tokens/new> with:

- **Resource owner**: your own account.
- **Repository access**: *Public repositories* is enough; grant no
  repository permission at all.
- **Account permissions**: *Copilot Requests* (read) and nothing
  else.
- **Expiry**: as short as your rotation cadence allows. Note
  the date; the agent step cannot see it.

Store the value as `COPILOT_CLI_TOKEN`. The agent step checks the
`github_pat_` prefix before launching the CLI, which rejects classic
and native tokens but cannot see extra grants on a fine-grained one.
Review the token's grants yourself. Native caller tokens are not
supported. An expired token fails every session at start; the
report then shows uniform rows whose reason names a missing session.

## Validation

Start offline:

```bash
uv run python -B -m unittest discover -s tests -v
prek run --all-files
zizmor --persona auditor .github/workflows/
aislop ci
```

The suite includes `tests/test_workflow.py`, which pins which job
holds which credential and which step gates the write token. Expect
zero findings from zizmor and a clean `aislop` gate at threshold 100.

Every pull request to the repository runs the secretless plumbing
legs: Prepare and Report with the pull request head as assets,
`skip_agent: true` and no secret in reach, twice in parallel to
prove two invocations in one run never read each other's artifacts.
They exercise the ledger fetch, the selection, the evidence upload
and the report.

## The first dry run

Dispatch the testing workflow by hand to run the agent itself,
against a ref you have reviewed:

```bash
gh workflow run testing.yaml \
  -f repositories='repo, other-repo' \
  -f max_targets=2
```

Both inputs are optional; without them the dispatch scans the
organisation with a cap of two. This path is always dry-run and
passes no App credential, so the prepare job reads with the
job-native token and the apply job records what it would have done
without a write. Read the Report job's step summary and the session
artifacts to judge whether each verdict landed where a maintainer
would put it.

Three artifact families sit under the run's *Artifacts* section:

- **Evidence, 7 days** (`bot-evidence-<ns>`): `selection.json`,
  `matrix.json`, the merged prior `ledger.json` and the selection
  summary. Start here when a target you expected is missing.
- **Session, 7 days** (`bot-session-<ns>-<key>`): the exact prompt,
  the CLI logs, `usage.json` and `session-summary.md`, whose last
  fenced `json` block is the verdict. Start here when a verdict looks
  wrong.
- **Results and report, 90 days** (`bot-result-<ns>-<key>-<attempt>`,
  `bot-report-<ns>-<attempt>`, `bot-ledger`): `check.json` and
  `result.json` per target, `report.md` and `report.json` for the
  run, and the ledger the next run reads.

A bot renames the `bot-` prefix after itself; the families stay.

## Going live

The scheduled caller ships with its `schedule:` block commented out
and `dry_run: true` for every scheduled run. Uncomment the schedule
first and let dry runs accumulate until the reports show `would-do`
rows across a run of days, and read each one: a `would-do` on a
target a human would have handled differently is a prompt or check
gap to close first.

To flip, open a pull request that changes the `dry_run` expression
in the cron caller so scheduled runs pass `false`; the comment above
it marks the line. The first live run acts on what the dry runs
reported, because live runs ignore dry-run ledger entries.

Disabling the scheduled workflow in the Actions UI is the kill
switch.

## Recovery

The pipeline makes one write per target, from the apply job, after
the offline check. What that write is depends on the bot, and so
does its reversal; the template's contribution is that nothing else
needs rolling back.

- **A wrong write.** Undo it by hand on the target, with a note. The
  head stays in the ledger, so the bot does not repeat the write; a
  new head is a fresh assessment.
- **A missed target.** Check `selection.json` in the evidence
  artifact: the bot's selection records why it skipped what it
  skipped, or name the repository in a dispatch.
- **A `failed` row.** The reason names the step that did not
  complete: a token mint, the offline check or GitHub during the
  write. Failed targets are not recorded in the ledger, so the next
  run retries them.
- **A lost ledger.** The prepare job merges the newest five ledger
  artifacts; it warns about an unreadable one and leaves it out.
  The cost is re-assessment of the targets the lost entries covered,
  nothing more.
- **A stuck run.** Live runs serialise on one concurrency group per
  caller and owner; cancel a stuck run from the Actions UI and the
  next schedule proceeds. Cancellation never undoes a write already
  made.
- **A pre-flight failure.** The annotation names the check and
  quotes nothing it read. A `config` failure means a credential
  shape or the egress coordinate is wrong; an `identity` failure
  means another App's key sits behind the mint; a `workflow` failure
  means a merged change broke a contract test or a zizmor rule. Fix
  the cause; the gate holds the run closed until then.

## GitHub Pages

The documentation site builds with MkDocs and deploys from
`.github/workflows/documentation.yaml` on pushes to `main` that
touch `docs/`, `mkdocs.yml` or the workflow itself. Set the
repository's Pages source to **GitHub Actions** under *Settings →
Pages*; the branch-based source does not work with this workflow.
Pull requests build the site with `mkdocs build --strict` and
deploy nothing.

## Further reading

- [Customising](../CUSTOMISING.md): what a bot keeps, adapts,
  replaces and deletes, and how to extend the shared pieces.
- [Design](../DESIGN.md): the pattern, the trust boundary, the
  pre-flight gate, the shared modules and the data contracts.
- [`prompt/task.md`](https://github.com/lfreleng-actions/bots-template/blob/main/prompt/task.md):
  the agent's task as it reads it, with the parts a bot replaces
  marked.
