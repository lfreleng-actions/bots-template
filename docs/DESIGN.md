<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# Design: A Template for GitHub Bots

This document describes the pattern every GitHub bot in the
`lfreleng-actions` organisation follows, the trust model behind it,
the pieces the template ships verbatim to each bot, and the pieces a
bot replaces. Anyone changing how the jobs hand data to each
other, in the template or in a bot built from it, starts here.

A **bot** here is a reusable GitHub Actions workflow that scans an
organisation for targets, runs one Copilot CLI agent session per
target, and makes one GitHub write per target. The template is not a
bot: its placeholders select nothing and write nothing, and a run of
it proves the plumbing alone.

## 1. Problem

Three bots exist in the organisation: `github-issues-triage-bot`
labels issues, `github-code-review-bot` approves trivial and low-risk
pull requests, and `github-code-monkey-bot` authors pull requests
from issues and publishes them through bot fork organisations. Each
grew on its own, and each arrived at the same shape: a trusted
job that selects, an untrusted job that runs the agent, a trusted job
that writes, and a report. They also arrived at the same defences in
slightly different forms: evidence passed by artifact ID and digest,
a bounded artifact fetch, a ledger of handled targets, a run-time
check that the App behind the key is the expected one.

Three copies of the same defence drift. A fix in one copy has no
path to the others; a contract test one bot adds stays absent from
the next. The template extracts the shared base so that a fourth
bot starts from the current state of the defences, and so that a
change to a defence lands in one place and propagates.

The organisation ships one agent harness, Copilot CLI. Earlier
drafts carried Claude and Gemini paths that no run exercised; the
organisation removed them rather than leave them untested, and the
template does not reintroduce them.

## 2. The pattern

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

The **prepare** job allocates an invocation namespace, validates the
inputs, resolves the assets ref to one commit, runs the pre-flight
gate, mints a read token when a client id is present,
fetches the prior ledger with the job-native token, runs the bot's
selection, and publishes `selection.json`, `matrix.json` and the
merged `ledger.json` as one evidence artifact. The artifact ID and
the SHA-256 digests of `selection.json` and `ledger.json` become job
outputs, which is how every later job knows it reads the bytes the
trusted job wrote.

The **agent** job runs once per matrix entry. It checks the matrix
fields against strict patterns, checks out the pinned assets without
credentials, downloads the evidence by ID and verifies the digests,
installs Copilot CLI from the committed lockfile, projects the
target's entry out of the verified `selection.json` into a packet,
and runs the CLI with shell tools that read. It holds the model
credential and nothing else. Its output is a session artifact: the
share summary ending in a verdict block, the usage file, the prompt
and the CLI logs.

The **apply** job runs once per matrix entry after the agent job
settles, including after a failed entry. It verifies the evidence
again, fetches the session through the bounded extractor, runs the
bot's offline check, and on the live path mints a token for that one
repository and makes the one write. Every path ends in a typed
`result.json`.

The **report** job gathers every result, renders the step summary,
writes `report.json` and `report.md`, and publishes the new ledger.

What a bot changes within this shape: `prepare.py` (what to select
and what the packet holds), the packet step and `prompt/task.md`
(what the agent reads and what the task asks), `apply.py` (how the
bot corroborates a verdict and what the write is), the write mint's
permission, and `report.py` (which columns the report shows).
Everything else is the pattern.

## 3. Trust model

### 3.1 Why tool policy cannot contain the agent

The Copilot CLI invocation allows `cat`, `jq`, `grep`, `head`,
`tail`, `wc` and `ls`, and denies `write`, `gh` and `git`. That
list states intent: the CLI auto-approves what it classifies as a
read, and a prompt-injected target could coax a bypass out of any
shell. `awk`, `sed` and `find` stay out of the allow list for that
reason: each can write or run commands.

Containment comes from what the job lacks. It has no GitHub
credential: `COPILOT_GITHUB_TOKEN` is a fine-grained PAT with Copilot
Requests and no repository grants, and the job-native token carries
`contents: read` alone, with `persist-credentials: false` on every
checkout. No step in the agent job references the App key, the
client id or an App token. Whatever the session does, it cannot
label, approve, push or read a private repository. Its sole product
is text, and the apply job treats that text as hostile input:
bounded on fetch, parsed into a validated shape, cross-checked
against the trusted selection, and then corroborated by the bot's
own checks before the one write.

### 3.2 Evidence by ID and digest

An artifact name is not an integrity boundary: any job in the run
can upload under a name. The prepare job publishes the
artifact ID `upload-artifact` returns and the SHA-256 of each
evidence file as job outputs, and every consumer downloads by
`artifact-ids` and runs `bot_evidence.py verify` against those
digests before reading a byte. The agent, apply and report jobs each
check the provenance values against strict patterns first: a
40-hex `assets_sha`, a numeric artifact ID, 64-hex digests, a
constrained matrix key.

