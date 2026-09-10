import json
import re
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from typing import Optional

from ..dom import (find_topmost_dialog, find_visible, is_employer_question_field,
                   is_in_data_collector, iter_visible)
from config import SELECTORS
from utils.navigation import jam

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
    has_response_submit: bool = False  # vacancy-response-letter-submit visible → cover form ready
    has_popup_questions: bool = False
    has_task_questions: bool = False
    has_test_form: bool = False
    has_modal_form: bool = False  # role="dialog" with visible textarea → cover-required response modal
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
    is_terminal: bool = True    # stop the goal-directed loop after this result
    goal_reached: bool = False  # application successfully submitted
    next_hint: Optional[str] = None  # optional hint for next handler selection

# ── Option matching, shared by every handler that reads a choice group ───────
# Lived twice, inline, in questions.py and hh_modal.py, and had already drifted:
# one normalised nbsp and multi-space, the other did a bare .strip().lower().
# 2026-09-09.

FREE_TEXT_OPTIONS = ("свой вариант", "другое", "other")


def norm_option(s: str) -> str:
    """Normalize option text for comparison: nbsp, multi-space, space-before-punct."""
    s = (s or "").replace('\u00a0', ' ')
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'\s+([.,!?;:])', r'\1', s)
    return s.strip().lower()


def is_free_text_option(option_text: str) -> bool:
    return norm_option(option_text) in FREE_TEXT_OPTIONS


# ── Cover delivery, shared by the three routes that can perform one ──────────
# hh offers three places a cover letter can actually be sent, and which one a
# vacancy uses is hh's choice, not ours: the response modal (where the letter is
# mandatory), chatik, and the post-apply cover form. All three are real; what
# was missing was a single fact saying which one delivered, and the evidence.
#
# Naming them here rather than letting each handler describe itself: a route
# nobody can enumerate is a route the record cannot be checked against, and
# "was a letter delivered" was being answered from the status string, which is
# produced by whichever layer ended the loop rather than by whichever layer
# delivered. See adapters/hh/adapter.py's cover_delivery for the carry.
COVER_ROUTES = ("modal", "chat", "cover_only")

_DELIVERY_KEYS = ("cover_delivered", "cover_length", "cover_text")


def cover_delivery_of(details: Optional[dict]) -> Optional[dict]:
    """The delivery this layer declared, with its evidence — or None.

    Fails closed on the claim, not on the absence: a route this module cannot
    name reads as "nothing was delivered here". A misspelled route silently
    claiming a letter reached an employer is the failure worth being strict
    about; a real delivery losing its name is caught by the handler tests.
    """
    if not details:
        return None
    if details.get("cover_delivered") not in COVER_ROUTES:
        return None
    return {k: details[k] for k in _DELIVERY_KEYS if k in details}


def coerce_answers(answers: dict) -> dict:
    """fill_form() promises one string per field. checkbox_group is the one
    exception — its answer is a list — and a model that returns a list for
    something else must not take the run down on the next `.strip()`.
    """
    out = {}
    for k, v in (answers or {}).items():
        if str(k).startswith("cbgroup_") or isinstance(v, str):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = ", ".join(str(x) for x in v)
        else:
            out[k] = "" if v is None else str(v)
    return out


def choose_checkbox_options(answer, options: list[str]) -> tuple[list[int], Optional[str]]:
    """Which of `options` a checkbox-group answer asks for, plus any free text.

    Returns (indices into `options`, free_text or None).

    A checkbox group is multi-select — that is what the control means — so the
    answer may name several options. It is also the LLM's output, which is the
    part of this that cannot be relied on to hold a format, so this reads
    several shapes rather than one:

      * a real list (the declared format, a JSON array)
      * one exact option, which is what single-choice-looking groups still give
      * several options run together in one string
      * "open: <text>" for a genuine free-text answer

    Splitting on commas would be the obvious way to read the third shape and is
    wrong: option texts contain commas of their own. Measured 2026-09-09 on one
    live form — "Цифровые продукты (SaaS, сервисы, приложения)" splits into three
    fragments that are not options, and "Одежда, обувь, аксессуары" into two. So
    matching runs the other way round: each option is looked for INSIDE the
    answer, longest first, and a match consumes its span so a shorter option
    cannot match the same words again.

    Free text is a last resort, never a way to say several things: a model that
    lists real option names after "open:" is asking for those options, and gets
    them ticked instead of recited into a comment box.
    """
    real = [(i, norm_option(o)) for i, o in enumerate(options) if not is_free_text_option(o)]

    def by_exact(values) -> list[int]:
        out = []
        for v in values:
            nv = norm_option(v if isinstance(v, str) else str(v))
            for i, no in real:
                if no and no == nv and i not in out:
                    out.append(i)
                    break
        return out

    if isinstance(answer, (list, tuple)):
        return sorted(by_exact(answer)), None

    text = (answer or "").strip() if isinstance(answer, str) else ""
    if not text:
        return [], None

    free_text = None
    if text.lower().startswith("open:"):
        free_text = text[5:].strip()
        text = free_text

    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, list) and parsed:
            picked = sorted(by_exact(parsed))
            if picked:
                return picked, None

    # Longest option first, each match consuming its span.
    picked: list[int] = []
    remaining = norm_option(text)
    for i, no in sorted(real, key=lambda t: -len(t[1])):
        if not no:
            continue
        m = re.search(rf'(?<!\w){re.escape(no)}(?!\w)', remaining)
        if m:
            picked.append(i)
            remaining = remaining[:m.start()] + " " + remaining[m.end():]
    if picked:
        return sorted(picked), None

    # Nothing on the page was named. Only now is this genuinely free text — and
    # a bare answer with no "open:" prefix counts, since the group may have no
    # free-text option at all and the caller has to see the text to report it.
    return [], (free_text if free_text is not None else text)


