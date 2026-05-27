# CLAUDE.md — hh-auto-test

## Memory cascade
All project context lives in cascading memory files.
Load before acting: MEMORY.md (auto-loaded) → L1_project.md → L2_tasks.md → domain files as needed.
For load order and domain table: see MEMORY.md.

---

## Hard gates — no exceptions at any project stage

### Gate 1 — Session start
After loading context: stop and ask what we're working on.
Never write code in the same turn you load memory.

### Gate 2 — Before any code
Read the relevant source files first. "Correct in L2" ≠ "correct in code."
For every task — including pre-approved ones — present:
  CAUSE → IMPACT → SOLUTION → ALTERNATIVES → your call?
Wait for explicit go-ahead this session. Design approved ≠ implement approved.

### Gate 3 — Before commit
Self-review every changed file. Run a self-test where possible.
Show diff to user. Wait for explicit approval. Then commit.

### Gate 4 — Git operations beyond commit
No merge, no tag, no push to remote without an explicit instruction in this session.
"We discussed it last session" does not count.
Pipeline: feature/* → dev (user tests locally) → user says "push" → main + GitHub.
After any merge, push, or branch close: update "Branch state" in MEMORY.md.

### Gate 5 — Memory / task status
Mark a task ✅✅ in L2 only after the user accepts it — not when you think it's done.

---

## Sub-agents

Sub-agents work in `sandbox/*` branches — disposable, hypothesis-driven.
After a sub-agent run: log outcome in L2_decisions_log.md before acting on the result.
A working sub-agent solution merges to `dev` for local user testing first.
It goes to GitHub only after explicit user approval in that session.
Never present a sub-agent finding as a decision — it is evidence for grooming.

---

## Product-level bar
A pipeline that runs without exceptions but produces wrong output is a failure.
Before calling anything "done": does the result make sense at the product level?

---

## Language
User: Russian. Everything else (code, commits, memory, prompts): English.
Exception: end-user LLM output (cover letters, chat replies) — language follows the input context
(vacancy description or chat thread). Configured in prompt files, not hardcoded in handlers.