The session artifact runs the other way: it comes from the untrusted
job, so no digest can vouch for it. The apply job fetches it by name
within the run, through `artifact_fetch.py`, which bounds the zip
size, the entry count and each permitted file before writing
anything, and extracts the two named session files and nothing else.

### 3.3 Invocation namespace

The prepare job allocates `run_id-run_attempt-uuid` and every
artifact name carries it, so two invocations of the reusable
workflow in one caller run never read each other's files, and a
rerun's artifacts stay distinct from the first attempt's. The
testing workflow's two parallel plumbing legs prove the property on
every pull request.

### 3.4 What the design trusts the caller with

`assets_repository` and `assets_ref` choose the prompt and scripts.
The prepare job resolves the ref to a commit once and every later job
checks out that SHA, so a moving branch cannot swap code between
jobs. Callers run reviewed assets with secrets or none at all: the
testing workflow's pull request legs check out the pull request's
own head as assets and pass no secret for this reason.

`egress_allow_config` names the organisation allow-list coordinate
for `harden-runner-block-action`; the bundled callers pin it to a
commit. `github_app_client_id` and the two secrets reach trusted
jobs alone, by construction of the reusable workflow.

### 3.5 What the design does not trust the caller with

The `owner` of every token mint is `inputs.org`, and the
`repositories` of the one write mint is a single matrix repository
name recorded by the trusted prepare job. No mint takes its owner or
scope from an issue, a pull request, agent output or a config file.
The contract tests pin both, and the pre-flight gate re-runs those
tests before any mint.

## 4. The pre-flight gate

The contract tests in `tests/test_workflow.py` run when a pull
request changes the workflow. A scheduled run executes whatever is on
the default branch, and nothing in that path re-checks the boundary
before the first App token mint: a drift merged through any route
the tests do not cover, a wrong secret wired to the right name, or a
broader App's key in this workflow would all run. The gate closes
that: `scripts/preflight.py` runs from the pinned assets checkout,
before any `create-github-app-token` step, and fails the run closed.

<!-- markdownlint-disable MD013 -->

| Stage                | Check                                                                                            | Drift it refuses                                                                                                                                                                                              |
| -------------------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Before the read mint | Re-run `tests/test_workflow.py` against the checked-out workflow files                           | An App key or mint in the agent job, an unpinned action, a write mint no longer gated on `!dry_run` and the check outcome, a download by name where the design demands an ID, `persist-credentials` turned on |
| Before the read mint | `zizmor --persona auditor` on the checked-out workflows: zero findings                           | Expression injection, `pull_request_target`, cache poisoning, anything the contract tests do not name                                                                                                         |
| Before the read mint | `config/bot.json` is a small object naming one lower-case slug                                   | A config edit that points identity checks at a different App                                                                                                                                                  |
| Before the read mint | Credential shapes: client id pattern, PEM markers and plausible key length, `github_pat_` prefix | The wrong secret under the right name; values are never printed                                                                                                                                               |
| Before the read mint | Block mode has a commit-pinned allow-list coordinate and a loaded list                           | An allow-list absent without an error, or pinned to a moving branch                                                                                                                                           |
| Before the read mint | A live run's `assets_sha` equals `job.workflow_sha`                                              | A reviewed workflow executing unreviewed scripts through `assets_ref`                                                                                                                                         |
| After the read mint  | The `app-slug` the mint returns equals `config/bot.json`                                         | Another App's private key wired into this workflow                                                                                                                                                            |
| After the read mint  | `GET /repos/{this}` with the token shows no `push`, `maintain` or `admin`                        | A read mint that obtained more than it asked for                                                                                                                                                              |
| Before the write     | The write mint's `app-slug` equals `config/bot.json`                                             | The same, on the one token that can write                                                                                                                                                                     |

<!-- markdownlint-enable MD013 -->

The gate costs about half a minute per run: a `uv` setup and a
`zizmor` install from PyPI, which the allow-list already carries.
Everything it checks is a file already on the runner or a value the
workflow already holds; it introduces nothing new to trust. A run it
fails is a run that must not proceed, and the error annotation names
the check without quoting what the check read.

The gate is not where policy lives. It verifies that the workflow
still has the shape the design and tests describe; the design and
tests remain the place to change that shape. `preflight.py` knows
the shape every bot shares and nothing of what any one bot does; a
bot that needs a further check adds a command rather than editing
the four that exist (see [Customising](CUSTOMISING.md)).

## 5. Credentials

### 5.1 One App per bot, under template names

Each bot runs as its own GitHub App. The organisation recorded the
lesson the hard way: the first live approval by the review bot used
the issues triage App with its permissions widened, and an identity
that approves pull requests must never be one that also labels
issues or pushes code. A permission added for one pipeline widens
the other, and the ruleset exception one bot needs must stay as
narrow as that bot. Never widen one bot's App for another; create a
new App.

