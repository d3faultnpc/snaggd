"""A page that carries hh's chat link has already applied, whatever else it shows.

2026-09-14, vacancy #17: the questionnaire went out, letter
included, and the next page was the ordinary "Отклик отправлен / Резюме
доставлено" with the "Написать" link. It also carried a progress bar hh now
draws there — "для одного приглашения требуется около десяти откликов", 8 of
10 — and the detector's wording rule read that as a multi-step modal at step 8.
hh_modal.py was sent onto a page with no modal: three jams, three captures,
and an application that had gone out was filed as skipped_hh_modal.

The chat link is an address the page can only have after a response. It now
outranks the progress words, unless a dialog with a form is actually open.
"""
import sys
from pathlib import Path

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

from adapters.hh.detector import FormDetector
from adapters.hh.handlers.base import FormInfo, FormType

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


det = FormDetector()
classify = det._classify_form

# The 2026-09-14 #17 page: chat link present, a progress bar at 8, no dialog.
info = FormInfo(form_type=FormType.UNKNOWN, input_count=0, has_chat_link=True,
                has_progress=True, progress_step=8)
check("the post-apply page with hh's progress bar is the chat, not modal step 8",
      classify(info, "отклик отправлен резюме доставлено написать") == FormType.CHAT_INTERFACE)

# A real multi-step modal: a dialog with a form is open, chat link not yet there.
info = FormInfo(form_type=FormType.UNKNOWN, input_count=1, has_modal_form=True,
                has_progress=True, progress_step=2)
check("a dialog with a form and a step counter is still the modal",
      classify(info, "шаг 2 из 3") == FormType.HH_MODAL_STEP1)

# Progress words on a page with no chat link keep their old meaning.
info = FormInfo(form_type=FormType.UNKNOWN, input_count=0, has_progress=True, progress_step=2)
check("progress words without a chat link still read as a multi-step modal",
      classify(info, "шаг 2 из 3") == FormType.HH_MODAL_STEP2)

# The chat link with a dialog form open: the dialog wins, as before.
info = FormInfo(form_type=FormType.UNKNOWN, input_count=1, has_chat_link=True, has_modal_form=True)
check("a chat link under an open form dialog does not shortcut the dialog",
      classify(info, "") == FormType.HH_MODAL_STEP1)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
