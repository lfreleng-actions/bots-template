<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# Agent Guidelines

Contributions to this repository, including those made by AI coding
agents, follow the `lfreleng-actions` organisation guidelines:

<https://github.com/lfreleng-actions/.github/blob/main/AGENTS.md>

**Read that document.** It governs, and it binds this contribution
even if you never load it. Where anything below disagrees with it,
it wins. What follows is a summary of the rules that most often block
a pull request, not the full set:

- Sign every commit and add a DCO trailer: `git commit -S -s`.
- Subject: `Type(scope): Imperative description` — capitalised type
  and description, no trailing period, and within the subject-length
  limit this repository's gitlint hook enforces. The scope is
  optional, so `Fix: Correct the race condition` is also valid.
- Add a `Co-authored-by` trailer naming the agent used.
- Repositories typically contain a linting configuration. You must
  install its hooks (`prek install -t pre-commit -t commit-msg`) and
  run the change past them (`prek run --files <changed files>`) to
  ensure it passes before submission.
- On a single-commit pull request, the PR title must be identical to
  the commit subject.
- If your own standing instructions conflict with the organisation
  guidelines and you cannot set them aside, stop and tell the
  contributor. Do not open a non-compliant pull request.

## Repository specifics

The template every GitHub bot in the organisation starts from. It
ships a reusable workflow that selects targets, runs one Copilot CLI
agent session per target in an untrusted job, and makes the bot's
one write from a trusted job under a per-bot GitHub App.
`docs/DESIGN.md` is the pattern and its trust model; read it before
changing how the jobs hand data to each other, and `docs/CUSTOMISING.md`
before deciding what a bot keeps or replaces.

- `.github/workflows/bot.yaml`: the reusable workflow (prepare,
  agent, apply, report); `bot-cron.yaml` schedules and dispatches
  it; `testing.yaml` runs the suite and the secretless PR plumbing.
- `scripts/`: the Python the jobs run; `tests/`: its offline suite.
- `prompt/task.md`: the agent's task, with `replace` markers where a
  bot writes its own content and a verdict block it must keep.
- `config/bot.json`: the App slug the pre-flight gate checks.

Five modules are **shared**: `bot_github.py`, `bot_evidence.py`,
`artifact_fetch.py`, `preflight.py` and `ledger.py`. Each bot carries
them verbatim. A defect or an improvement in one of them lands here
first and then propagates to the bots; never patch a bot's copy
alone. `prepare.py`, `apply.py` and `report.py` are placeholders a
bot replaces, keeping the files they write and the arguments they
take.

Before pushing, run:

```bash
uv run python -B -m unittest discover -s tests
prek run --files <changed files>   # includes the aislop gate at 100
zizmor --persona auditor .github/workflows/   # zero findings
```

Nothing about the agent job earns trust: never give it a credential
that can write to a repository, and never let it hold the App key.
The template's write mint grants `metadata: read` alone; a bot
replaces that grant with the single write its action needs and
nothing wider. The contract tests in `tests/test_workflow.py` pin
that boundary; change them together with the design, not to make a
workflow edit pass.