The slug lives in `config/bot.json`, a small object with `app_slug`
and an optional `description`; the pre-flight gate refuses a token
minted by any other App, twice per target: after the read mint and
before the write.

The calling workflow names the credentials by role, never by App or
repository: `vars.BOT_APP_CLIENT_ID` and `secrets.BOT_APP_PRIVATE_KEY`.
Every bot repository uses the same two names, so the callers are
identical and the App behind a name can change without a code
change. The reusable workflow receives them as the input
`github_app_client_id` and the secret `github_app_private_key`.

### 5.2 Two mints, least privilege

The App key appears in the prepare and apply jobs as an input to
`create-github-app-token` and nowhere else.

- **Prepare** mints `contents: read` and `metadata: read`, scoped to
  the named repositories when any and organisation-wide for a scan.
  A bot adds the read grants its selection needs here and nothing
  else; the gate then probes the token and refuses one that can push,
  maintain or administer.
- **Apply** mints for `matrix.repo_name` alone, on the live path,
  after the offline check printed `approvable=true`. The template
  grants `metadata: read`, which can write nothing; a bot replaces
  that line with the single write permission its action needs, for
  example `permission-issues: 'write'` or
  `permission-pull-requests: 'write'`.

An empty client id limits a run to dry-run: the prepare job fails a
live run without one before anything else happens, and a dry run
falls back to the job-native token for its reads.

The ledger lives in the bot repository's own artifacts, so the
prepare job reads it with the job-native token under
`actions: read`; the App has no grant there and needs none.

### 5.3 The model credential

The organisation secret `COPILOT_CLI_TOKEN` holds a personal
fine-grained PAT with Copilot Requests and no repository grants; the
reusable workflow receives it as `copilot_token`. The agent job
holds this credential and no other. The agent step checks the
`github_pat_` prefix before launching the CLI, which rejects native
and classic tokens and nothing more; the setup guide's provisioning
procedure keeps extra grants out. The token's owner is the rotation
owner; an expired token fails every session at start, visible in the
report as rows whose reason names a missing session.

### 5.4 Models

The default model is `claude-opus-5.5`. The cron caller's dispatch
form offers Claude Opus 5.5, Claude Fable 5.1, Claude Sonnet 5.5 and
GPT-6 Astra as display names and maps each to the identifier the CLI
accepts. The selection records the model, and `usage.json` supplies
the session's cost figures, which a bot's report may sum.

## 6. The Copilot CLI harness

The agent job installs the CLI with `npm ci` from the committed
`tools/copilot-cli/package-lock.json`, copied out of the verified
assets checkout into `$RUNNER_TEMP`, with `--ignore-scripts`. The
lockfile pins the whole dependency tree by integrity hash; bumping
the CLI means regenerating the lockfile in a reviewed pull request.

The invocation:

- `--available-tools=bash,list_bash,read_bash,stop_bash` and an
  `--allow-tool` list of `cat`, `jq`, `grep`, `head`, `tail`, `wc`
  and `ls`; `--deny-tool` for `write`, `gh` and `git`. Section 3.1
  explains why this states intent rather than enforcing it.
- `--secret-env-vars=COPILOT_GITHUB_TOKEN`, so the CLI redacts the
  credential from its own output.
- `--no-ask-user --no-custom-instructions --disable-builtin-mcps
  --no-auto-update --no-color`: no interaction, no instructions from
  the runner's home, no MCP servers, no self-update at run time.
- `--share=artefacts/session-summary.md`: the session summary whose
  last fenced `json` block is the verdict.
- `--usage-output-file artefacts/usage.json` and
  `--log-dir=artefacts/copilot-logs`.
- `COPILOT_HOME` under `$RUNNER_TEMP`, deleted afterwards along with
  the CLI's spilled tool-output files in the temp directory. That
  cleanup is hygiene on a single-use VM, not secure erasure, and it
  runs with `continue-on-error` so it can never gate the apply job.

The session has `timeout-minutes` from `max_runtime_minutes`, and
the job fifteen minutes more for toolchain setup and upload. The
session artifact uploads with `overwrite: true` under one name per
target, so a rerun of a matrix entry replaces its own earlier session
rather than colliding with it.

The agent job runs `harden-runner` in **audit** mode whatever the
caller's `egress_policy`: the model backend, `api.githubcopilot.com`,
is not in the organisation allow-list, and the job holds nothing a
block could protect beyond the model credential. The trusted jobs
run in block mode (section 9).

No other harness ships. Claude and Gemini paths existed in earlier
drafts of the bots, untested by any run; the organisation removed
them, and a bot that wants another harness adds it with the same
discipline, a lockfile pin and contract tests, rather than
resurrecting the removed code.

