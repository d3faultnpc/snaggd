from .base import BaseHandler, FormType, ProcessResult
from config import SELECTORS


class ChatHandler(BaseHandler):
    """
    Handler for auto-read employers (Sber etc.) — applies via chatik.

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

    def process(self, page, cover_letter: str, hr_matcher=None) -> ProcessResult:
        # 1. Click "Go to chat"
        chat_link = page.query_selector(SELECTORS['chat_link'])
        if not chat_link or not chat_link.is_visible():
            return ProcessResult(
                success=False,
                status="skipped_no_chat_button",
                reason="Кнопка чата не найдена (vacancy-response-link-view-topic)",
                scenario="chat_error"
            )

        print("   🔹 Кликаю 'Перейти в чат'...")
        chat_link.click()
        self._wait_and_random_delay(page, 3000, 5000)

        # 2. Look for "+Add cover letter" link in chatik
        add_cover = page.query_selector(SELECTORS['chatik_add_cover'])
        if add_cover and add_cover.is_visible():
            print("   🔹 Кликаю '+Добавить сопроводительное'...")
            add_cover.click()
            self._wait_and_random_delay(page, 2000, 3000)

        # 3. Find input field — verified data-qa first, then fallback
        chat_input = None
        for selector in [
            SELECTORS['chatik_input'],
            'div[contenteditable="true"]',
            'textarea',
        ]:
            el = page.query_selector(selector)
            if el and el.is_visible():
                chat_input = el
                break

        if not chat_input:
            return ProcessResult(
                success=False,
                status="skipped_no_chat_input",
                reason="Поле ввода в chatik не найдено",
                scenario="chat_no_input"
            )

        print("   🔹 Заполняю сопроводительное в chatik...")
        try:
            tag = chat_input.evaluate('el => el.tagName.toLowerCase()')
            if tag == 'div':
                chat_input.type(cover_letter, delay=10)
            else:
                chat_input.type(cover_letter, delay=10)
            print("   ✅ Сообщение заполнено")
            self._wait_and_random_delay(page, 2000, 3000)
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_chat_fill_error",
                reason=f"Ошибка заполнения chatik: {e}",
                scenario="chat_fill_error"
            )

        # 4. Send — try submit button first, fallback to Enter
        try:
            send_btn = page.query_selector('button:has-text("Отправить")')
            if send_btn and send_btn.is_visible():
                print("   🔹 Кликаю 'Отправить' в chatik...")
                send_btn.click()
            else:
                print("   🔹 Отправляю через Enter...")
                chat_input.press("Enter")

            self._wait_and_random_delay(page, 3000, 4000)
            print("   ✅ Сопроводительное отправлено через chatik!")

            return ProcessResult(
                success=True,
                status="applied_via_chat",
                reason="Авточтение: сопроводительное отправлено через chatik",
                scenario="chat_cover_sent",
                details={'cover_length': len(cover_letter)}
            )
        except Exception as e:
            return ProcessResult(
                success=False,
                status="skipped_chat_send_error",
                reason=f"Ошибка отправки в chatik: {e}",
                scenario="chat_send_error"
            )
