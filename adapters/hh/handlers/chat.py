import time

from .base import BaseHandler, FormType, ProcessResult
from config import SELECTORS

try:
    from core.llm_agent import LLMAgent
    _agent = LLMAgent()
except Exception:
    _agent = None


class ChatHandler(BaseHandler):
    """
    Handler for auto-read employers (Sber, PERX, etc.) — applies via chatik.

    Entry condition: FormDetector returns CHAT_INTERFACE
      (form-helper-error + vacancy-response-link-view-topic visible after Apply click).

    Flow:
      1. Click "Go to chat" (main page) → chatik iframe opens at chatik.hh.ru
      2. _wait_for_chatik_frame() → get Frame object for the cross-origin iframe
      3. _handle_hr_bot_loop(scope) → detect and answer HR-bot questions via LLM
      4. _find_add_cover_btn(scope) → find "Добавить сопроводительное" in iframe
      5. click() → cover letter textarea appears
      6. _find_cover_input(scope) + type(cover_letter) → fill textarea
      7. _send_cover(scope) → send

    Key architecture note:
      Chatik renders ALL its content in a cross-origin iframe (<iframe src="chatik.hh.ru/...">).
      page.query_selector / page.wait_for_selector cannot enter cross-origin iframes.
      All chatik interactions must go through the Frame object returned by _wait_for_chatik_frame.
      ElementHandle.click() / .type() work fine regardless of which frame they belong to.

    Verified selectors:
      vacancy-response-link-view-topic — "Go to chat" link on main page (2026-04-06)
      iframe.chatik-integration-iframe — chatik iframe container (2026-05-27)
      textarea-native-wrapper textarea — "Сообщение" input inside iframe (2026-05-26)

    Unverified (update after live debug snapshot):
      chatik_cover_input cascade — textarea after "Добавить сопроводительное"
      chatik_cover_send — send button for cover letter form
      chatik_bot_message — employer/bot message bubble
    """

    def __init__(self):
        self._cover_typed = False

    def can_handle(self, form_type: FormType) -> bool:
        return form_type == FormType.CHAT_INTERFACE

    def verify_submission(self, page) -> bool:
        if not self._cover_typed:
            # No cover typed (applied_via_chat_no_cover path).
            # Application was already submitted when the chat link was clicked.
            return True

        # Cover was typed — verify by checking whether React cleared the input after send.
        chatik_frame = None
        for frame in page.frames:
            if 'chatik.hh.ru' in frame.url:
                chatik_frame = frame
                break

        if chatik_frame is None:
            return True  # frame gone = navigated away after send = success

        try:
            inp = chatik_frame.query_selector(SELECTORS['chatik_input'])
            if inp is None:
                return True  # input element removed = React rebuilt UI after send = success
            if inp.input_value().strip() == "":
                return True
            # Input still has text — give React one more beat to clear after send
            page.wait_for_timeout(2000)
            return inp.input_value().strip() == ""
        except Exception:
            return True  # DOM access failed = frame changed state = assume success

    def process(self, page, cover_letter: str, **kwargs) -> ProcessResult:
        self._cover_typed = False
        # 1. Click "Go to chat" — chat_link is on main page, not inside iframe
        chat_link = page.query_selector(SELECTORS['chat_link'])
        if not chat_link or not chat_link.is_visible():
            return ProcessResult(
                success=False,
                status="skipped_no_chat_button",
                reason="Chat button not found (vacancy-response-link-view-topic)",
                scenario="chat_error",
                is_terminal=True,
                goal_reached=False
            )

        print("   🔹 Clicking 'Go to chat'...")
        chat_link.click()
        self._wait_and_random_delay(page, 3000, 5000)

        # 2. Wait for chatik iframe — all content lives in a cross-origin frame
        chatik_scope = self._wait_for_chatik_frame(page)
        if chatik_scope is None:
            print("   ⚠️ Chatik iframe not accessible — falling back to main page scope")
            chatik_scope = page  # fallback for possible future HH redesign

        # 3. HR-bot Q&A loop (PERX and similar auto-interview bots)
        self._handle_hr_bot_loop(chatik_scope, page)

        # 4. Click "Добавить сопроводительное" to open the cover letter field
        add_cover = self._find_add_cover_btn(chatik_scope)
        if not add_cover:
            print("   ℹ️ 'Добавить сопроводительное' not found — application submitted without cover letter")
            return ProcessResult(
                success=True,
                status="applied_via_chat_no_cover",
                reason="Chat application submitted; cover letter button not available",
                scenario="chat_no_cover",
                is_terminal=True,
                goal_reached=True
            )

        print("   🔹 Clicking 'Добавить сопроводительное'...")
        add_cover.click()
        self._wait_and_random_delay(page, 2000, 3000)

        # 5. Find cover letter textarea (separate from "Сообщение" field)
        cover_input = self._find_cover_input(chatik_scope)
        if not cover_input:
            print("   ⚠️ Cover letter textarea not found after clicking 'Добавить' — skipping cover")
            return ProcessResult(
                success=True,
                status="applied_via_chat_no_cover",
                reason="Cover letter textarea not found after 'Добавить сопроводительное'",
                scenario="chat_no_cover",
                is_terminal=True,
                goal_reached=True
            )

        # 6. Focus + type cover letter
        # Chatik uses a single "Сообщение" textarea for cover letters too.
        # Typing \n triggers React's Enter-as-send handler → paragraph 1 is dispatched
        # as a standalone message, element re-renders, paragraph 2 is lost.
        # Fix: flatten all newlines to a space before typing.
        chatik_safe_cover = cover_letter.replace('\n', ' ').strip()
        print("   🔹 Typing cover letter into cover field...")
        try:
            cover_input.click()
            self._wait_and_random_delay(page, 500, 1000)
            cover_input.type(chatik_safe_cover, delay=10)
            self._cover_typed = True
            print("   ✅ Cover letter typed")
            self._wait_and_random_delay(page, 1500, 2500)
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_chat_fill_error",
                reason=f"Cover letter fill error: {e}",
                scenario="chat_fill_error",
                is_terminal=True,
                goal_reached=False
            )

        # 7. Send cover letter
        try:
            sent = self._send_cover(chatik_scope, cover_input, page)
            if sent:
                print("   ✅ Cover letter sent via chatik!")
                return ProcessResult(
                    success=True,
                    status="applied_via_chat",
                    reason="Auto-read employer: cover letter sent via chatik",
                    scenario="chat_cover_sent",
                    details={'cover_length': len(cover_letter)},
                    is_terminal=True,
                    goal_reached=True
                )
            else:
                return ProcessResult(
                    success=False,
                    status="skipped_chat_send_error",
                    reason="Send button not found in chatik cover form",
                    scenario="chat_send_error",
                    is_terminal=True,
                    goal_reached=False
                )
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_chat_send_error",
                reason=f"Chatik send error: {e}",
                scenario="chat_send_error",
                is_terminal=True,
                goal_reached=False
            )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _wait_for_chatik_frame(self, page):
        """Waits for the chatik iframe to load and returns its Playwright Frame object.

        Chatik renders all its content inside a cross-origin iframe served from chatik.hh.ru.
        The iframe element appears in the main page DOM after clicking 'Go to chat', but
        the frame object becomes accessible in page.frames once the iframe navigates.

        Returns the Frame if found within 12s, or None on timeout.
        """
        # Step 1: wait for iframe element to appear in main page DOM
        try:
            page.wait_for_selector(
                'iframe.chatik-integration-iframe',
                timeout=12000,
                state='attached'
            )
        except Exception:
            print("   ⚠️ Chatik iframe element not found in DOM within 12s")
            return None

        # Step 2: wait for the frame to become accessible (frame URL set after navigation)
        deadline = time.time() + 5
        while time.time() < deadline:
            for frame in page.frames:
                if 'chatik.hh.ru' in frame.url:
                    print(f"   ✅ Chatik iframe frame acquired: {frame.url[:60]}")
                    return frame
            page.wait_for_timeout(300)

        print("   ⚠️ Chatik iframe element loaded but frame not accessible within 5s")
        return None

    def _find_add_cover_btn(self, scope):
        """Finds 'Добавить сопроводительное' inside chatik iframe.

        scope is a Playwright Frame object (chatik.hh.ru iframe).
        Frame.wait_for_selector and Frame.query_selector have same API as Page equivalents.
        """
        # Primary: text-based wait — works regardless of element tag or class
        try:
            el = scope.wait_for_selector(
                ':text("Добавить сопроводительное")',
                timeout=12000,
                state='visible'
            )
            if el:
                print("   ✅ 'Добавить сопроводительное' found in chatik iframe")
                return el
        except Exception:
            print("   ⚠️ :text() timed out in chatik iframe — trying data-qa cascade")

        # Fallback: data-qa / tag cascade
        for selector in SELECTORS['chatik_add_cover']:
            el = scope.query_selector(selector)
            if el and el.is_visible():
                print(f"   ✅ Found via cascade in iframe: {selector}")
                return el

        print("   ⚠️ 'Добавить сопроводительное' not found in chatik iframe")
        return None

    def _find_cover_input(self, scope):
        """Finds cover letter input inside chatik iframe.

        DOM probe (2026-05-27) confirmed: after clicking 'Добавить сопроводительное',
        NO separate textarea is created. A cover letter panel appears visually, but the
        actual text input remains the existing 'Сообщение' field
        (data-qa="chatik-new-message-text"). Text typed while the panel is active
        is sent as the cover letter.

        Falls back to chatik_cover_input cascade in case HH ever adds a separate field.
        """
        # Primary: the verified "Сообщение" textarea — the cover letter input post-panel-open
        el = scope.query_selector(SELECTORS['chatik_input'])
        if el and el.is_visible():
            return el

        # Fallback cascade (unverified — for possible future HH versions)
        for selector in SELECTORS['chatik_cover_input']:
            el = scope.query_selector(selector)
            if el and el.is_visible():
                return el

        return None

    def _send_cover(self, scope, cover_input, page) -> bool:
        """Sends the cover letter form inside chatik iframe.

        scope is already the chatik frame — no need to scope to chatik-root.
        Prefers dedicated cover send button, falls back to any 'Отправить', then Enter.
        """
        for selector in SELECTORS['chatik_cover_send']:
            btn = scope.query_selector(selector)
            if btn and btn.is_visible():
                print("   🔹 Clicking send button...")
                btn.click()
                self._wait_and_random_delay(page, 2000, 3000)
                return True

        # Fallback: any visible "Отправить" in chatik frame
        send_btn = scope.query_selector('button:has-text("Отправить")')
        if send_btn and send_btn.is_visible():
            print("   🔹 Clicking 'Отправить' in chatik...")
            send_btn.click()
            self._wait_and_random_delay(page, 2000, 3000)
            return True

        # Last resort: Enter key on cover input
        print("   🔹 Sending via Enter...")
        cover_input.press("Enter")
        self._wait_and_random_delay(page, 2000, 3000)
        return True

    def _handle_hr_bot_loop(self, scope, page) -> None:
        """Detects HR-bot questions in chatik iframe and answers them via LLM.

        scope is the chatik Frame — all queries run inside the iframe.
        Called before the cover letter step. Returns immediately if no bot messages found.

        Answers via _agent.answer_question() — uses candidate profile directly.
        Text input instead of quick-reply buttons: more accurate, not limited to preset options.

        NOTE: chatik_bot_message selectors are unverified — update after live debug snapshot.
        """
        bot_message_selectors = SELECTORS['chatik_bot_message']
        max_rounds = 5
        rounds = 0
        last_answered_text = None

        while rounds < max_rounds:
            # Check for bot message inside iframe
            bot_el = None
            for selector in bot_message_selectors:
                els = scope.query_selector_all(selector)
                visible = [e for e in els if e.is_visible()]
                if visible:
                    bot_el = visible[-1]  # latest message
                    break

            if not bot_el:
                break  # No bot messages — nothing to answer

            question_text = bot_el.inner_text().strip()
            if not question_text:
                break

            # Skip if same question already answered (bot hasn't replied yet)
            if question_text == last_answered_text:
                break

            print(f"   🤖 HR-bot question: {question_text[:80]}...")

            # Generate answer via LLM directly from candidate profile
            if _agent is None:
                print("   ⚠️ HR-bot: LLM unavailable — skipping bot loop")
                break
            try:
                answer = _agent.answer_question(question_text)
            except Exception as e:
                print(f"   ⚠️ HR-bot LLM error: {e} — skipping bot loop")
                break

            if not answer:
                print("   ⚠️ HR-bot: empty LLM answer — skipping bot loop")
                break

            # Find "Сообщение" input inside iframe and type answer
            msg_input = scope.query_selector(SELECTORS['chatik_input'])
            if not msg_input or not msg_input.is_visible():
                print("   ⚠️ HR-bot: 'Сообщение' input not found in chatik iframe — skipping")
                break

            safe_answer = answer.replace('\n', ' ').strip()
            print(f"   🔹 Answering HR-bot: {safe_answer[:60]}...")
            msg_input.click()
            self._wait_and_random_delay(page, 300, 600)
            msg_input.type(safe_answer, delay=10)
            self._wait_and_random_delay(page, 500, 1000)

            # Send: already in iframe scope, any "Отправить" is safe to click
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