## 7. Shared modules

Five modules are the template's contribution to every bot. Each bot
carries them verbatim; a defect or an improvement lands here first
and propagates. A bot never patches its own copy alone.

### 7.1 `bot_github.py`

GitHub reads and writes through the `gh` CLI, kept apart from any
policy so the rules in a bot's scripts stay readable.

Public functions: `api_object`, `api_list` (paginated), `api_write`,
`graphql`, `run_gh`, `is_absent`, `parse_status`, `require_str`,
`require_int`, `require_sha` and `safe_message`. `GitHubError`
carries the HTTP status its caller passes or, failing that, the one
`parse_status` finds at the end of a line of `gh`'s stderr, in either
form `gh` prints it: `(HTTP 404)` after a JSON message, or
`gh: HTTP 403` alone for any other reply. Reads retry three times on
`500`-`504` with a two-second delay; writes never retry. Every call
has a sixty-second timeout and pins `API_VERSION`. `REPO_RE` and
`SHA_RE` are the patterns the other modules share.

A bot's `prepare.py` and `apply.py` import it for every GitHub call.
Nothing here decides whether the bot may act; callers decide that
first.

### 7.2 `bot_evidence.py`

Two parts: the `verify` command the workflow runs in every consumer
job, and the cap table for what an untrusted session may hand to a
trusted job.

`verify --directory DIR --expect NAME=SHA256 [--expect ...]` checks
the exact bytes of each named file against the digest the trusted
producer published as a job output, never against a value found in
the downloaded artifact, reading each file as a regular file under
`MAX_EVIDENCE_BYTES` (16 MiB). `SESSION_FILES` names
`session-summary.md` (required, 8 MiB) and `usage.json` (optional,
1 MiB); `artifact_fetch` enforces those caps while extracting.
`read_regular` is the bounded reader the placeholders use for every
file they open.

A bot that needs more from its sessions extends `SESSION_FILES`;
the trusted jobs read nothing else from a session.

### 7.3 `artifact_fetch.py`

Fetches an artifact without letting it expand unbounded.
`download-artifact` extracts an archive before anything can check
its size, so a small, highly compressible upload could fill the
runner's disk. This fetch finds the artifact through the API,
refuses one whose zip exceeds the sum of the permitted file caps
plus 1 MiB of overhead, streams the zip to disk under that limit,
inspects the zip directory (at most 64 entries), and extracts the
permitted files alone, each read with a hard stop so a zip that lies
about sizes cannot expand past its cap.

The download retries a failure that `gh` reports without an HTTP
status, meaning no reply arrived, and a `500`, `502`, `503` or `504`,
with the read backoff of section 7.1: a runner can lose DNS for a
second while harden-runner restarts its resolver. Any other status or
a refusal ends it at once. One five-minute deadline spans every
attempt, and a backoff that would reach it ends the download instead.
The error names the attempt and the status but never quotes `gh`,
whose message for a failed redirect carries the signed storage URL.

Two profiles: `session` applies `SESSION_FILES` to an agent session
from the current run, and `ledger` applies `LEDGER_FILES` to a prior
run's ledger. Exit status 0 means accepted, with `artifact_id=<id>`
on stdout; 3 means no such artifact; 4 means refused. The apply job
turns 3 and 4 into a typed failure reason for the target rather than
a job failure.

### 7.4 `preflight.py`

The run-time checks of section 4, as four commands: `workflow`
(contract tests and zizmor from the pinned checkout), `config`
(`config/bot.json`, credential shapes, egress coordinate, live
assets skew), `identity` (the minted slug) and `token` (a read token
cannot push). Each prints one line per check and nothing else; no
value a check read is ever echoed. The workflow calls them under the
template's credential names, which the contract tests pin.

### 7.5 `ledger.py`

The run-to-run memory (section 8). `fetch --repository OWNER/REPO
--output FILE [--artifact-name NAME] [--limit N]` merges the newest
five published ledgers, through `artifact_fetch` under the `ledger`
profile, and writes an empty ledger when no run has published.
`load_ledger`, `merge`, `already_assessed` and `record` are the
functions a bot's `prepare.py` and `report.py` call. `RECORDED_VERDICTS`
is the one constant a bot edits.

## 8. The ledger

Every run's report job uploads `ledger.json` as the `bot-ledger`
artifact: the entries it inherited plus one for every target this
run reached a recorded verdict on. The next run's prepare job merges
the newest five of those artifacts and skips any target already
handled at the same head, so an unchanged target costs one agent
session rather than one every schedule tick. An unreadable ledger
artifact produces a warning and drops out of the merge; the cost is
re-assessment of the targets it covered.

