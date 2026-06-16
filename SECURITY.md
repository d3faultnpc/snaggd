# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.3.x   | ✅        |
| < 0.3   | ❌        |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report security issues by email: **d3faultnpc@proton.me**

Please include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

You will receive a response within 7 days. Confirmed issues will be patched in the next release.

## Security Considerations

This tool handles the following sensitive data — all stored **locally only** and never committed to version control:

| File | Contents | Protected by |
|------|----------|--------------|
| `data/hh_cookies.json` | HH.ru session cookies | `.gitignore` |
| `.env` | OpenRouter API key | `.gitignore` |
| `data/resume_facts.md` | Parsed resume data | `.gitignore` |
| `data/job_preferences.md` | Job search preferences | `.gitignore` |

**All credentials and personal data remain local to the user's machine.** No data is sent to any third-party service except via the configured OpenRouter API key for LLM inference.
