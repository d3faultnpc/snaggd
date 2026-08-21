from .base import BaseHandler, FormType, ProcessResult
from config import SELECTORS

class CoverOnlyHandler(BaseHandler):
    """Handler for forms with a single cover letter field."""

    # _narrate() lives on BaseHandler now (session 58 code-review — was
    # duplicated near-identically across 4 handlers, hoisted to avoid drift).

    def can_handle(self, form_type: FormType) -> bool:
        return form_type in [FormType.COVER_ONLY, FormType.UNKNOWN]

    def process(self, page, **kwargs) -> ProcessResult:
        """Fills the cover letter field and submits."""
        reporter = kwargs.get("reporter")
        _vac_seq = kwargs.get("vacancy_seq")
        vid = str(_vac_seq) if _vac_seq is not None else None

        textarea = self._find_cover_field(page)

        if not textarea:
            return ProcessResult(
                success=False,
                status="skipped_no_textarea",
                reason="Cover letter field not found",
                scenario="cover_error",
                is_terminal=True,
                goal_reached=False
            )

        # Guard against misdetected salary fields
        if self._is_salary_field(textarea):
            return ProcessResult(
                success=False,
                status="skipped_salary_form",
                reason="Salary expectations field detected instead of cover letter",
                scenario="salary_detection",
                is_terminal=True,
                goal_reached=False
            )

        try:
            # Generated on demand, only now that we know a real cover field
            # exists and isn't actually a salary field (session 56) — cached
            # by vacancy_id, so an earlier layer's cover (if any) gets reused
            # instead of a fresh one.
            llm_cover = kwargs.get("llm_cover")
            cover_letter = llm_cover.cover(kwargs.get("vacancy_text", ""), kwargs.get("vacancy_id"))
            self._narrate(reporter, "   🔹 Filling cover letter...",
                          gui_message="Writing your cover letter into the form…", vacancy_id=vid)
            textarea.type(cover_letter, delay=10)
            # Plain print — folded into the line above for the GUI.
            print("   ✅ Cover letter filled")

            self._wait_and_random_delay(page, 2000, 3000)

            send_button = self._find_element_by_selectors(page, SELECTORS['send_button'])

            if not send_button:
                return ProcessResult(
                    success=False,
                    status="skipped_no_send_button",
                    reason="Submit button not found",
                    scenario="cover_error",
                    is_terminal=True,
                    goal_reached=False
                )

            # Plain print — mechanical click, the outcome line below is what matters.
            print("   🔹 Submitting application...")
            send_button.click()

            self._wait_and_random_delay(page, 3000, 5000)
            self._narrate(reporter, "   ✅ Application submitted!",
                          gui_message="[OK] application submitted", vacancy_id=vid)

            return ProcessResult(
                success=True,
                status="applied",
                reason="Cover letter submitted",
                scenario="cover_only",
                # The letter itself, not only its length. It is what was
                # actually sent on the person's behalf, and until now it lived
                # only in cover_cache.json — keyed by vacancy and profile hash,
                # invisible from the record and dropped whenever the profile
                # changed. Vacancy TEXT is deliberately NOT stored (there is a
                # link, and hh keeps its own archive); the letter has no such
                # second home.
                details={'cover_length': len(cover_letter),
                         'cover_text': cover_letter},
                is_terminal=True,
                goal_reached=True
            )

        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_error",
                reason=f"Form fill error: {str(e)}",
                scenario="cover_error",
                is_terminal=True,
                goal_reached=False
            )
    
    def verify_submission(self, page) -> bool:
        return self._poll_for_success(page, timeout_s=5)

    def _is_salary_field(self, element) -> bool:
        """Returns True if the element is a salary expectations field."""
        try:
            placeholder = element.get_attribute('placeholder') or ""
            label = element.query_selector('xpath=..//label') or element.query_selector('xpath=..//..//label')
            label_text = label.inner_text().strip().lower() if label else ""
            salary_keywords = ['зарплат', 'salary', 'ожидани', 'expected salary', 'доход', 'желаем']
            return any(kw in placeholder.lower() or kw in label_text for kw in salary_keywords)
        except Exception as e:
            # Fails OPEN: "we couldn't tell" is answered with "not a salary
            # field", and the caller then types a cover letter into it and
            # submits to a real employer. Kept as-is for now — flipping the
            # default would skip legitimate applies on any transient DOM
            # error — but it is no longer silent about it. The real fix is to
            # route this judgement through the same LLM batch call that
            # hh_modal.py's generic-field path already trusts for it.
            print(f"   ⚠️ Salary-field check failed ({e}) — proceeding as if it is NOT a salary field")
            return False