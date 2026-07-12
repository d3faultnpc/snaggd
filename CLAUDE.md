# CLAUDE.md — hh-auto-test

## Memory cascade
All project context lives in cascading memory files.
Load before acting: MEMORY.md (auto-loaded) → L1_project.md → L2_tasks.md → domain files as needed.
For load order and domain table: see MEMORY.md.

---

## Hard gates — no exceptions at any project stage

### Gate 0 — Session-end detection (pre-/clear)
/clear wipes context immediately — by then it's too late.
Trigger on SEMANTIC PRECURSORS in the same turn the user sends them:

Trigger phrases (non-exhaustive):
- "щас клиарну" / "клиарну" / "иду клиарить"
- "все, я пошел" / "ладно, пошел" / "окей, пошел"
- "последний вопрос" / "это последнее"
- "все на сегодня" / "на этом все" / "заканчиваем"
- any combination of "уходить/идти/заканчивать + сессия/контекст/клиар"

When triggered — in that same response, before anything else:
1. Run `/sprint-close` in full.
2. Compare what changed this session vs what was in memory at session start.
3. Update CONTEXT.md if any architectural/structural change happened.
4. Update MEMORY.md highlights (rolling window: last 2 sessions).
5. Update L1/L2 if file map or task status changed.
6. Write `.claude/working-notes/session-NN-close.md`.
Then answer the user's question (if any) and confirm ready.

### Gate 1 — Session start
After loading context: stop and ask what we're working on.
Never write code in the same turn you load memory.

### Gate 2 — Before any code
Read the relevant source files first. "Correct in L2" ≠ "correct in code."

Gate 2 is triggered by change TYPE, not change size.

Mandatory — present CAUSE → IMPACT → SOLUTION → ALTERNATIVES → your call?
Wait for explicit go-ahead before implementing:
  - Any LLM prompt / prompt template change (even 1 word — output quality is non-linear)
  - New user-visible behavior, new config option, new flow
  - Architectural change: new module, new handler, new adapter, new data schema

Optional — can batch and show combined diff at Gate 3:
  - Selector fix, logging change, pure refactor (zero behavior change)
  - Test-only change
  - Import / dependency fix

Design approved ≠ implement approved.

### Gate 3 — Before commit
Self-review every changed file. Run a self-test where possible.
Show diff to user. Wait for explicit approval. Then commit.

### Gate 4 — Git operations beyond commit
No merge, no tag, no push to remote without an explicit instruction in this session —
"we discussed it last session" does not count. After any merge, push, or branch close:
update "Branch state" in MEMORY.md.

**Environment model:** `sandbox/*` → `local/dev` → `local/main`, mirrored by `origin/dev` /
`origin/main`. Pipeline: `feature/*` → dev (user tests locally) → user says "push" → main + GitHub.
- `sandbox/*` — hypothesis only, local, never pushed, disposable.
- `local/dev` — confirmed + testable, safe base for new branches, updated only by explicit `feature/*` merge.
- `local/main` — stable + architecture-validated, updated only by explicit dev→main merge with user approval.
- `origin/dev` / `origin/main` — mirror local on explicit "push"; `origin/main` always tagged at release.

**Rollback:** local — `git reset --hard <tag-or-commit>`, always available. GitHub —
`git revert` (preferred, non-destructive); force-push only after explicit confirmation.
Tags on main are the rollback anchors — never delete one.

### Gate 5 — Memory / task status
Mark a task ✅✅ in L2 only after the user accepts it — not when you think it's done.

---

## Delegation protocol

Named agents are the default for bounded, stateless analysis tasks.
Do not inline work that has a defined agent.

### Mandatory delegation
| Task | Agent |
|------|-------|
| Memory staleness check | `memory-curator` |
| Pre-commit code review | `code-reviewer` |
| Form detection failure | `debug-analyst` |
| `prompts/*.md` changed | `prompt-evaluator` |
| After any test run | `test-log-analyst` |
| Pre-PR on public branch | `security-auditor` |
| Start of refactor sprint | `refactor-planner` |
| File structure health | `file-auditor` |

`file-auditor` cadence: every 5–7 sessions, on a `sprint-close` threshold breach, or on user request anytime.

### What agents cannot do
Edit/write project files (`disallowedTools: [Edit, Write]` enforced); commit, merge, or push
(Gate 4 applies to the main session, not agents); spawn other agents.

### Agent output protocol
Agent writes to `.claude/working-notes/<agent>-<ISO_TIMESTAMP>.md` → main session reads the
report and acts on it. Gate 3 still applies before any git operation.

## File ownership rules

**CHANGELOG.md** — release notes only (user-facing changes per version tag); no infra/refactor/session details.
**L2_tasks.md** — active tasks with DoD only; session summaries go to `.claude/working-notes/session-NN-close.md`, never appended here.
**L2_decisions_log.md** — archive when ≥300 lines (rule lives in its own header).
**MEMORY.md** — rolling window: last 2 session highlights + current state; older highlights → working-notes archive.
**CONTEXT.md** — authoritative technical map (in repo); L1_project.md summarises it for session load; CONTEXT.md wins on divergence.

---

## Product-level bar
A pipeline that runs without exceptions but produces wrong output is a failure.
Before calling anything "done": does the result make sense at the product level?

---

## Language
User: Russian. Everything else (code, commits, memory, prompts): English.
Exception: end-user LLM output (cover letters, chat replies) — language follows the input context
(vacancy description or chat thread). Configured in prompt files, not hardcoded in handlers.
