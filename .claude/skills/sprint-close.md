# /sprint-close

Run at the end of a sprint before any commit. Enforces Gate 3 + Gate 5.

## Steps

### 1. Self-review
For every file changed in this sprint:
- Read the file, check that the change matches the approved design.
- For .py files: run `venv/bin/python3 -m py_compile <file>` — must pass clean.
- For prompts/*.md: flag as Gate 2 trigger if any wording changed.

### 2. Show diff
```bash
git diff HEAD
git status
```
Present a summary: what changed, why, any concerns.

### 3. Session close note
Write `.claude/working-notes/session-NN-close.md` (use actual session number):
- What was done (per task, one line each)
- Deferred items and why
- Next sprint recommendation

**Rule:** Session summary goes here — NOT appended to L2_tasks.md.
L2_tasks.md contains active tasks only.

### 4. File health check
Report line counts on key files:
```bash
wc -l ~/.claude/projects/*/memory/L2_tasks.md \
       ~/.claude/projects/*/memory/L2_decisions_log.md \
       ~/.claude/projects/*/memory/MEMORY.md \
       CONTEXT.md
```

Thresholds:
- `L2_tasks.md` > 500 lines → recommend running `file-auditor` next session
- `L2_decisions_log.md` > 280 lines → recommend running `file-auditor` next session
- MEMORY.md session highlights > 2 → prune to 2 now, archive the rest to working-notes

### 5. Memory update
- Update branch state table in MEMORY.md (run `git log --oneline <branch> | head -1` first — never from memory).
- If any task reached DoD this sprint: mark as `✅✅` in L2_tasks.md **only after explicit user acceptance**.

### 6. Gate 4 reminder
Do NOT push or open a PR without explicit user instruction.
End with: "Sprint complete. PR `feature/<branch> → dev` ready when you say so."
