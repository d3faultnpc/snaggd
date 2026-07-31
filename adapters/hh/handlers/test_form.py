from .base import BaseHandler, FormType, ProcessResult
from config import SELECTORS, CONFIG


class TestFormHandler(BaseHandler):
    """
    Handler for employer test/question forms.

    Strategy: click 'Apply without answering questions'
    (vacancy-response-link-no-questions) — one discrete step, then let the
    goal-directed loop re-detect whatever page results (a screening
    questionnaire, a cover-only form, chatik, etc.) and dispatch to the
    handler that actually fits.

    Session 56: this used to chain its own field-filling/cover/submit logic
    internally, via a narrower duplicate of questions.py's fill_form()
    collector (text fields only, no radio/checkbox). It was built 2026-05-29
    already using the modern LLM batch-fill mechanism (not pre-LLM legacy),
    but never migrated to the goal-directed loop idiom introduced two days
    later — so it silently lagged questions.py's later capability growth
    instead of deferring to it. Removed; this handler now does exactly one
    thing, matching every other handler in the loop.
    """

    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.TEST_FORM

    def verify_submission(self, page) -> bool:
        return self._poll_for_success(page, timeout_s=5)

    def process(self, page, **kwargs) -> ProcessResult:
        reporter = kwargs.get("reporter")
        _vac_seq = kwargs.get("vacancy_seq")
        vid = str(_vac_seq) if _vac_seq is not None else None
        try:
            no_q_link = page.query_selector(SELECTORS['test_no_questions'])
            if not no_q_link or not no_q_link.is_visible():
                if CONFIG.fill_tests:
                    # FILL_TESTS=true: delegate to LLM fill — not yet implemented
                    return ProcessResult(
                        success=False,
                        status="skipped_test_form",
                        reason="Test mandatory — LLM fill not yet implemented (FILL_TESTS=true noted)",
                        scenario="test_form_fill_pending",
                        is_terminal=True, goal_reached=False
                    )
                return ProcessResult(
                    success=False,
                    status="skipped_test_form",
                    reason="No skip-questions link found — test is mandatory",
                    scenario="test_form_required",
                    is_terminal=True, goal_reached=False
                )

            print("   🔹 Clicking 'Apply without answering questions'...")
            if reporter is not None:
                reporter.emit("Skipping the test question", actor="scan", vacancy_id=vid)
            no_q_link.click()
            self._wait_and_random_delay(page, 2000, 3000)
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_test_form",
                reason=f"Error clicking no-questions link: {e}",
                scenario="test_form_error",
                is_terminal=True, goal_reached=False
            )

        return ProcessResult(
            success=True,
            status="test_skipped",
            reason="Test skipped — handing off to the next detected form layer",
            scenario="test_form_skipped",
            is_terminal=False, goal_reached=False
        )
