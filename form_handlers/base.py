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
    has_popup_questions: bool = False
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

class BaseHandler(ABC):
    """Base class for form handlers."""

    @abstractmethod
    def can_handle(self, form_type: FormType) -> bool:
        """Returns True if this handler can process the given form type."""
        pass

    @abstractmethod
    def process(self, page, cover_letter: str, hr_matcher=None) -> ProcessResult:
        """Process the form."""
        pass

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