# /release

Triggered when the user says: "подтверждаю релиз", "гоу релиз", "релизим",
"выпускаем", "выпуск", "давай релиз", or any clear equivalent — OR when I
proposed releasing a major feature and the user confirmed.

Gate 4 applies: do not push or tag without this explicit trigger in the current session.

---

## Steps

### 1. Confirm version
State the next version (patch / minor based on what shipped).
Ask for confirmation if unclear.

### 2. Security audit (one combined agent)
Spawn **one** `security-auditor` agent covering both:
- Local working tree (uncommitted changes)
- Git-tracked files (`git ls-files`) — what's already on GitHub

Prompt must say: "under 400 words, findings only — no section-by-section narration."
Do NOT spawn two separate agents.
Wait for result before proceeding.

### 3. Fix blockers
Address any BLOCKER or HIGH findings from the audit before continuing.

### 4. CHANGELOG.md
Add entry for the new version. Format: `## [X.Y.Z] — YYYY-MM-DD · Theme`.
Sections: Summary, then ### New / Fixed / Changed / Security as applicable.

### 5. Commit
Stage all changed files + CHANGELOG in one commit:
```
feat: <summary> (vX.Y.Z)
```

### 6. Merge chain
```
sandbox/* → feature/* → dev → main
```
Only branches that exist and are relevant. Use `--no-ff` merges with descriptive messages.

### 7. Tag
```bash
git tag -a vX.Y.Z -m "vX.Y.Z — <one-line theme>"
```

### 8. Push
```bash
git push origin main && git push origin dev && git push origin vX.Y.Z
```

### 9. GitHub Release
```bash
gh release create vX.Y.Z --title "vX.Y.Z — <theme>" --notes "<markdown notes>"
```
Notes = condensed CHANGELOG entry (key bullet points, no full prose).

### 10. Update MEMORY.md
Update branch state table with new HEADs (verify from `git log`, never from memory).
