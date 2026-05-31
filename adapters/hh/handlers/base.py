from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from typing import Optional

class FormType(Enum):
    """HH application form types."""
    HH_MODAL_STEP1 = "hh_modal_step1"
    HH_MODAL_STEP2 = "hh_modal_step2" 
    COVER_ONLY = "cover_only"
    EMPLOYER_QUESTIONS = "employer_questions"
    SALARY_FORM = "salary_form"
    CHAT_INTERFACE = "chat_interface"
    TEST_FORM = "test_form"
    UNKNOWN = "unknown"

@dataclass
class FormInfo:
    """Detected form metadata."""
    form_type: FormType
    input_count: int
    has_salary_field: bool = False
    has_cover_field: bool = False
    has_chat_link: bool = False
    has_form_error: bool = False
    has_response_submit: bool = False  # vacancy-response-letter-submit visible → cover form ready
    has_popup_questions: bool = False
    has_task_questions: bool = False
    has_test_form: bool = False
    has_progress: bool = False
    progress_step: Optional[int] = None
    labels: list = None
    placeholders: list = None
    buttons: list = None
    
    def __post_init__(self):
        if self.labels is None:
            self.labels = []
        if self.placeholders is None:
            self.placeholders = []
        if self.buttons is None:
            self.buttons = []

@dataclass
class ProcessResult:
    """Form processing result."""
    success: bool
    status: str  # applied, skipped_salary, skipped_error, etc.
    reason: str
    scenario: str = "unknown"  # A, B, C for logging
    details: Optional[dict] = None
    is_terminal: bool = True    # stop the goal-directed loop after this result
    goal_reached: bool = False  # application successfully submitted
    next_hint: Optional[str] = None  # optional hint for next handler selection

class BaseHandler(ABC):
    """Base class for form handlers."""

    @abstractmethod
    def can_handle(self, form_type: FormType) -> bool:
        """Returns True if this handler can process the given form type."""
        pass

    @abstractmethod
    def process(self, page, cover_letter: str, **kwargs) -> ProcessResult:
        """Process the form. kwargs may include vacancy_text (used by QuestionsHandler)."""
        pass

    @abstractmethod
    def verify_submission(self, page) -> bool:
        """DOM щуп: confirm submission succeeded after process() returned success.
        Check DOM for success signals (modal gone, notification visible, button changed).
        Return False → caller sets status=applied_unverified and increments error counter.
        """
        pass

    def _poll_for_success(self, page, timeout_s: int = 5) -> bool:
        """Shared helper: poll DOM for HH modal submission success signals."""
        import time
        end = time.time() + timeout_s
        success_selectors = [
            '[data-qa*="vacancy-response-success"]',
            '[data-qa*="response-completed"]',
            '[data-qa*="response-notification"]',
        ]
        modal_selectors = [
            '[role="dialog"]',
            '[data-qa*="modal"]',
            '[data-qa*="response-popup"]',
            '.HH-Modal',
        ]
        while time.time() < end:
            # Success notification appeared
            for sel in success_selectors:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        return True
                except Exception:
                    pass
            # Modal disappeared (submit closed the dialog)
            modal_visible = False
            for sel in modal_selectors:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        modal_visible = True
                        break
                except Exception:
                    pass
            if not modal_visible:
                return True
            time.sleep(0.5)
        return False

    def _wait_and_random_delay(self, page, min_ms: int = 2000, max_ms: int = 5000) -> None:
        """Human-like random delay."""
        import random
        import time
        delay = random.randint(min_ms, max_ms)
        time.sleep(delay / 1000.0)

    def _find_element_by_selectors(self, page, selectors: list, visible_only: bool = True):
        """Finds the first element matching any selector in the list."""
        for selector in selectors:
            try:
                element = page.query_selector(selector)
                if element and (not visible_only or element.is_visible()):
                    return element
            except:
                continue
        return None