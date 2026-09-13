"""The chat that never opened is named, not impersonated by the page.

2026-09-09, Сбер2B (137103713): after the questionnaire the claw clicked into
the chat, no chatik frame appeared within 12s, and the page itself was taken as
the chat "for a possible future redesign". Twelve more seconds went on hunting
the cover control on a vacancy page, and the record said "cover letter button
not available" — the wrong decision named. With a navigator plugged in, the
same path would ask about that control over every link on the page, with none
of the locks the survey node has.

Two things are pinned. The frame is found by what it IS — a frame whose
document is chatik's, on any tab of the context — not by the iframe element's
class, which used to gate the search and could hide the frame behind a renamed
class. And when there is no frame, the decision that failed is the chat, it
jams under its own node, and the hunt for the button does not start.
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

_ENGINE = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE))

from adapters.hh.handlers.chat import ChatHandler
from utils.navigation import NODES

results = []


def check(label, condition):
    print(f"  {'✅' if condition else '❌'} {label}")
    results.append(bool(condition))


class _Frame:
    def __init__(self, url): self.url = url


class _Page:
    def __init__(self, frames, context=None):
        self.frames = frames
        self.context = context

    def wait_for_timeout(self, ms): pass


class _Context:
    def __init__(self, pages): self.pages = pages


h = ChatHandler.__new__(ChatHandler)

# The frame on the page that clicked: found by URL, not by any element.
chat = _Frame("https://chatik.hh.ru/chat/123")
page = _Page([_Frame("https://hh.ru/vacancy/1"), chat])
page.context = _Context([page])
with redirect_stdout(io.StringIO()):
    got = h._wait_for_chatik_frame(page, timeout_s=1)
check("the chat frame is found by its own URL", got is chat)

# The frame on another tab of the same context.
main = _Page([_Frame("https://hh.ru/vacancy/1")])
other = _Page([_Frame("about:blank"), chat])
main.context = other.context = _Context([main, other])
buf = io.StringIO()
with redirect_stdout(buf):
    got = h._wait_for_chatik_frame(main, timeout_s=1)
check("a chat that opened on another tab is still the chat", got is chat)
check("and says so", "another tab" in buf.getvalue())

# No frame anywhere: None, within the timeout, without touching any class.
lonely = _Page([_Frame("https://hh.ru/vacancy/1")])
lonely.context = _Context([lonely])
with redirect_stdout(io.StringIO()):
    got = h._wait_for_chatik_frame(lonely, timeout_s=0.5)
check("no frame on any tab is None", got is None)

src = (_ENGINE / "adapters/hh/handlers/chat.py").read_text(encoding="utf-8")
_body = src.split("def _wait_for_chatik_frame")[1].split("\n    def ")[0]
_code = "\n".join(l for l in _body.splitlines() if not l.strip().startswith("#"))
check("the iframe element's class no longer gates the search",
      "chatik-integration-iframe" not in _code and "wait_for_selector" not in _code)
check("a missing chat is a named node", "chatik_frame" in NODES)
check("and process() jams on it instead of taking the page for the chat",
      'jam("chatik_frame"' in src and "chatik_scope = page" not in src)
check("the record then blames the chat, not the button",
      "the chat frame never appeared" in src)

print()
print(f"{sum(results)}/{len(results)} passed")
if sum(results) != len(results):
    sys.exit(1)