An entry names a target as `repository`, `number` and `head_sha`,
with `verdict`, `dry_run`, `run_id`, `assessed_at` and an optional
`tier`. A bot whose targets are issues rather than pull requests uses
the issue's number and the branch head it looked at. The recorded
verdicts are `done`, `would-do` and `needs-human`; a skip or a
failure is not recorded, so the next run looks at that target again.

Dry runs record their verdicts too, so a rollout in dry-run does not
repeat the same work every run, but a live run ignores dry-run
entries: the first live run after the flip must still act on what
dry runs reported. `already_assessed` encodes the asymmetry: a
live entry always counts; a dry-run entry counts for another dry run
alone.

The placeholder `prepare.py` requires the ledger file to exist and
passes it through; a bot's selection calls `already_assessed` on each
candidate.

## 9. Egress

The trusted jobs run `harden-runner` in the mode `egress_policy`
names, `block` by default, with the allow-list that
`lfreleng-actions/harden-runner-block-action` loads from the
coordinate in `egress_allow_config`. The bundled callers pin that
coordinate to a commit (`@<40 hex>`), and the pre-flight gate refuses
block mode with an unpinned coordinate or an empty loaded list. The
prepare job prints the allow-list summary to the step summary; the
apply and report jobs load it without a summary.

The agent job always audits (section 6). A bot that needs the agent
to reach a further endpoint changes nothing here; a bot that needs a
trusted job to reach one adds it to the organisation allow-list
through that repository's own review.

## 10. Inputs and callers

### 10.1 Reusable workflow (`bot.yaml`)

<!-- markdownlint-disable MD013 -->

| Input                   | Type    | Default                          | Meaning                                                              |
| ----------------------- | ------- | -------------------------------- | -------------------------------------------------------------------- |
| `org`                   | string  | required                         | Target owner; live runs require an organisation App                  |
| `dry_run`               | boolean | `true`                           | Run the agent and the checks; write nothing                          |
| `model`                 | string  | `claude-opus-5.5`                | Copilot CLI model identifier, lower-case                             |
| `repositories`          | string  | `''`                             | Repositories to scan; commas and/or spaces; empty scans the owner    |
| `exclude_repos`         | string  | `''`                             | Comma-separated repositories to skip; overrides the bundled list     |
| `max_targets`           | string  | `'20'`                           | Targets to hand to the agent at most; `0` lifts the cap              |
| `max_concurrent_agents` | string  | `'5'`                            | Agent sessions running at once, 1-30                                 |
| `max_runtime_minutes`   | string  | `'20'`                           | Wall-clock budget for each session, 1-120                            |
| `skip_agent`            | boolean | `false`                          | Plumbing test: prepare and report without sessions                   |
| `egress_policy`         | string  | `block`                          | `harden-runner` egress for trusted jobs: `audit` or `block`          |
| `egress_allow_config`   | string  | `''`                             | `harden-runner-block-action` config coordinate                       |
| `github_app_client_id`  | string  | `''`                             | GitHub App client id; empty limits runs to dry-run                   |
| `assets_repository`     | string  | `lfreleng-actions/bots-template` | Trusted repository providing the prompt and scripts                  |
| `assets_ref`            | string  | `''`                             | Commit, tag or branch of the assets; empty resolves the caller's SHA |

<!-- markdownlint-enable MD013 -->

<!-- markdownlint-disable MD013 -->

| Secret                   | Meaning                                                                   |
| ------------------------ | ------------------------------------------------------------------------- |
| `copilot_token`          | Fine-grained PAT with Copilot Requests; the one credential the agent gets |
| `github_app_private_key` | GitHub App private key, used in trusted jobs alone                        |

<!-- markdownlint-enable MD013 -->

The prepare job validates every numeric input against its range,
the model against `^[a-z0-9.-]+$` and the repository list against
`^[A-Za-z0-9_.,-]*$` before anything else runs. A bot that adds an
input declares it here, forwards it in the callers and adds it to
`InputContracts` in the tests (see [Customising](CUSTOMISING.md)).

### 10.2 Scheduled caller (`bot-cron.yaml`)

Dispatch inputs `dry_run`, `model` (a display-name choice the
`options` job maps to an identifier), `repositories` and
`max_targets`. The `schedule:` block ships commented out, with a
weekday 07:00 UTC example; a workflow needs one live trigger and the
dispatch form is it until the bot earns a schedule. Scheduled runs
stay dry-run until the rollout clears them; a manual dispatch takes
the operator's choice. The caller passes `org` as
`github.repository_owner`, so a fork's scheduled runs stay inside the
fork, and pins `assets_repository` and `assets_ref` to its own
commit. Disabling the workflow in the Actions UI is the kill switch.

### 10.3 Testing caller (`testing.yaml`)

