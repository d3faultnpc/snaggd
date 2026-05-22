from .base import BaseHandler, FormType, ProcessResult
from config import SELECTORS


class ChatHandler(BaseHandler):
    """
    Handler for auto-read employers (Sber etc.) — applies via chatik.
    verify_submission: chat selectors unverified live — returns False until tested.

    Scenario:
      Employer has auto-view enabled. After clicking 'Apply' the form
      immediately shows an error + chat button.
      Flow: click chat → add cover letter in chatik → send.

    Verified selectors (2026-04-06):
      vacancy-response-link-view-topic — "Go to chat" link

    Not verified (from spec, require a live test session):
      chatik-chat-message-applicant-action — "+Add cover letter" link
      chatik-new-message-text — message input field
    """

    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.CHAT_INTERFACE

    def verify_submission(self, page) -> bool:
        # Chat selectors not yet verified live — conservatively unverified until task #5
        # TODO: check for message-sent indicator in chatik after live test
        return False

    def process(self, page, cover_letter: str, hr_matcher=None, **kwargs) -> ProcessResult:
        # 1. Click "Go to chat"
        chat_link = page.query_selector(SELECTORS['chat_link'])
        if not chat_link or not chat_link.is_visible():
            return ProcessResult(
                success=False,
                status="skipped_no_chat_button",
                reason="Chat button not found (vacancy-response-link-view-topic)",
                scenario="chat_error"
            )

        print("   🔹 Clicking 'Go to chat'...")
        chat_link.click()
        self._wait_and_random_delay(page, 3000, 5000)

        # 2. Look for "+Add cover letter" link in chatik
        add_cover = page.query_selector(SELECTORS['chatik_add_cover'])
        if add_cover and add_cover.is_visible():
            print("   🔹 Clicking '+Add cover letter'...")
            add_cover.click()
            self._wait_and_random_delay(page, 2000, 3000)

        # 3. Find input field — scoped inside chatik-root to avoid hitting vacancy form
        chatik_root = page.query_selector('[data-qa="chatik-root"]')
        scope = chatik_root if chatik_root else page
        chat_input = None
        for selector in [
            SELECTORS['chatik_input'],
            'div[contenteditable="true"]',
            'textarea',
        ]:
            el = scope.query_selector(selector)
            if el and el.is_visible():
                chat_input = el
                break

        if not chat_input:
            return ProcessResult(
                success=False,
                status="skipped_no_chat_input",
                reason="Chat input field not found",
                scenario="chat_no_input"
            )

        print("   🔹 Typing cover letter into chatik...")
        try:
            tag = chat_input.evaluate('el => el.tagName.toLowerCase()')
            if tag == 'div':
                chat_input.type(cover_letter, delay=10)
            else:
                chat_input.type(cover_letter, delay=10)
            print("   ✅ Message typed")
            self._wait_and_random_delay(page, 2000, 3000)
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_chat_fill_error",
                reason=f"Chatik fill error: {e}",
                scenario="chat_fill_error"
            )

        # 4. Send — scoped to chatik-root to avoid clicking vacancy form's "Отправить"
        try:
            send_btn = scope.query_selector('button:has-text("Отправить")')
            if send_btn and send_btn.is_visible():
                print("   🔹 Clicking 'Send' in chatik...")
                send_btn.click()
            else:
                print("   🔹 Sending via Enter...")
                chat_input.press("Enter")

            self._wait_and_random_delay(page, 3000, 4000)
            print("   ✅ Cover letter sent via chatik!")

            return ProcessResult(
                success=True,
                status="applied_via_chat",
                reason="Auto-read employer: cover letter sent via chatik",
                scenario="chat_cover_sent",
                details={'cover_length': len(cover_letter)}
            )
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_chat_send_error",
                reason=f"Chatik send error: {e}",
                scenario="chat_send_error"
            )
