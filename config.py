import json
import os
from pathlib import Path
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # dotenv optional — env vars can be set externally

# Project root = directory containing this file
BASE_DIR = Path(__file__).parent

# User data dir: override via DATA_DIR env var, default to ./data
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))

@dataclass
class Config:
    # Paths — all derived from BASE_DIR / DATA_DIR, no hardcoded usernames
    base_dir: Path = field(default_factory=lambda: BASE_DIR)
    data_dir: Path = field(default_factory=lambda: DATA_DIR)
    applied_log_path: Path = field(default_factory=lambda: DATA_DIR / "applied_log.json")
    logs_dir: Path = field(default_factory=lambda: BASE_DIR / "logs")
    cookies_path: Path = field(default_factory=lambda: Path(
        os.getenv("HH_COOKIES_PATH", BASE_DIR / "data" / "hh_cookies.json")))

    # Processing limits
    max_vacancies_per_session: int = int(os.getenv("MAX_VACANCIES", "3"))
    min_score: int = int(os.getenv("MIN_SCORE", "60"))
    max_skips: int = int(os.getenv("MAX_SKIPS", "10"))
    max_questions_per_form: int = 10
    # Max vacancies to collect per search URL per run (0 = no limit / old behaviour)
    vacancies_per_url: int = int(os.getenv("VACANCIES_PER_URL", "10"))

    # Browser delays (ms)
    min_delay: int = 2000
    max_delay: int = 5000
    page_load_timeout: int = 30000
    initial_wait: int = 25000
    modal_wait: int = 5000

    # HH search URLs — one per line in data/search_urls.txt
    # Supports multiple searches (different roles / resume directions)
    search_urls_path: Path = field(default_factory=lambda: DATA_DIR / "search_urls.txt")

    # LLM settings
    llm_max_input_chars: int = 5000
    cache_size: int = 15

    # Search pagination — how many pages to scrape per search URL (50 vacancies/page)
    max_pages: int = int(os.getenv("MAX_PAGES", "2"))

    # Browser
    headless: bool = os.getenv("HEADLESS", "false").lower() == "true"

    # REST API
    api_key: str = os.getenv("API_KEY", "")

    # Test forms: skip by default; set true to attempt LLM fill when no skip link exists
    fill_tests: bool = os.getenv("FILL_TESTS", "false").lower() == "true"

    def __post_init__(self):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


CONFIG = Config()

# ── OTA schema check (Task 8) ──────────────────────────────────────────────────
# Advisory only — never blocks a session, never auto-writes. candidate.json isn't read by
# the live apply loop yet (system prompt still comes from candidate.md directly, see
# llm_agent.py), so an absent/stale candidate.json can't break a running session; this only
# nudges toward keeping it in sync. Deliberately does NOT auto-run migrate_candidate.py
# (that's a real LLM call + disk write — same "no silent write to live profile data"
# principle migrate_candidate.py itself enforces via its --apply gate).

CURRENT_SCHEMA_VERSION = "1.0"


def _check_candidate_schema(data_dir: Path) -> None:
    if data_dir.parent.name != "profiles":
        return  # flat/legacy dir (e.g. --setup-keys, no active profile) — not a profile, skip

    json_path = data_dir / "candidate.json"
    if not json_path.exists():
        if (data_dir / "candidate.md").exists():
            print(f"ℹ️  [{data_dir.name}] candidate.json not found (candidate.md exists — "
                  f"pre-schema profile). Run: python scripts/migrate_candidate.py --profile {data_dir.name}")
        return  # brand-new profile, nothing onboarded yet — not an error

    try:
        parsed = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(parsed, dict):
        return  # valid JSON but not an object (e.g. [], null, a bare string) — not our shape
    version = parsed.get("schema_version")

    if version != CURRENT_SCHEMA_VERSION:
        print(f"ℹ️  [{data_dir.name}] candidate.json schema_version={version!r}, "
              f"expected {CURRENT_SCHEMA_VERSION!r} — may need updating.")


_check_candidate_schema(CONFIG.data_dir)

