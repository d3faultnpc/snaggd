from .base import BaseHandler, FormType, ProcessResult
from config import SELECTORS


class ChatHandler(BaseHandler):
    """
    Handler for auto-read employers (Sber, PERX, etc.) — applies via chatik.

    Entry condition: FormDetector returns CHAT_INTERFACE
      (form-helper-error + vacancy-response-link-view-topic visible after Apply click).

    Flow:
      1. Click "Go to chat" → chatik opens, application auto-submitted by HH
      2. _handle_hr_bot_loop() → detect and answer HR-bot questions via LLM text input
      3. Click "Добавить сопроводительное" → cover letter textarea appears
      4. click() + type(cover_letter) → send
      If cover button not found → return applied_via_chat_no_cover (application already submitted).

    Verified selectors:
      vacancy-response-link-view-topic — "Go to chat" link (2026-04-06)
      textarea-native-wrapper textarea — "Сообщение" input (2026-05-26)

    Unverified (update after live debug snapshot):
      chatik_cover_input cascade — textarea after "Добавить сопроводительное"
      chatik_cover_send — send button for cover letter form
      chatik_bot_message — employer/bot message bubble
    """

    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.CHAT_INTERFACE

    def verify_submission(self, page) -> bool:
        # TODO (#5): check for message-sent indicator in chatik after live test
        # Possible signals: last message bubble matches our cover letter text,
        # or success notification appears.
        return False

    def process(self, page, cover_letter: str, hr_matcher=None, **kwargs) -> ProcessResult:
        # 1. Click "Go to chat" — chatik opens, HH auto-submits the application
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

        # 2. HR-bot Q&A loop (PERX and similar auto-interview bots)
        if hr_matcher:
            self._handle_hr_bot_loop(page, hr_matcher)

        # 3. Click "Добавить сопроводительное" to open the cover letter field
        add_cover = self._find_add_cover_btn(page)
        if not add_cover:
            print("   ℹ️ 'Добавить сопроводительное' not found — application submitted without cover letter")
            return ProcessResult(
                success=True,
                status="applied_via_chat_no_cover",
                reason="Chat application submitted; cover letter button not available",
                scenario="chat_no_cover"
            )

        print("   🔹 Clicking 'Добавить сопроводительное'...")
        add_cover.click()
        self._wait_and_random_delay(page, 2000, 3000)

        # 4. Find cover letter textarea (separate from "Сообщение" field)
        cover_input = self._find_cover_input(page)
        if not cover_input:
            print("   ⚠️ Cover letter textarea not found after clicking 'Добавить' — skipping cover")
            return ProcessResult(
                success=True,
                status="applied_via_chat_no_cover",
                reason="Cover letter textarea not found after 'Добавить сопроводительное'",
                scenario="chat_no_cover"
            )

        # 5. Focus + type cover letter
        print("   🔹 Typing cover letter into cover field...")
        try:
            cover_input.click()
            self._wait_and_random_delay(page, 500, 1000)
            cover_input.type(cover_letter, delay=10)
            print("   ✅ Cover letter typed")
            self._wait_and_random_delay(page, 1500, 2500)
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_chat_fill_error",
                reason=f"Cover letter fill error: {e}",
                scenario="chat_fill_error"
            )

        # 6. Send cover letter
        try:
            sent = self._send_cover(page, cover_input)
            if sent:
                print("   ✅ Cover letter sent via chatik!")
                return ProcessResult(
                    success=True,
                    status="applied_via_chat",
                    reason="Auto-read employer: cover letter sent via chatik",
                    scenario="chat_cover_sent",
                    details={'cover_length': len(cover_letter)}
                )
            else:
                return ProcessResult(
                    success=False,
                    status="skipped_chat_send_error",
                    reason="Send button not found in chatik cover form",
                    scenario="chat_send_error"
                )
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_chat_send_error",
                reason=f"Chatik send error: {e}",
                scenario="chat_send_error"
            )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _find_add_cover_btn(self, page):
        """Finds the 'Добавить сопроводительное' button inside chatik.

        Element type varies across HH versions (a/button/div). Iterates through
        a cascade of selectors — data-qa first, then by tag+text, then any tag.
        Searches full page (chatik may render outside chatik-root via React portal).
        """
        for selector in SELECTORS['chatik_add_cover']:
            el = page.query_selector(selector)
            if el and el.is_visible():
                return el
        return None

    def _find_cover_input(self, page):
        """Finds the cover letter textarea that appears after 'Добавить сопроводительное'.

        Different from the regular 'Сообщение' input — this is the formal cover letter field.
        Tries specific data-qa selectors first, falls back to placeholder text.
        Cascade must be updated after live debug snapshot confirms the actual data-qa value.
        """
        for selector in SELECTORS['chatik_cover_input']:
            el = page.query_selector(selector)
            if el and el.is_visible():
                return el
        return None

    def _send_cover(self, page, cover_input) -> bool:
        """Sends the cover letter form. Prefers dedicated cover send button,
        falls back to chatik-root scoped 'Отправить', then Enter.
        """
        for selector in SELECTORS['chatik_cover_send']:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                print(f"   🔹 Clicking send button...")
                btn.click()
                self._wait_and_random_delay(page, 2000, 3000)
                return True

        # Fallback: scoped "Отправить" inside chatik-root to avoid vacancy form collision
        chatik_root = page.query_selector('[data-qa="chatik-root"]')
        scope = chatik_root if chatik_root else page
        send_btn = scope.query_selector('button:has-text("Отправить")')
        if send_btn and send_btn.is_visible():
            print("   🔹 Clicking 'Отправить' in chatik...")
            send_btn.click()
            self._wait_and_random_delay(page, 2000, 3000)
            return True

        # Last resort: Enter key
        print("   🔹 Sending via Enter...")
        cover_input.press("Enter")
        self._wait_and_random_delay(page, 2000, 3000)
        return True

    def _handle_hr_bot_loop(self, page, hr_matcher) -> None:
        """Detects HR-bot questions in chatik and answers them via LLM text input.

        Called before the cover letter step. If no bot messages are found, returns immediately.
        Reads the latest employer message, generates a text answer via hr_matcher,
        types it into the "Сообщение" field (NOT the cover letter field), and sends.
        Repeats until no new bot message appears within the wait window.

        Text input instead of quick-reply buttons: more accurate, not limited to preset options.

        NOTE: Selectors in chatik_bot_message are unverified — update after live debug snapshot.
        """
        bot_message_selectors = SELECTORS['chatik_bot_message']
        max_rounds = 5
        rounds = 0
        last_answered_text = None

        while rounds < max_rounds:
            # Check for bot message
            bot_el = None
            for selector in bot_message_selectors:
                els = page.query_selector_all(selector)
                visible = [e for e in els if e.is_visible()]
                if visible:
                    bot_el = visible[-1]  # latest message
                    break

            if not bot_el:
                break  # No bot messages — nothing to answer

            question_text = bot_el.inner_text().strip()
            if not question_text:
                break

            # Skip if this is the same question we already answered (bot hasn't replied yet)
            if question_text == last_answered_text:
                break

            print(f"   🤖 HR-bot question: {question_text[:80]}...")

            # Generate answer via LLM
            try:
                answer = hr_matcher.find_answer(question_text)
            except Exception as e:
                print(f"   ⚠️ HR-bot LLM error: {e} — skipping bot loop")
                break

            if not answer:
                print("   ⚠️ HR-bot: empty LLM answer — skipping bot loop")
                break

            # Find "Сообщение" input and type the answer
            msg_input = page.query_selector(SELECTORS['chatik_input'])
            if not msg_input or not msg_input.is_visible():
                print("   ⚠️ HR-bot: 'Сообщение' input not found — skipping bot loop")
                break

            print(f"   🔹 Answering HR-bot: {answer[:60]}...")
            msg_input.click()
            self._wait_and_random_delay(page, 300, 600)
            msg_input.type(answer, delay=10)
            self._wait_and_random_delay(page, 500, 1000)

            # Send answer
            chatik_root = page.query_selector('[data-qa="chatik-root"]')
            scope = chatik_root if chatik_root else page
            send_btn = scope.query_selector('button:has-text("Отправить")')
            if send_btn and send_btn.is_visible():
                send_btn.click()
            else:
                msg_input.press("Enter")

            last_answered_text = question_text
            # Wait for bot to reply before checking for next question
            self._wait_and_random_delay(page, 3000, 5000)
            rounds += 1

        if rounds > 0:
            print(f"   ✅ HR-bot loop: answered {rounds} question(s)")
