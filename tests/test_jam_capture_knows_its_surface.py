"""A jam capture writes what the surface can give, and says what it wrote.

Three kinds of surface reach the debug observer: a Page, the chatik Frame
(chat.py), and a dialog ElementHandle (base.py, questions.py, hh_modal.py).
Until 2026-09-12 all three were treated as a Page because all three answer
`query_selector_all`, and a Page snapshot begins with `page.screenshot(...,
full_page=False)` — which a Frame does not have and an ElementHandle refuses.
The snapshot failed on its first line, wrote nothing, and the observer still
printed "jam capture: jamNN_node_layers.html". Live 2026-09-11, vacancy #2:
`ElementHandle.screenshot() got an unexpected keyword argument 'full_page'`,
and no jam01_* file on disk.

Six of the twelve nodes hand over a non-Page surface. Two of them are the ones
the chatik iframe hides from every page-level snapshot, which is the reason the
observer was given the surface in the first place.
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

os.environ.setdefault("LLM_API_KEY", "test")

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


with patch("core.llm_agent.OpenAI"):
    from adapters.hh.adapter import HHAdapter


class _Logger:
    def load_applied_log(self): return []
    def log_daily(self, message): pass
    def is_processed(self, url, log): return None
    def log_result(self, log, **kwargs): log.append(kwargs)


def _observer(tmp):
    """The real _observe_jam closure, obtained the way run() registers it."""
    with patch("core.llm_agent.OpenAI"):
        a = HHAdapter(data_dir=Path(tmp))
    a.get_vacancies = lambda target_url=None: []
    registered = []
    with patch("adapters.hh.adapter.set_jam_observer",
               side_effect=lambda fn: registered.append(fn)):
        a.run(_Logger(), debug=True)
    a._jam_dir = Path(tmp) / "vacancy"
    # No browser page behind the surface: what the surface gives is all there is.
    a.browser.get_current_page = lambda: None
    return a, registered[0]


class _Frame:
    """What chat.py hands over: content(), no frames, no screenshot."""
    def query_selector_all(self, sel): return []
    def content(self): return "<html><body data-qa='chatik'>frame</body></html>"


class _Dialog:
    """What base.py hands over: inner_html(), a screenshot that refuses full_page."""
    def query_selector_all(self, sel): return []
    def inner_html(self): return "<div role='dialog'>dialog</div>"
    def screenshot(self, path): raise TypeError("unexpected keyword argument 'full_page'")


class _Page:
    """Enough of a Page for _debug_snapshot to run its first two writes."""
    frames = []
    def query_selector_all(self, sel): return []
    def query_selector(self, sel): return None
    def screenshot(self, path, full_page=False): Path(path).write_bytes(b"png")
    def inner_html(self, sel): return "<body>page</body>"
    def evaluate(self, js, *args): return []


with tempfile.TemporaryDirectory() as tmp:
    adapter, observe = _observer(tmp)
    out_dir = adapter._jam_dir

    buf = io.StringIO()
    with redirect_stdout(buf):
        observe("cover_input", "no field", _Frame())
    said = buf.getvalue()
    check("a Frame surface is captured by its own content()",
          (out_dir / "jam01_cover_input_scope.html").read_text() ==
          "<html><body data-qa='chatik'>frame</body></html>")
    check("and the capture line names that file, not a layers file it never wrote",
          "jam01_cover_input_scope.html" in said and "_layers.html" not in said)
    check("no snapshot error is printed for a surface that cannot take one",
          "debug_snapshot" not in said)

    buf = io.StringIO()
    with redirect_stdout(buf):
        observe("action_button", "no address", _Dialog())
    said = buf.getvalue()
    check("a dialog ElementHandle is captured by its inner_html()",
          (out_dir / "jam02_action_button_scope.html").read_text() ==
          "<div role='dialog'>dialog</div>")
    check("its screenshot() is never called with full_page",
          "full_page" not in said)

    buf = io.StringIO()
    with redirect_stdout(buf):
        observe("apply_button", "no address", _Page())
    said = buf.getvalue()
    check("a Page still gets the full snapshot",
          (out_dir / "jam03_apply_button.png").exists() and
          (out_dir / "jam03_apply_button.html").exists())
    check("and the capture line lists what the snapshot actually wrote",
          "jam03_apply_button.png" in said and "jam03_apply_button.html" in said)
    check("with no scope file for a surface that IS the page",
          not (out_dir / "jam03_apply_button_scope.html").exists())

    buf = io.StringIO()
    with redirect_stdout(buf):
        observe("form_type", "unknown shape", None)
    said = buf.getvalue()
    check("no surface and no page: the observer says so instead of naming a file",
          "nothing could be written" in said)

    check("the sequence counts every jam, captured or not",
          adapter._jam_seq == 4)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