Pull request runs are secretless by design. A same-repository pull
request can change the reusable workflow and the scripts, so a check
that held a model credential would hand it to the code under review.
PR runs set `skip_agent: true` and pass no secret, proving
the plumbing from the pull request head: the ledger fetch, the
selection, the evidence upload and the report. Two legs run in
parallel to prove invocation namespaces never collide. They run in
block mode with the organisation allow-list, as the scheduled caller
does, so a pull request also proves the trusted jobs' egress.

The full agent session, dry-run with read access alone, runs from
`workflow_dispatch` instead: a maintainer triggers it against a
chosen ref with `repositories` and `max_targets`, and it holds the
model credential alone. The same workflow runs the offline suite
under `uv run --locked` and builds the documentation with
`mkdocs build --strict`.

### 10.4 Concurrency

Live runs hold one lock per caller repository and owner across the
whole pipeline. Dry runs write nothing, so they lock within their own
caller run and never cancel or wait on a live run. The cron caller
serialises its live runs on one group; a dry dispatch takes a group
of its own. The testing caller supersedes a pull request's older run
on a new push and gives each manual dispatch its own group.

## 11. Artefacts and retention

<!-- markdownlint-disable MD013 -->

| Artifact                            | Producer | Content                                                                | Retention |
| ----------------------------------- | -------- | ---------------------------------------------------------------------- | --------- |
| `bot-evidence-<ns>`                 | prepare  | `selection.json`, `matrix.json`, `ledger.json`, `selection-summary.md` | 7 days    |
| `bot-session-<ns>-<key>`            | agent    | `session-summary.md`, `usage.json`, `prompt.md`, `copilot-logs/`       | 7 days    |
| `bot-result-<ns>-<key>-<attempt>`   | apply    | `check.json`, `check-summary.md`, `result.json`                        | 90 days   |
| `bot-report-<ns>-<attempt>`         | report   | `report.md`, `report.json`                                             | 90 days   |
| `bot-ledger`                        | report   | `ledger.json`                                                          | 90 days   |

<!-- markdownlint-enable MD013 -->

`<ns>` is the prepare job's namespace, `run-attempt-uuid`, and
`<key>` the target key. Trusted evidence downloads use the
producer's artifact ID and verify the digests; the session download
uses the name within the run and the bounded extractor; the report
job gathers results by name pattern within the namespace. Result and
report names append the run attempt so a rerun avoids immutable-name
conflicts while producer artifacts survive. The ledger's fixed name
is unique within a run, which is all the upload requires; the next
prepare job fetches the newest five by that name.

A bot renames the `bot-` prefix after itself in every name and in
the `--artifact-name` the ledger fetch passes.

## 12. Publishing from bot fork organisations

A bot whose write is code, a branch and a pull request rather than a
label or a review, follows one further rule: an AI agent never pushes
a branch to a target repository and never opens a pull request from
one. A branch in the target is a same-repository branch, and GitHub
runs the target's `pull_request` workflows for it as trusted code,
with the repository's secrets and a token that can write, before any
maintainer has read the change.

The bot forks the target into the bot organisation for that project,
pushes there, and opens the pull request from the fork. The target's
workflows then run the change as an outside contribution: no
secrets, a token that cannot write, and the organisation's approval
gate before any workflow runs.

<!-- markdownlint-disable MD013 -->

| Target organisation                                   | Fork organisation                 |
| ----------------------------------------------------- | --------------------------------- |
| `onap`                                                | `lfreleng-bot-forks-onap`         |
| `opendaylight`                                        | `lfreleng-bot-forks-opendaylight` |
| `o-ran-sc`                                            | `lfreleng-bot-forks-oransc`       |
| every other organisation, `lfreleng-actions` included | `lfreleng-bot-forks`              |

<!-- markdownlint-enable MD013 -->

The mapping is explicit: `o-ran-sc` maps to `oransc`, and nothing
derives a fork organisation from a target name. The fork
organisations hold bot forks and nothing else, have Actions switched
off, and grant access to the bot App installation alone. A bot that
cannot reach the right fork organisation stops and says so; it does
not fall back to a branch in the target. `github-code-monkey-bot`
is the reference implementation; its `docs/ORG-AGENTS-GUIDANCE.md`
is the organisation's statement of the rule.

## 13. Contract tests and what they pin

`tests/test_workflow.py` parses the three workflow files and pins
the facts the design relies on, so a workflow edit that moves a
credential or a gate fails a test rather than merging unnoticed. The
pre-flight gate re-runs the same tests at run time from the pinned
assets.

<!-- markdownlint-disable MD013 -->

