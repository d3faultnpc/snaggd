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

# Workspace dir: legacy symlink (HH_Auto/) or DATA_DIR
# TODO: migrate fully to DATA_DIR after onboarding is implemented
_workspace_legacy = BASE_DIR / "HH_Auto"
WORKSPACE_DIR = _workspace_legacy if _workspace_legacy.exists() else DATA_DIR


@dataclass
class Config:
    # Paths — all derived from BASE_DIR / DATA_DIR, no hardcoded usernames
    base_dir: Path = field(default_factory=lambda: BASE_DIR)
    data_dir: Path = field(default_factory=lambda: DATA_DIR)
    workspace_dir: Path = field(default_factory=lambda: WORKSPACE_DIR)
    applied_log_path: Path = field(default_factory=lambda: DATA_DIR / "applied_log.json")
    logs_dir: Path = field(default_factory=lambda: BASE_DIR / "logs")
    cookies_path: Path = field(default_factory=lambda: DATA_DIR / "hh_cookies.json")

    # Processing limits
    max_vacancies_per_session: int = int(os.getenv("MAX_VACANCIES", "3"))
    max_skips: int = 10
    max_questions_per_form: int = 5

    # Browser delays (ms)
    min_delay: int = 2000
    max_delay: int = 5000
    page_load_timeout: int = 30000
    initial_wait: int = 25000
    modal_wait: int = 5000

    # HH search URL — set via .env or --search-url flag
    hh_search_url: str = os.getenv("HH_SEARCH_URL", "")

    # LLM settings
    llm_max_input_chars: int = 3000
    cache_size: int = 1000
    cache_file: str = str(DATA_DIR / "llm_cache.json")

    # Browser
    headless: bool = os.getenv("HEADLESS", "false").lower() == "true"

    def __post_init__(self):
        self.logs_dir.mkdir(exist_ok=True)
        self.data_dir.mkdir(exist_ok=True)


CONFIG = Config()

SELECTORS = {
    'vacancy_title': '[data-qa="serp-item__title"]',
    'vacancy_description': '[data-qa="vacancy-description"]',
    'vacancy_title_page': '[data-qa="vacancy-title"]',
    'apply_button': [
        'button:has-text("Откликнуться")',
        'a:has-text("Откликнуться")',
        '[data-qa="vacancy-response"]'
    ],
    'send_button': [
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
    # chatik selectors — NOT verified on live modal
    'chatik_add_cover': '[data-qa="chatik-chat-message-applicant-action"]',
    'chatik_input': '[data-qa="chatik-new-message-text"]',
    'inputs_all': 'input[type="text"], textarea, input[type="radio"]',
    'progress_indicators': '[class*="progress"], [class*="step"], [class*="Step"]',
    'labels': 'label',
    'buttons': 'button, a[role="button"]'
}

FORM_KEYWORDS = {
    'hh_modal': ['город', 'метро', 'график', 'занятость', 'подтвердить', 'далее'],
    'questions': ['расскажите', 'почему', 'как вы', 'ваш опыт', 'ваша', 'ваше', 'ваши'],
    'salary': ['зарплат', 'salary', 'ожидани', 'доход', 'желаем', 'expected'],
    'cover': ['сопровод', 'cover letter', 'о себе', 'расскажите о себе'],
    'navigation': ['далее', 'подтвердить', 'продолжить', 'готово', 'отправить']
}