class BaseHandler(ABC):
    """Base class for form handlers."""

    @abstractmethod
    def can_handle(self, form_type: FormType) -> bool:
        """Returns True if this handler can process the given form type."""
        pass

    @abstractmethod
    def process(self, page, **kwargs) -> ProcessResult:
        """Process the form. kwargs: vacancy_text, vacancy_id, llm_cover (an
        LLMCover instance — call llm_cover.cover(vacancy_text, vacancy_id) on
        demand, only if this handler actually needs to send a real cover
        letter; never generate one eagerly), reporter, cover_sent_via_modal."""
        pass

    @abstractmethod
    def verify_submission(self, page) -> bool:
        """DOM щуп: confirm submission succeeded after process() returned success.
        Check DOM for success signals (modal gone, notification visible, button changed).
        Return False → caller sets status=applied_unverified and increments error counter.
        """
        pass

    def _poll_for_success(self, page, timeout_s: int = 5) -> bool:
        """Shared helper: poll DOM for HH modal submission success signals."""
        import time
        end = time.time() + timeout_s
        success_selectors = [
            '[data-qa*="vacancy-response-success"]',
            '[data-qa*="response-completed"]',
            '[data-qa*="response-notification"]',
        ]
        modal_selectors = [
            '[role="dialog"]',
            '[data-qa*="modal"]',
            '[data-qa*="response-popup"]',
            '.HH-Modal',
        ]
        # Both probes moved off single-match query_selector: with the old code a
        # hidden first match of '[role="dialog"]' read as "no modal on screen",
        # and the absence branch below turns that straight into "submitted".
        # Checking every match can only report the modal as still-present more
        # often — the conservative direction for a success predicate.
        while time.time() < end:
            # Success notification appeared — positive evidence, trust it.
            if find_visible(page, success_selectors) is not None:
                return True
            # Modal disappeared (submit closed the dialog). This is an
            # absence-based signal and inherits every weakness of one: it also
            # fires if the modal selectors simply never matched this modal.
            # Kept because handlers call it only AFTER their own submit click,
            # where a closed dialog really is the outcome — but it is why a
            # False here escalates to applied_unverified rather than a hard fail.
            if find_visible(page, modal_selectors) is None:
                return True
            time.sleep(0.5)
        return False

    def _wait_and_random_delay(self, page, min_ms: int = 2000, max_ms: int = 5000) -> None:
        """Human-like random delay."""
        import random
        import time
        delay = random.randint(min_ms, max_ms)
        time.sleep(delay / 1000.0)

    def _narrate(self, reporter, message: str, level: str = "info",
                 gui_message: str = None, vacancy_id: str = None) -> None:
        """print + mirror into an attached GUI client's log when a reporter is attached —
        same contract as HHAdapter._say() (session 58). Hoisted here from 4
        near-identical per-handler copies (chat.py/hh_modal.py/questions.py/
        cover_only.py) so they can't drift from each other on a future edit.
        Always actor="scan" — a handler's own hands filling/clicking a form,
        never the LLM's output (core/llm_agent.py's call_type tagging covers
        the matching llm-side narration)."""
        print(message)
        if reporter is not None:
            reporter.emit(
                gui_message if gui_message is not None else message.strip(),
                level=level, actor="scan", vacancy_id=vacancy_id,
            )

    @staticmethod
    def _flag_for_debug_review(result: "ProcessResult", reason: str, **extra_details) -> "ProcessResult":
        """Surfaces an ambiguous mid-form failure (LLM answer couldn't be
        applied, a bot-message selector never matched, etc.) as
        needs_debug_review instead of letting a downstream success status
        quietly hide it. Preserves the underlying outcome in details, and
        needs_debug_review is retry-exempt in logger.is_processed() — no
        manual log surgery needed to revisit it later."""
        return ProcessResult(
            success=result.success,
            status="needs_debug_review",
            reason=f"Ambiguous execution: {reason}",
            scenario="needs_debug_review",
            details={**(result.details or {}), "debug_reason": reason,
                     "underlying_status": result.status, **extra_details},
            is_terminal=result.is_terminal,
            goal_reached=result.goal_reached,
        )

    def _find_element_by_selectors(self, page, selectors: list, visible_only: bool = True):
        """First VISIBLE element matching any selector, in cascade order.

        Used to call `page.query_selector()` per selector — first DOM match only.
        If that one match was hidden, this gave up on the selector entirely and
        moved to the next string in the cascade, even when a later match of the
        SAME selector was visible and clickable. That is the identical bug that
        made `click_apply_button` report "Apply button not found" on a page with
        four visible Apply buttons (fixed there 2026-08-02, left standing here).

        Live on the cover-letter path: cover_only.py finds both its textarea and
        its send button through this helper.
        """
        return find_visible(page, selectors, visible_only=visible_only)

    def _find_action_button(self, page, addresses: list, keywords: list):
        """The button that advances this form. Returns (element, how) or (None, None).

        Two tiers, and the order was never the problem here — both callers
        already tried addresses before wording. What was wrong with the wording
        tier was everything else about it:

        * It searched the WHOLE PAGE. Any 'продолжить' anywhere — page chrome,
          footer, an unrelated overlay — was a candidate. Scoped to the open
          dialog now, falling back to the page only when no dialog is open.
        * It did not know what it was clicking. hh's profile surveys carry a
          "Сохранить и продолжить" that matches the navigation keywords
          perfectly and writes to the user's hh profile rather than advancing
          anything. That is exactly what got clicked on 2026-08-11. Survey
          buttons are vetoed now.

        The wording tier stays, deliberately. Across all 123 captured pages hh
        ships NO data-qa on the "next/continue" button of a response modal —
        the wording is the only handle that exists on markup hh has not
        tagged. The goal is not zero Russian; it is that Russian is never the
        primary mechanism, is confined to the form being acted on, and cannot
        reach a button that does something else.

        Deliberately NOT a third tier: asking the model to pick a button when
        both of these miss. It would not remove a single hardcoded word — it
        would sit behind them — while adding a new way to click an unknown
        control on a real employer's form. If the canon/LLM boundary moves, it
        should move as a designed whole (audit item 9), not per button.
        """
        scope = find_topmost_dialog(page) or page

        for selector in addresses:
            try:
                page.wait_for_selector(f"{selector}:not([disabled])", timeout=5000)
            except Exception:
                pass  # already enabled, or genuinely absent — the query below decides
            for btn in iter_visible(scope, selector):
                try:
                    if not btn.is_disabled():
                        return btn, "address"
                except Exception:
                    continue

        for btn in iter_visible(scope, SELECTORS['buttons']):
            try:
                if btn.is_disabled() or is_in_data_collector(btn):
                    continue
                label = btn.inner_text().strip().lower()
            except Exception:
                continue
            if any(kw in label for kw in keywords):
                return btn, "wording"

        # Both tiers missed. The docstring above explains why a third tier was
        # deliberately NOT a model picking a button — that argument stands, and
        # this is not it: nothing is asked and nothing is clicked, the node is
        # only named so it can be counted.
        jam("action_button", "neither an address nor a known wording matched", scope=scope)
        return None, None

    def _find_cover_field(self, page, extra_selectors: list = None, reject=None):
        """The cover-letter textarea — never an employer's question field.

        The last entry of SELECTORS['cover_textarea'] is a bare 'textarea',
        deliberately, as a last resort for markup we have not seen. On an
        employer questionnaire that entry matches every question box on the
        page (one per question, no placeholder, no data-qa), so the cascade
        would hand back question #1 and the caller would type a cover letter
        into it and submit it to a real employer under the user's name.

        Nothing above stopped that: _is_salary_field only rejects fields whose
        placeholder or label mentions salary, and hh's question textareas carry
        no placeholder at all.

        Structural rejection instead of a keyword guess, and no LLM call: an
        employer question field is identifiable by where it sits in the DOM
        (inside task-body / vacancy-response-question, or name="task_N_text"),
        which is a fact about the markup rather than about the wording.

        `reject` is an extra per-candidate veto (hh_modal passes its salary
        check). A rejected candidate is skipped, not fatal — the cascade keeps
        going, which is what the hand-rolled loops it replaces did.
        """
        selectors = (extra_selectors or []) + list(SELECTORS['cover_textarea'])
        for selector in selectors:
            for el in iter_visible(page, selector):
                if is_employer_question_field(el):
                    print("   ⏭ Skipping an employer question field while looking for the cover box")
                    continue
                if reject is not None and reject(el):
                    continue
                return el
        jam("cover_field", f"{len(selectors)} address(es) tried, none held a usable box", scope=page)
        return None