| Group           | What it pins                                                                                                                                                                                                                                                                                                                               |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Top level       | Default `permissions: {}`; concurrency separates dry runs from live runs; every action pinned to a commit; every checkout drops credentials; `harden-runner` leads every job                                                                                                                                                               |
| Prepare         | Live runs require the App; the read token holds no write; the gate runs after pinning and before the read mint under the template credential names; zizmor at a fixed version; the ledger comes from the native token before selection; `skip_agent` and an empty selection stop the sessions; the full provenance set reaches the outputs |
| Mint provenance | Every mint's `owner` is the trusted `org` input; the write mint's scope is one recorded repository                                                                                                                                                                                                                                         |
| Agent           | No App key; read permissions alone; budget and fan-out come from prepare; evidence arrives by ID and passes verification before use; the prompt comes from the pinned assets; the CLI invocation carries the allow and deny lists; the session artifact name stays stable per key; cleanup never gates apply                               |
| Apply           | Runs for every entry after the agent; evidence passes verification before the session fetch; the write mint runs on the live path alone; every entry uploads a typed result                                                                                                                                                                |
| Report          | Gathers namespaced results; the ledger artifact has a fixed name and long retention                                                                                                                                                                                                                                                        |
| Inputs          | The declared inputs, secrets and defaults                                                                                                                                                                                                                                                                                                  |
| Cron caller     | Dispatch stays dry-run unless the operator says otherwise; the form; display names map to identifiers; credentials forwarded and assets pinned; dry dispatches outside the live group; steps pinned                                                                                                                                        |
| Testing caller  | Plumbing is secretless selection of the PR head; the manual dry run holds the model credential alone; regression runs the locked suite; steps pinned                                                                                                                                                                                       |

<!-- markdownlint-enable MD013 -->

Change these tests together with the design, never to make a
workflow edit pass. Each other test file covers one script module:
`test_apply`, `test_artifact_fetch`, `test_bot_evidence`,
`test_bot_github`, `test_ledger`, `test_preflight`, `test_prepare`
and `test_report`. The suite runs offline under `unittest` and needs
no credential; the `prek` hook runs it on every commit.

## 14. Repository layout

```text
.github/workflows/bot.yaml               reusable workflow (§2, §10.1)
.github/workflows/bot-cron.yaml          schedule and dispatch caller
.github/workflows/testing.yaml           PR plumbing, manual dry-run,
                                         regression, docs build
.github/workflows/documentation.yaml     mkdocs to GitHub Pages
prompt/task.md                           agent task, with replace markers
config/bot.json                          the App slug the gate checks
config/excluded-repos.txt                repositories the scan skips
tools/copilot-cli/                       pinned CLI lockfile
scripts/bot_github.py                    shared: gh wrapper (§7.1)
scripts/bot_evidence.py                  shared: digests, caps (§7.2)
scripts/artifact_fetch.py                shared: bounded fetch (§7.3)
scripts/preflight.py                     shared: run-time gate (§7.4)
scripts/ledger.py                        shared: run-to-run memory (§7.5)
scripts/prepare.py                       placeholder: selection
scripts/apply.py                         placeholder: check and write
scripts/report.py                        placeholder: report and ledger
tests/test_<module>.py                   offline unittest suite
tests/test_workflow.py                   workflow contract tests (§13)
pyproject.toml, uv.lock                  Python tooling
.pre-commit-config.yaml, .aislop/        lint hooks, aislop at 100
docs/DESIGN.md                           this document
docs/CUSTOMISING.md                      what a bot keeps and replaces
docs/setup/README.md                     operator's guide
```

Linting runs through `prek` hooks including the offline suite,
gitleaks, gitlint, yamllint, ruff, mypy, basedpyright, write-good,
shellcheck, markdownlint, reuse, actionlint, codespell,
gha-workflow-linter, the JSON schema checks for workflows and
`aislop` at threshold 100.

## 15. Data contracts

The files the jobs exchange, as the template's placeholders emit
them. Every reader treats a file from a less trusted producer as
hostile input: typed, bounded and cross-checked against the trusted
`selection.json`. A bot extends these shapes; it does not remove the
fields named here, which the workflow's provenance checks and the
shared modules rely on.

### 15.1 `selection.json` (prepare to agent, apply, report; trusted)

```json
{
  "schema": 1,
  "org": "lfreleng-actions",
  "dry_run": true,
  "model": "claude-opus-5.5",
  "targets": [
    {
      "key": "repo-157",
      "repository": "lfreleng-actions/repo",
      "repo_name": "repo",
      "number": 157,
      "head_sha": "<40 hex>"
    }
  ]
}
```

Each target carries the five `TARGET_KEYS`: `key` is a short
`[A-Za-z0-9_.-]+` token unique within the run and names the target's
artifacts; `repository` is `owner/name`; `repo_name` is the bare
name that scopes the write mint; `number` is the issue or pull
request; `head_sha` is the commit the agent looked at. A bot adds
whatever its packet and its offline check need to each target, and
top-level fields such as skip counts; the agent's packet step
projects one target out of this file by `key`.

### 15.2 `matrix.json` (prepare to workflow)

```json
{"include": [{"key": "repo-157",
              "repository": "lfreleng-actions/repo",
              "repo_name": "repo", "number": 157,
              "head_sha": "<40 hex>"}]}
```

