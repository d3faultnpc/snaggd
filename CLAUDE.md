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
No merge, no tag, no push to remote without an explicit instruction in this session.
"We discussed it last session" does not count.
Pipeline: feature/* → dev (user tests locally) → user says "push" → main + GitHub.
After any merge, push, or branch close: update "Branch state" in MEMORY.md.

### Environment model (enforced by Gate 4)

```
sandbox/*  →  local/dev  →  local/main
               ↕                ↕
           origin/dev      origin/main
```

Tier rules — no exceptions:
- `sandbox/*`: hypothesis only. Local. Never pushed to GitHub. Disposable.
- `local/dev`: confirmed + testable. Safe base for new branches. Updated only by explicit feature/* merge.
- `local/main`: stable + architecture-validated. Updated only by explicit dev → main merge with user approval.
- `origin/dev`: mirrors local/dev on explicit "push dev" instruction. GitHub beta state.
- `origin/main`: mirrors local/main at every release. Always tagged.

Rollback protocol:
- Local: `git reset --hard <tag-or-commit>` — always available.
- GitHub: `git revert <commit>` (preferred, non-destructive). Force-push only after explicit user confirmation.
- Tags on main are the primary rollback anchors. Never delete a tag.

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

### When to run file-auditor
- Cadence: every 5–7 sessions
- When `sprint-close` reports a threshold breach
- On user request at any time

### What agents cannot do
- Edit or write project files (`disallowedTools: [Edit, Write]` enforced)
- Commit, merge, push (Gate 4 applies to main session)
- Spawn other agents

### Agent output protocol
1. Agent writes output to `.claude/working-notes/<agent>-<ISO_TIMESTAMP>.md`
2. Main session reads the report and acts on it
3. Gate 3 still applies before any git operation

## File ownership rules

**CHANGELOG.md** — release notes only. User-facing changes per version tag.
Infra changes, refactors, session details do not belong here.

**L2_tasks.md** — active tasks with DoD only.
Session summaries → `.claude/working-notes/session-NN-close.md`. Never append sessions to L2.

**L2_decisions_log.md** — archive when ≥300 lines (built into its own header).

**MEMORY.md** — rolling window: last 2 session highlights + current state.
Older highlights → working-notes archive.

**CONTEXT.md** — authoritative technical map (in repo). L1_project.md summarises it for session load.
When they diverge, CONTEXT.md wins.

---

## Product-level bar
A pipeline that runs without exceptions but produces wrong output is a failure.
Before calling anything "done": does the result make sense at the product level?

---

## Language
User: Russian. Everything else (code, commits, memory, prompts): English.
Exception: end-user LLM output (cover letters, chat replies) — language follows the input context
(vacancy description or chat thread). Configured in prompt files, not hardcoded in handlers.
