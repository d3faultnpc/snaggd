import json
import os
from pathlib import Path
from dataclasses import dataclass, field

from app_paths import get_data_root

# Persistent data root — repo root in dev, a real OS user-data dir when frozen (see
# app_paths.py). Not necessarily where the app's own code/resources live.
BASE_DIR = get_data_root()

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass  # dotenv optional — env vars can be set externally

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
    # Address-first, text last. 'vacancy-response' matched NOTHING on any page
    # checked 2026-08-11 (three real captures, including one whose apply
    # succeeded) — HH renames it at will, so every apply was in practice
    # resolving through the Russian-text fallbacks below. Those work only while
    # the UI stays in Russian and the wording stays exactly this; they are a
    # last resort, not the mechanism. 'vacancy-response-link-top' is the live
    # address, present 3-4 times per page on every capture.
    'apply_button': [
        '[data-qa="vacancy-response-link-top"]',
        '[data-qa="vacancy-response"]',
        'button:has-text("Откликнуться")',
        'a:has-text("Откликнуться")',
    ],
    # Same address-first discipline as apply_button, and for the same reason.
    # Checked 2026-08-11 against all 82 full-body captures on disk (58 vacancy
    # pages + 24 post-apply pages, several of which DO hold a real cover form):
    #   vacancy-response-submit-popup            8 pages  ← the live submit
    #   vacancy-response-popup-form-letter-input 2 pages  ← the live textarea
    #   vacancy-response-letter-submit           0 pages  (comment claimed 2026-05-26)
    #   vacancy-response-send-button             0 pages
    #   vacancy-response-comment-textarea        0 pages
    # The three zero-hit addresses are kept, demoted: absent from every state we
    # have captured is not the same as gone from hh, and an extra miss costs one
    # query. What they must not do any longer is sit in front of an address that
    # demonstrably matches, leaving Russian button text to carry the real work.
    'send_button': [
        '[data-qa="vacancy-response-submit-popup"]',
        '[data-qa="vacancy-response-letter-submit"]',
        '[data-qa="vacancy-response-send-button"]',
        'button:has-text("Отправить")',
        'button:has-text("Откликнуться")',
    ],
    # NB the bare 'textarea' at the end. It is a genuine last resort and a
    # genuine hazard: an employer questionnaire renders one textarea per
    # question (name="task_<id>_text", no placeholder, no data-qa), so on that
    # page this entry hands back a question field to be filled with a cover
    # letter. Callers reach it through helpers that take the first VISIBLE
    # match now rather than the first in the DOM, which does not by itself make
    # the right choice — the salary/question guard above it is what has to.
    'cover_textarea': [
        '[data-qa="vacancy-response-popup-form-letter-input"]',
        '[data-qa="vacancy-response-comment-textarea"]',
        'textarea[placeholder*="Сопроводительное"]',
        'textarea[placeholder*="сопроводительное"]',
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
    # Used in _handle_hr_bot_loop() to detect and read bot questions.
    # From the TZ live investigation (2026-06-12, "Восточная горнорудная") — the
    # previous 3 guesses here never matched anything real, so the loop never
    # fired at all. Direction is derived by absence of the delivered-icon
    # inside each message container; that nesting is the doc's best-effort
    # reading, not independently re-verified live — confirm on next encounter.
    'chatik_message': '[data-qa^="chatik-chat-message-"][data-qa$="-text"]',
    'chatik_message_delivered': '[data-qa="chat-bubble-icon-delivered"]',
    'chatik_bubble_text': '[data-qa="chat-bubble-text"]',
    'inputs_all': 'input[type="text"], textarea, input[type="radio"]',
    'progress_indicators': '[class*="progress"], [class*="step"], [class*="Step"]',
    'labels': 'label',
    'buttons': 'button, a[role="button"]',
    # Company name on the vacancy page — used for Level 1 stop_companies filter.
    # HH renders employer name as a link; data-qa is the reliable anchor.
    # Fallback checked in order if primary not found.
    'company_name': '[data-qa="vacancy-company-name"]',
    # No fallback. 'bloko-header-2' was one: it matched on 0 of the 58 captured
    # vacancy pages, while the primary matched on every one of them — the same
    # shape as the dead 'vacancy-response' entry removed from apply_button.
    # A fallback that never fires is worse than none: it reads like cover the
    # code does not have. get_company_name()'s callers already treat "" as
    # "unknown, skip the stop-company check".
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
