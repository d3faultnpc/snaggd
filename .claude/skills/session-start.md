# /session-start

Run at the beginning of every work session. Enforces Gate 1.

## Steps

1. Load memory cascade in order:
   - MEMORY.md (auto-loaded — confirm it is current)
   - L1_project.md → summarise tech stack and active phase in one sentence
   - L2_tasks.md → list tasks that are 🔄 in-progress or blocked; flag any whose status looks stale vs git log

2. Check git state:
   ```bash
   git status
   git log --oneline -5
   git branch -v
   ```
   Compare branch state table in MEMORY.md against actual HEAD hashes.
   Flag any divergence.

3. Check for memory drift trigger:
   - If last session was more than 3 days ago → recommend running `memory-curator` agent before any code work.
   - If CONTEXT.md header date is more than 14 days old → flag for update.

4. Gate 1: Stop and ask the user what we are working on today.
   Do not write any code or edit any files before the user answers.

## What NOT to do
- Do not start implementing anything in this turn.
- Do not load domain files (hh_selectors.md, etc.) unless the user's answer requires them.
- Do not run file-auditor automatically — recommend it if thresholds look breached, but wait for user.