The agent and apply jobs fan out over `include`. The agent job
checks each field against a strict pattern and checks that the
`head_sha` in the matrix equals the one in the verified selection;
`repo_name` scopes the write token.

### 15.3 `ledger.json` (report to the next run's prepare; trusted)

```json
{
  "schema": 1,
  "entries": [
    {
      "repository": "lfreleng-actions/repo",
      "number": 157,
      "head_sha": "<40 hex>",
      "verdict": "would-do",
      "tier": null,
      "dry_run": true,
      "run_id": 123456789,
      "assessed_at": "2026-10-05T10:00:00Z"
    }
  ]
}
```

`verdict` is one of `RECORDED_VERDICTS`. `check_entry` drops any key
not listed here, so a bot that wants to remember more extends the
module rather than the file.

### 15.4 Agent verdict (agent to apply; untrusted)

The last fenced `json` block of `session-summary.md`:

```json
{
  "schema": 1,
  "key": "repo-157",
  "repository": "lfreleng-actions/repo",
  "number": 157,
  "head_sha": "<40 hex>",
  "verdict": "needs-human",
  "summary": "One or two sentences on what you found and why.",
  "findings": [{"area": "other", "note": "..."}],
  "injection_attempts": []
}
```

`verdict` is `act` or `needs-human`. The placeholder check refuses a
block whose `schema` is not 1, whose `repository`, `number` or
`head_sha` differs from the trusted target, whose verdict token is
unknown or whose `summary` is not a string. A bot renames the
verdict tokens to its own vocabulary in `prompt/task.md` and
`AGENT_VERDICTS` together, and adds the fields its corroboration
reads.

### 15.5 `check.json` (apply, offline; trusted)

```json
{
  "schema": 1,
  "approvable": false,
  "target": {"key": "repo-157", "repository": "lfreleng-actions/repo",
             "number": 157, "head_sha": "<40 hex>"},
  "verdict": "needs-human",
  "summary": "bounded to 2000 characters",
  "reasons": ["the session ended without a verdict block"]
}
```

`approvable` is true when the reasons list is empty and the agent
said `act`; the step prints `approvable=true|false` as its output
and the write mint runs on `true`, off dry-run, with a client id.
`verdict` is `failed` when the fetch reported a missing or refused
session and `needs-human` otherwise; `apply` promotes it.

### 15.6 `result.json` (apply, per target; trusted)

```json
{
  "schema": 1,
  "key": "repo-157",
  "repository": "lfreleng-actions/repo",
  "number": 157,
  "head_sha": "<40 hex>",
  "verdict": "would-do",
  "reasons": [],
  "summary": "...",
  "write_url": null,
  "dry_run": true,
  "run_attempt": 1,
  "run_url": "https://github.com/.../actions/runs/123456789"
}
```

`verdict` is `would-do` on an approvable dry run, `done` when the
live write returned a URL, `needs-human` when it did not or the check
refused, and `failed` when the fetch failed or, through the
workflow's fallback step, when apply did not complete. The fallback
writes the same shape with `repo_name` added, so every selected
target reaches the report.

### 15.7 `report.json` and `report.md` (report; trusted)

```json
{
  "schema": 1,
  "run_id": 123456789,
  "dry_run": true,
  "targets": [
    {"key": "repo-157", "repository": "lfreleng-actions/repo",
     "number": 157, "head_sha": "<40 hex>", "verdict": "would-do",
     "reasons": [], "write_url": null}
  ],
  "recorded": 1
}
```

A selected target with no result appears as `missing` and goes
unrecorded. `report.md` renders one row per target with its verdict
and reasons; a bot's `row_for` and `describe` add the columns its
results carry. `recorded` counts the entries this run added to the
ledger.

## 16. What the template leaves to each bot

- **Selection.** What a target is, how the scan finds it, which
  skip rules apply, and what the packet holds. The template's
  `select_targets` returns an empty list.
- **The task.** Every `replace` marker in `prompt/task.md`: the
  bot's name, its rules, the packet fields, the procedure and the
  areas its findings use. The trust framing, the tool list and the
  verdict block stay.
- **Corroboration.** What the apply job checks before it believes
  an `act`: a deterministic classifier, a live re-read of the
  target, a veto list. The template checks shape and identity and
  nothing more.
- **The write.** `perform_write` and the permission the write mint
  grants. One write per target; the template makes none.
- **The report columns**, the recorded verdicts, and any extra
  session file a bot needs its agent to hand over.
- **Operational judgement**: the schedule, the rollout from dry-run
  to live, and how a wrong write is undone on that bot's targets.

Everything else, the four jobs, the evidence contract, the gate, the
shared modules, the ledger, the egress posture, the callers and the
contract tests, is the pattern, and a bot keeps it.
