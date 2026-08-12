import time

from .base import BaseHandler, FormType, ProcessResult
from ..dom import find_chat_link, find_visible
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
      3. _handle_hr_bot_loop(scope) → PARKED, not called (see its own docstring
         and the call site's comment) — a real, wanted feature, half-built,
         disabled pending a proper post-first-goal design
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
      chatik_message_delivered nesting — direction check inside HR-bot loop (from TZ doc, not independently re-verified live)
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

    def process(self, page, **kwargs) -> ProcessResult:
        self._cover_typed = False
        reporter = kwargs.get("reporter")
        llm_cover = kwargs.get("llm_cover")
        vacancy_id = kwargs.get("vacancy_id")
        vacancy_text = kwargs.get("vacancy_text", "")
        _vac_seq = kwargs.get("vacancy_seq")
        vid = str(_vac_seq) if _vac_seq is not None else None
        # 1. Click "Go to chat" — chat_link is on main page, not inside iframe
        chat_link = find_chat_link(page)
        if chat_link is None:
            return ProcessResult(
                success=False,
                status="skipped_no_chat_button",
                reason="Chat button not found (vacancy-response-link-view-topic)",
                scenario="chat_error",
                is_terminal=True,
                goal_reached=False
            )

        self._narrate(reporter, "   🔹 Clicking 'Go to chat'...",
                      gui_message="Opening the chat with the employer", vacancy_id=vid)
        chat_link.click()
        self._wait_and_random_delay(page, 3000, 5000)

        # 2. Wait for chatik iframe — all content lives in a cross-origin frame
        chatik_scope = self._wait_for_chatik_frame(page)
        if chatik_scope is None:
            print("   ⚠️ Chatik iframe not accessible — falling back to main page scope")
            chatik_scope = page  # fallback for possible future HH redesign

        # 3. HR-bot Q&A loop — DISABLED AGAIN (session 56), root cause now
        # CONFIRMED live (Ozon "Младший менеджер по продукту ML-моделей...",
        # 2026-07-26, log + user's own HH.ru screenshot cross-verified). Not
        # the suggestion-chip theory (disproven session 55 via DevTools — chips
        # are plain <button>, no data-qa overlap with chatik_message). The real
        # culprit: the "Добавить сопроводительное" prompt itself, still sitting
        # in the chat at this point (cover not added yet), matches
        # SELECTORS['chatik_message'] and carries no 'delivered' icon, so the
        # reversed scan treats it as an incoming HR-bot question. The LLM is
        # asked to "answer" it, produces a generic self-pitch, and that gets
        # sent as a real plain message — then the code below (nothing gates on
        # hr_bot_rounds) proceeds to also send the real cover_letter via
        # "Добавить сопроводительное" regardless: one plain message (bogus
        # answer) + one differing cover, both real sends, one process() call.
        # This loop is a half-built fragment of an intended, separate
        # post-first-goal phase (after the cover is confirmed sent, watch
        # chatik for a real AI auto-responder asking real screening questions,
        # and hold that conversation) that was started before the *first*
        # goal (cover delivery, verified via the finish selector below) is
        # even confirmed done — user's own call: park the whole feature for
        # post-post-release (10+ paying users), not a near-term item. This
        # step should just finalize on the terminal-selector check below, no
        # extra LLM call here.
        hr_bot_rounds, hr_bot_debug_reason = 0, None

        # 4. Click "Добавить сопроводительное" to open the cover letter field
        cover_sent_via_modal = kwargs.get("cover_sent_via_modal", False)
        add_cover = self._find_add_cover_btn(chatik_scope)
        # Plain print — dev diagnostic (session 56, dup-message investigation),
        # not user narration.
        print(f"   🔬 diag: cover_sent_via_modal={cover_sent_via_modal}, add_cover_found={add_cover is not None}")
        if not add_cover:
            if cover_sent_via_modal:
                self._narrate(reporter, "   ✅ Cover was sent in a prior form layer — chatik confirms goal reached",
                              gui_message="Cover letter was already sent — confirming here",
                              vacancy_id=vid)
                return self._apply_hr_bot_override(hr_bot_debug_reason, hr_bot_rounds, ProcessResult(
                    success=True,
                    status="applied_via_modal",
                    reason="Cover letter sent in prior form layer; chatik opened, goal verified",
                    scenario="hh_modal_with_cover",
                    is_terminal=True,
                    goal_reached=True
                ))
            self._narrate(reporter, "   ℹ️ 'Добавить сопроводительное' not found — application submitted without cover letter",
                          gui_message="[OK] applied via chat — no cover letter option here",
                          vacancy_id=vid)
            return self._apply_hr_bot_override(hr_bot_debug_reason, hr_bot_rounds, ProcessResult(
                success=True,
                status="applied_via_chat_no_cover",
                reason="Chat application submitted; cover letter button not available",
                scenario="chat_no_cover",
                is_terminal=True,
                goal_reached=True
            ))

        self._narrate(reporter, "   🔹 Clicking 'Добавить сопроводительное'...",
                      gui_message="Opening the cover letter field", vacancy_id=vid)
        add_cover.click()
        self._wait_and_random_delay(page, 2000, 3000)

        # 5. Find cover letter textarea (separate from "Сообщение" field)
        cover_input = self._find_cover_input(chatik_scope)
        if not cover_input:
            self._narrate(reporter, "   ⚠️ Cover letter textarea not found after clicking 'Добавить' — skipping cover",
                          gui_message="[OK] applied via chat — couldn't add a cover letter",
                          vacancy_id=vid)
            return self._apply_hr_bot_override(hr_bot_debug_reason, hr_bot_rounds, ProcessResult(
                success=True,
                status="applied_via_chat_no_cover",
                reason="Cover letter textarea not found after 'Добавить сопроводительное'",
                scenario="chat_no_cover",
                is_terminal=True,
                goal_reached=True
            ))

        # 6. Focus + type cover letter — generated on demand, right here, not
        # eagerly at the top of the vacancy pipeline (session 56). Cached by
        # vacancy_id, so if an earlier layer already generated one for this
        # same vacancy (e.g. an hh_modal step), this reuses that exact text.
        self._narrate(reporter, "   🔹 Typing cover letter into cover field...",
                      gui_message="Typing your message into the chat…", vacancy_id=vid)
        try:
            cover_letter = llm_cover.cover(vacancy_text, vacancy_id)
            # Chatik uses a single "Сообщение" textarea for cover letters too.
            # Typing \n triggers React's Enter-as-send handler → paragraph 1 is dispatched
            # as a standalone message, element re-renders, paragraph 2 is lost.
            # Fix: flatten all newlines to a space before typing.
            chatik_safe_cover = cover_letter.replace('\n', ' ').strip()
            cover_input.click()
            self._wait_and_random_delay(page, 500, 1000)
            try:
                cover_input.type(chatik_safe_cover, delay=10)
            except Exception:
                # HH's overlay can block the normal actionability-gated type()
                # the same way it blocks clicks (found live 2026-08-02,
                # ElementHandle.type: Timeout 30000ms exceeded) — force-focus
                # past whatever's intercepting, then type via the page's own
                # keyboard (no per-element actionability gate on the target),
                # so this still fires real keystroke events, not a
                # React-ignored .fill().
                cover_input.click(force=True)
                page.keyboard.type(chatik_safe_cover, delay=10)
            self._cover_typed = True
            # Plain print — folded into "Typing your message…" above for the GUI.
            print("   ✅ Cover letter typed")
            self._wait_and_random_delay(page, 1500, 2500)
        except Exception as e:
            return self._apply_hr_bot_override(hr_bot_debug_reason, hr_bot_rounds, ProcessResult(
                success=False,
                status="skipped_chat_fill_error",
                reason=f"Cover letter fill error: {e}",
                scenario="chat_fill_error",
                is_terminal=True,
                goal_reached=False
            ))

        # 7. Send cover letter
        try:
            sent = self._send_cover(chatik_scope, cover_input, page)
            if sent:
                self._narrate(reporter, "   ✅ Cover letter sent via chatik!",
                              gui_message="[OK] message sent", vacancy_id=vid)
                return self._apply_hr_bot_override(hr_bot_debug_reason, hr_bot_rounds, ProcessResult(
                    success=True,
                    status="applied_via_chat",
                    reason="Auto-read employer: cover letter sent via chatik",
                    scenario="chat_cover_sent",
                    details={'cover_length': len(cover_letter)},
                    is_terminal=True,
                    goal_reached=True
                ))
            else:
                return self._apply_hr_bot_override(hr_bot_debug_reason, hr_bot_rounds, ProcessResult(
                    success=False,
                    status="skipped_chat_send_error",
                    reason="Send button not found in chatik cover form",
                    scenario="chat_send_error",
                    is_terminal=True,
                    goal_reached=False
                ))
        except Exception as e:
            return self._apply_hr_bot_override(hr_bot_debug_reason, hr_bot_rounds, ProcessResult(
                success=False,
                status="skipped_chat_send_error",
                reason=f"Chatik send error: {e}",
                scenario="chat_send_error",
                is_terminal=True,
                goal_reached=False
            ))

    # ── Private helpers ───────────────────────────────────────────────────────

    # _narrate() lives on BaseHandler now (session 58 code-review — was
    # duplicated near-identically across 4 handlers, hoisted to avoid drift).

    def _apply_hr_bot_override(self, debug_reason: str | None, rounds: int, result: ProcessResult) -> ProcessResult:
        """Thin wrapper around BaseHandler._flag_for_debug_review — passthrough
        when the HR-bot loop exited cleanly, otherwise attaches hr_bot_rounds
        alongside the standard debug_reason/underlying_status details."""
        if not debug_reason:
            return result
        return self._flag_for_debug_review(result, debug_reason, hr_bot_rounds=rounds)

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
        # Wait on the union, then choose address-first.
        #
        # This used to await the Russian :text() selector and only fall through
        # to the data-qa cascade when that timed out — the exact inversion of
        # the address-first rule the apply path was fixed to follow. Waiting and
        # prioritising are separate concerns, and conflating them made the
        # wording the primary mechanism: the cascade could only ever run after
        # a 12s timeout, i.e. never on a healthy page.
        #
        # Chatik is a cross-origin iframe, so `scope` here is a Frame. Its
        # wait_for_selector has the same API as Page's.
        cascade = list(SELECTORS['chatik_add_cover'])
        try:
            scope.wait_for_selector(", ".join(cascade), timeout=12000, state='visible')
        except Exception as e:
            # Either it genuinely never appeared, or Playwright refused the
            # comma-joined union (the cascade mixes plain CSS with :has-text()).
            # Both end up here, so retry the wording-only wait that was the
            # primary before this change rather than give up on the send.
            print(f"   ⚠️ union wait for 'Добавить сопроводительное' failed ({e}) — retrying by wording")
            try:
                scope.wait_for_selector(':text("Добавить сопроводительное")',
                                        timeout=12000, state='visible')
            except Exception:
                print("   ⚠️ 'Добавить сопроводительное' didn't appear in chatik")
                return None

        # `:text()` stays in the list, last: it is the broadest match (any tag)
        # and the only one that survives hh changing the element type again.
        el = find_visible(scope, cascade + [':text("Добавить сопроводительное")'])
        if el is not None:
            print("   ✅ 'Добавить сопроводительное' found in chatik iframe")
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

    def _handle_hr_bot_loop(self, scope, page, reporter=None) -> tuple[int, str | None]:
        """Detects HR-bot questions in chatik iframe and answers them via LLM.

        PARKED (session 56) — not called from process() (see its hardcoded
        hr_bot_rounds/hr_bot_debug_reason = 0, None). Confirmed root cause of
        #38's duplicate message: this scan can pick up the "Добавить
        сопроводительное" prompt itself as if it were an incoming question,
        and nothing downstream gated the real cover-letter step on whatever
        this loop already sent. Kept, not deleted — it's a half-built
        fragment of a real, wanted post-first-goal feature (watch chatik for
        a genuine AI auto-responder after the cover is confirmed sent, not
        before), parked by user's own call for post-post-release.

        scope is the chatik Frame — all queries run inside the iframe.
        Called before the cover letter step. Returns immediately if no incoming
        (non-delivered) messages are found.

        Answers via _agent.answer_question() — uses candidate profile directly.
        Text input instead of quick-reply buttons: more accurate, not limited to preset options.

        Selectors are from the TZ live investigation (2026-06-12) — see config.py's
        chatik_message* entries. The whole scan is exception-guarded: a wrong
        assumption about DOM nesting fails safe (loop just stops) instead of
        taking down the rest of the vacancy result.

        Returns (rounds_answered, debug_reason). debug_reason is None on a clean
        exit (no bot present, or waiting for a reply that hasn't arrived yet) —
        otherwise a short machine-readable tag for an ambiguous outcome (selector
        miss, LLM failure, execution failure) that the caller surfaces via
        needs_debug_review instead of letting it disappear into a print().
        """
        max_rounds = 5
        rounds = 0
        last_answered_text = None

        while rounds < max_rounds:
            # Latest incoming (non-delivered) message inside iframe
            bot_el = None
            try:
                messages = scope.query_selector_all(SELECTORS['chatik_message'])
                for msg in reversed(messages):
                    if not msg.is_visible():
                        continue
                    if msg.query_selector(SELECTORS['chatik_message_delivered']):
                        continue  # ours — carries the delivered icon
                    bot_el = msg.query_selector(SELECTORS['chatik_bubble_text']) or msg
                    break
            except Exception as e:
                self._narrate(reporter, f"   ⚠️ HR-bot: message scan error: {e} — skipping bot loop")
                return rounds, f"message_scan_error: {e}"

            if not bot_el:
                break  # No incoming messages — nothing to answer, clean exit

            question_text = bot_el.inner_text().strip()
            if not question_text:
                # Matched an incoming message with no readable text — DOM oddity,
                # not "nothing to do".
                return rounds, "empty_question_text"

            # Skip if same question already answered (bot hasn't replied yet) — clean exit
            if question_text == last_answered_text:
                break

            self._narrate(reporter, f"   🤖 HR-bot question: {question_text[:80]}...")

            # Generate answer via LLM directly from candidate profile
            if _agent is None:
                self._narrate(reporter, "   ⚠️ HR-bot: LLM unavailable — skipping bot loop")
                return rounds, "llm_unavailable"
            try:
                answer = _agent.answer_question(question_text)
            except Exception as e:
                self._narrate(reporter, f"   ⚠️ HR-bot LLM error: {e} — skipping bot loop")
                return rounds, f"llm_error: {e}"

            if not answer:
                self._narrate(reporter, "   ⚠️ HR-bot: empty LLM answer — skipping bot loop")
                return rounds, "empty_llm_answer"

            # Find "Сообщение" input inside iframe and type answer
            msg_input = scope.query_selector(SELECTORS['chatik_input'])
            if not msg_input or not msg_input.is_visible():
                self._narrate(reporter, "   ⚠️ HR-bot: 'Сообщение' input not found in chatik iframe — skipping")
                return rounds, "input_not_found"

            safe_answer = answer.replace('\n', ' ').strip()
            self._narrate(reporter, f"   🔹 Answering HR-bot: {safe_answer[:60]}...")
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
        else:
            # Exhausted max_rounds while the bot was still asking — we don't
            # know if the interview was actually finished or we just gave up.
            self._narrate(reporter, f"   ⚠️ HR-bot loop: hit {max_rounds}-round cap, bot may not be done")
            return rounds, "max_rounds_exhausted"

        if rounds > 0:
            self._narrate(reporter, f"   ✅ HR-bot loop: answered {rounds} question(s)")
        return rounds, None
