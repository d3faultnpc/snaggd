from .base import BaseHandler, FormType, ProcessResult
from config import SELECTORS, FORM_KEYWORDS


class HHModalHandler(BaseHandler):
    """
    Handler for HH modals with navigation.

    Scenarios:
      A. Form with textarea → fill → "Submit" → success
      B. After "Submit": error "Application already viewed" → click "Chat"
      C. No textarea → just click the navigation button
    """

    def can_handle(self, form_type: FormType) -> bool:
        return form_type in [FormType.HH_MODAL_STEP1, FormType.HH_MODAL_STEP2]

    def process(self, page, cover_letter: str, hr_matcher=None, **kwargs) -> ProcessResult:
        # 1. Find and fill the cover letter textarea
        textarea = self._find_cover_textarea(page)
        filled = False

        if textarea:
            print("   🔹 Filling cover letter...")
            try:
                # type() fires React input/change events per-keystroke;
                # textarea stays disabled while empty — events are needed to enable the submit button
                textarea.type(cover_letter, delay=10)
                filled = True
                print("   ✅ Cover letter filled")
                self._wait_and_random_delay(page, 2000, 3000)
            except Exception as e:
                print(f"   ⚠️ Textarea fill error: {e}")
        else:
            print("   ⚠️ Cover letter field not found")

        # 2. Click the submit button (wait for it to become enabled after filling)
        nav_button = self._find_nav_button(page)
        if not nav_button:
            return ProcessResult(
                success=False,
                status="skipped_hh_modal",
                reason="Navigation buttons not found in HH modal",
                scenario="hh_modal_error"
            )

        button_text = nav_button.inner_text().strip()
        print(f"   🔹 Clicking: '{button_text}'")
        nav_button.scroll_into_view_if_needed()
        nav_button.click()
        self._wait_and_random_delay(page, 2000, 4000)

        # 3. Post-submit edge case check
        edge_result = self._check_post_submit_edge_case(page)
        if edge_result:
            return edge_result

        # 4. Success
        if filled:
            return ProcessResult(
                success=True,
                status="applied",
                reason=f"Cover letter submitted, button: '{button_text}'",
                scenario="hh_modal_with_cover",
                details={'button_text': button_text}
            )
        return ProcessResult(
            success=True,
            status="hh_modal_navigation",
            reason=f"HH modal navigation: '{button_text}'",
            scenario="hh_modal_no_cover",
            details={'button_text': button_text}
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_cover_textarea(self, page):
        """Finds cover letter textarea using verified selectors."""
        selectors = [
            # Popup modal (verified 2026-04-06)
            f'[data-qa="vacancy-response-popup-form-letter-input"] textarea',
            SELECTORS['popup_letter_input'],
            # Inline form (verified 2026-04-05)
            '[data-qa="vacancy-response-letter-informer"] textarea',
            '[data-qa="textarea-native-wrapper"] textarea',
            'textarea[data-qa*="response"]',
        ] + SELECTORS['cover_textarea']

        for selector in selectors:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    # Exclude salary fields
                    if not self._is_salary_field(el):
                        return el
            except Exception:
                continue
        return None

    def _is_salary_field(self, element) -> bool:
        """Returns True if the element is a salary field."""
        try:
            placeholder = (element.get_attribute('placeholder') or "").lower()
            salary_keywords = ['зарплат', 'salary', 'ожидани', 'доход', 'желаем']
            return any(kw in placeholder for kw in salary_keywords)
        except Exception:
            return False

    def _find_nav_button(self, page):
        """Finds the navigation button ('Submit', 'Apply', 'Next', etc.).

        Popup button starts disabled — wait up to 5 s for it to become enabled after form fill.
        """
        import time

        # Verified data-qa selectors (inline and popup)
        for selector in [SELECTORS['letter_submit'], SELECTORS['popup_submit']]:
            try:
                try:
                    page.wait_for_selector(
                        f"{selector}:not([disabled])",
                        timeout=5000
                    )
                except Exception:
                    pass  # Timeout — button may already be enabled
                btn = page.query_selector(selector)
                if btn and btn.is_visible() and not btn.is_disabled():
                    return btn
            except Exception:
                pass

        # Fallback by text (including "Откликнуться" in popup)
        nav_keywords = FORM_KEYWORDS['navigation'] + ['откликнуться']
        buttons = page.query_selector_all('button, a[role="button"]')
        for btn in buttons:
            try:
                if not btn.is_visible():
                    continue
                text = btn.inner_text().strip().lower()
                if any(kw in text for kw in nav_keywords):
                    return btn
            except Exception:
                continue
        return None

    def verify_submission(self, page) -> bool:
        return self._poll_for_success(page, timeout_s=5)

    def _check_post_submit_edge_case(self, page) -> ProcessResult | None:
        """
        Checks post-submit edge case: 'Application already viewed by employer' → click 'Chat'.

        Selectors verified 2026-04-05 via debug snapshots.
        """
        try:
            error_el = page.query_selector(SELECTORS['form_error'])
            if not error_el or not error_el.is_visible():
                return None

            error_text = error_el.inner_text().strip()
            print(f"   ⚠️ Form error detected: '{error_text}'")

            # "Please fill in cover letter" — textarea was not filled
            if 'введите' in error_text.lower() or 'заполните' in error_text.lower():
                return ProcessResult(
                    success=False,
                    status="skipped_no_cover_filled",
                    reason=f"Cover letter not filled: {error_text}",
                    scenario="hh_modal_cover_required"
                )

            if 'просмотрен' not in error_text.lower() and 'уже' not in error_text.lower():
                return None

            print("   🔍 Edge case: application already viewed — looking for 'Chat' button...")

            chat_link = page.query_selector(SELECTORS['chat_link'])
            if chat_link and chat_link.is_visible():
                print("   🔹 Clicking 'Chat'...")
                chat_link.click()
                self._wait_and_random_delay(page, 2000, 3000)
                return ProcessResult(
                    success=True,
                    status="chat_redirect",
                    reason=f"Edge case: {error_text} → redirected to chat",
                    scenario="edge_case_chat",
                    details={'error_text': error_text}
                )

            return ProcessResult(
                success=False,
                status="skipped_edge_case_no_chat",
                reason=f"Edge case: {error_text}, chat button not found",
                scenario="edge_case_no_chat",
                details={'error_text': error_text}
            )

        except Exception as e:
            print(f"   ⚠️ Edge case check error: {e}")
            return None
