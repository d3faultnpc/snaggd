from .base import FormType, ProcessResult, BaseHandler
from .hh_modal import HHModalHandler
from .cover_only import CoverOnlyHandler
from .questions import QuestionsHandler
from .salary import SalaryHandler
from .chat import ChatHandler
from .test_form import TestFormHandler

class FormHandlers:
    def __init__(self):
        self.handlers = [
            SalaryHandler(),
            TestFormHandler(),
            HHModalHandler(),
            ChatHandler(),
            QuestionsHandler(),
            CoverOnlyHandler(),
        ]
    
    def get_handler(self, form_type: FormType) -> BaseHandler:
        """Returns the appropriate handler for the given form type."""
        for handler in self.handlers:
            if handler.can_handle(form_type):
                return handler

        # Fallback to CoverOnlyHandler for unknown types
        return CoverOnlyHandler()

__all__ = [
    'FormType', 'ProcessResult', 'BaseHandler', 'FormHandlers',
    'HHModalHandler', 'CoverOnlyHandler', 'QuestionsHandler',
    'SalaryHandler', 'ChatHandler', 'TestFormHandler'
]