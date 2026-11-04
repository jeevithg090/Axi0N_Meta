# Security policy

Rulecam watches cameras and keeps an evidence log, so security and privacy problems matter more
here than in most projects. Thank you for reporting them carefully.

## How to report

**Please do not open a public issue.** Report it privately instead:

1. Open the repository's **Security** tab.
2. Click **Report a vulnerability** and describe the problem.

Only the maintainers can see the report. Please include what you found, the steps to reproduce
it, and what an attacker could do with it. We aim to answer within a few days.

## What counts

Anything that breaks a promise in the README, for example:

- leaked keys or tokens (in the code, the logs, the evidence log or the UI);
- a way to change, drop or reorder evidence records without the chain or the Merkle root
  noticing;
- unblurred faces or person pixels leaving the system in a mode that promises to blur or drop
  them (prototype mode is the only mode that sends raw frames, and it is off by default);
- any way to identify a person, or to get per-person metrics, outside prototype mode;
- text in a scene (a sign, a sticker, a PDF) that makes a model follow its instructions;
- an alert, action or interlock that can be triggered or silenced without the owner's approval.

## Supported versions

Only the `main` branch is supported. Fixes are made there.
