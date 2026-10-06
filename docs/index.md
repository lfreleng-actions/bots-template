<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# GitHub Bots Template

The starting point for every GitHub bot in the `lfreleng-actions`
organisation. A bot is a reusable GitHub Actions workflow that runs a
Copilot CLI agent session in an untrusted job and makes its one
write from a trusted job under a dedicated GitHub App. The template
ships the four-job pipeline, the trust boundary, the pre-flight
security gate, the run-to-run ledger and the offline test suite; a
bot built from it supplies the selection, the prompt and the write.

## How a run works

```text
prepare (trusted)
  | ledger, selection, per-target packet
  | commit SHA, evidence ID, digests
  v
agent (untrusted, one runner per target)
  | offline packet; model credential alone, no App key or token
  v
apply (trusted, one runner per target)
  | verify evidence, corroborate the verdict, make the one write
  v
report (trusted)
  | report + the next run's ledger
```

## Bots built on this base

<!-- markdownlint-disable MD013 -->

| Bot                                                                                        | What it does                                                     |
| ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| [`github-issues-triage-bot`](https://github.com/lfreleng-actions/github-issues-triage-bot) | Labels issues                                                    |
| [`github-code-review-bot`](https://github.com/lfreleng-actions/github-code-review-bot)     | Approves trivial and low-risk pull requests                      |
| [`github-code-monkey-bot`](https://github.com/lfreleng-actions/github-code-monkey-bot)     | Authors pull requests from issues through bot fork organisations |

<!-- markdownlint-enable MD013 -->

## Where to go next

- [Setup](setup/README.md): the App, variables and secrets, the model
  credential, validation, the first dry run and going live.
- [Customising](CUSTOMISING.md): what to keep, adapt, replace and
  delete when building a bot, and how to extend the shared pieces.
- [Design](DESIGN.md): the pattern, the trust model, the pre-flight
  gate, the shared modules and the data contracts.