SELECTORS = {
    'vacancy_title': '[data-qa="serp-item__title"]',
    'vacancy_description': '[data-qa="vacancy-description"]',
    'vacancy_title_page': '[data-qa="vacancy-title"]',
    'apply_button': [
        '[data-qa="vacancy-response"]',
        'button:has-text("Откликнуться")',
        'a:has-text("Откликнуться")',
    ],
    'send_button': [
        '[data-qa="vacancy-response-letter-submit"]',  # post-apply cover form (verified 2026-05-26)
        'button:has-text("Отправить")',
        'button:has-text("Откликнуться")',
        '[data-qa="vacancy-response-send-button"]'
    ],
    'cover_textarea': [
        'textarea[placeholder*="Сопроводительное"]',
        'textarea[placeholder*="сопроводительное"]',
        '[data-qa="vacancy-response-comment-textarea"]',
        'textarea'
    ],
    'cookie_accept': 'button:has-text("Понятно")',
    'chat_link': '[data-qa="vacancy-response-link-view-topic"]',
    'form_error': '[data-qa="form-helper-error"]',
    'immediate_success': '[data-qa="vacancy-response-success-standard-notification"]',
    'letter_submit': '[data-qa="vacancy-response-letter-submit"]',
    'popup_submit': '[data-qa="vacancy-response-submit-popup"]',
    'popup_letter_input': '[data-qa="vacancy-response-popup-form-letter-input"]',
    'test_form_marker': '[data-qa="employer-asking-for-test"]',
    'test_no_questions': '[data-qa="vacancy-response-link-no-questions"]',
    'letter_toggle': '[data-qa="vacancy-response-letter-toggle"]',
    'popup_questions': '[data-qa^="vacancy-response-question"]',
    'popup_add_cover': '[data-qa="add-cover-letter"]',
    # chatik selectors — partially verified 2026-05-26; cover_input cascade unverified (update after live debug)
    # "Добавить сопроводительное" inside chatik — element type varies across HH versions.
    # Cascade: try known data-qa first, then by element type, fallback to any tag via Playwright text selector.
    'chatik_add_cover': [
        '[data-qa="chatik-chat-message-applicant-action"]',  # from spec (unverified)
        'button:has-text("Добавить сопроводительное")',
        'div:has-text("Добавить сопроводительное")',
        'span:has-text("Добавить сопроводительное")',
        'a:has-text("Добавить сопроводительное")',           # original fallback
    ],
    'chatik_input': '[data-qa="chatik-new-message-text"]',  # "Сообщение" textarea, confirmed via DOM probe 2026-05-27
    # Cover letter textarea that appears after clicking "Добавить сопроводительное"
    # Cascade: try specific data-qa first, fall back to placeholder text
    'chatik_cover_input': [
        '[data-qa="chatik-cover-letter-textarea"]',
        '[data-qa="cover-letter-textarea"]',
        'textarea[placeholder*="сопроводительн"]',
        'textarea[placeholder*="Сопроводительн"]',
    ],
    # Send button for cover letter form (inside chatik after "Добавить")
    'chatik_cover_send': [
        '[data-qa="chatik-cover-letter-submit"]',
        'button:has-text("Отправить сопроводительное")',
        'button:has-text("Сохранить")',
    ],
    # HR-bot message bubble (PERX and similar auto-interview bots)
    # Used in _handle_hr_bot_loop() to detect and read bot questions
    'chatik_bot_message': [
        '[data-qa="chatik-message-employer"]',
        '[data-qa*="chatik-message-bot"]',
        '[class*="chatik-Message_employer"]',
    ],
    'inputs_all': 'input[type="text"], textarea, input[type="radio"]',
    'progress_indicators': '[class*="progress"], [class*="step"], [class*="Step"]',
    'labels': 'label',
    'buttons': 'button, a[role="button"]',
    # Company name on the vacancy page — used for Level 1 stop_companies filter.
    # HH renders employer name as a link; data-qa is the reliable anchor.
    # Fallback checked in order if primary not found.
    'company_name': '[data-qa="vacancy-company-name"]',
    'company_name_fallback': '[data-qa="bloko-header-2"]',
    # Employer review rating score on vacancy page.
    # Located in main vacancy block (before featured section). 0 hits = no reviews → None.
    # Note: text uses comma as decimal separator ("4,6") — handled by replace(",", ".") in browser.py.
    'employer_rating': '[data-qa="employer-review-small-widget-total-rating"]',
}

FORM_KEYWORDS = {
    'hh_modal': ['город', 'метро', 'график', 'занятость', 'подтвердить', 'далее'],
    'questions': ['расскажите', 'почему', 'как вы', 'ваш опыт', 'ваша', 'ваше', 'ваши'],
    'salary': ['зарплат', 'salary', 'ожидани', 'доход', 'желаем', 'expected'],
    'cover': ['сопровод', 'cover letter', 'о себе', 'расскажите о себе'],
    'navigation': ['далее', 'подтвердить', 'продолжить', 'готово', 'отправить']
}
