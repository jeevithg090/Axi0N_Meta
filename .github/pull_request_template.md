## [TASK-ID] Title

### Summary
- What this PR does and why (2–5 bullets)

### Task
- Task: P?-?? — link: docs/TASKS.md#…
- Priority: must | should
- Depends on / unblocks:

### How it was tested
```
make check
<paste the summary lines of the real output>
```
Manual / live / GPU checks run (with output or numbers):

### Evidence
Screenshots, short video, measured numbers (latency, precision, etc.)

### Checklist
- [ ] Every acceptance criterion in the task is met (list them ✓)
- [ ] Required tests added and passing; coverage floors hold
- [ ] Contracts unchanged — or labelled `contract-change` with two approvals
- [ ] Model calls go through clients + replay cache; `relay.model_used` published
- [ ] No secrets, no media committed, no print(), no dead code
- [ ] Guardrail tests untouched or strengthened; privacy mode respected
- [ ] Package README / WHAT-WE-BUILT.md / licences / model map updated
- [ ] Follow-up issues filed: #
