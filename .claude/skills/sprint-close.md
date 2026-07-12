# /sprint-close

**Triggers:**
- User calls `/sprint-close` explicitly
- Semantic session-end signal detected (see Gate 0 in CLAUDE.md for full list):
  "щас клиарну", "все я пошел", "последний вопрос", "все на сегодня", etc.
  → /clear itself is TOO LATE — trigger on the precursor phrase, in that same response turn

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
- **CONTEXT.md drift check:** if this session touched any file map, data path, CLI flag, handler, or config —
  read CONTEXT.md header date and compare to today. If the relevant section is stale, update it now.
  Sections to check by change type:
  - New/renamed files → §12 File Registry
  - New CLI flag or env var → §9 Config, §10 Run Modes
  - New handler or FormType → §5 FormDetector & Handlers
  - Data path / profile change → §3 Data Flow, §12
  After any CONTEXT.md edit: bump the "Updated:" date in the header.
- **MEMORY.md rolling window:** keep only last 2 session highlights. Archive older ones to working-notes.

### 6. Gate 4 reminder
Do NOT push or open a PR without explicit user instruction.
End with: "Sprint complete. PR `feature/<branch> → dev` ready when you say so."
