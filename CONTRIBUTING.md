# Contributing

## Adding a new site adapter

Each job site lives in its own folder under `adapters/<site>/`. The adapter implements the `SiteAdapter` ABC from `adapters/base.py`.

### Minimal structure

```
adapters/
└── mysite/
    ├── __init__.py
    ├── adapter.py    ← MyAdapter(SiteAdapter)
    ├── browser.py    ← Playwright page actions
    ├── detector.py   ← form classification (DOM only, no LLM)
    └── handlers/
        ├── __init__.py
        └── base.py   ← form types for this site
```

### SiteAdapter contract

```python
from adapters.base import SiteAdapter

class MyAdapter(SiteAdapter):
    def name(self) -> str:
        return "mysite.com"

    def auth_method(self) -> str:
        return "cookie"  # or "oauth" / "form"

    def verify(self) -> bool:
        # pre-flight: check cookies exist, search URLs configured, etc.
        ...

    def start(self) -> bool:
        # launch browser, load cookies, return True if ready
        ...

    def close(self) -> None:
        ...

    def get_vacancies(self) -> list:
        # return [(url, title, index), ...]
        ...

    def process_vacancy(self, url, title, index, cover_gen, hr_matcher, logger) -> dict:
        # open vacancy → extract text → click apply → detect form → fill → submit
        # return applied_log entry dict
        ...
```

### Guidelines

- **No LLM in detector** — `detector.py` must classify forms using DOM signals only (selectors, text content). LLM tokens are expensive; classification should be < 1s.
- **Always `type(text, delay=10)`** — never `textarea.fill()` on React-based sites; it bypasses input event handlers and the field appears empty on submit.
- **Playwright cookies** — store in `data/<site>_cookies.json`, load via `page.context.add_cookies()`.
- **Unknown form → save debug snapshot** — always, not only in `--debug` mode. Helps users report issues.
- **Log schema** — return a dict matching `applied_log.json` schema (see `CONTEXT.md` for full field list).

### Testing your adapter

```bash
# Use sandbox to avoid real submissions
DATA_DIR=sandbox/data python main.py --debug --max 1
```

Run `python scripts/check_sensitive.py` before any push — it scans for hardcoded paths, tokens, and personal data.

### Priorities for next adapters

| Site | Method | Priority |
|------|--------|----------|
| Greenhouse | REST API | P0 |
| Lever | REST API | P0 |
| Workday | Playwright | P0 |
| SuperJob | OAuth API (verify first) | P1 |
| LinkedIn | Playwright + Patchright | P2 |

> **Note:** Indeed uses DataDome bot protection (skip). Zarplata.ru redirects to HH.ru (skip).
