"""
Unit tests for HHBrowser.close() idempotency.
No real browser launched — mocks browser/playwright objects.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parent.parent))


def make_browser():
    with patch("adapters.hh.browser.sync_playwright"):
        from adapters.hh.browser import HHBrowser
        return HHBrowser()


def test_close_when_never_started():
    b = make_browser()
    b.close()  # should not raise
    b.close()  # second call — should not raise
    print("  ✅ close() on never-started browser: ok")


def test_close_idempotent():
    b = make_browser()

    mock_browser = MagicMock()
    mock_pw_manager = MagicMock()
    b.browser = mock_browser
    b._pw_manager = mock_pw_manager
    b.playwright = MagicMock()

    b.close()
    assert b.browser is None, "browser should be None after first close()"
    assert b._pw_manager is None, "_pw_manager should be None after first close()"
    assert b.playwright is None, "playwright should be None after first close()"
    assert mock_browser.close.call_count == 1
    assert mock_pw_manager.__exit__.call_count == 1

    b.close()  # second call — no new close() calls
    assert mock_browser.close.call_count == 1, "browser.close() called again on second close()"
    assert mock_pw_manager.__exit__.call_count == 1, "__exit__ called again on second close()"
    print("  ✅ close() idempotency: ok")


def test_close_survives_browser_exception():
    b = make_browser()

    mock_browser = MagicMock()
    mock_browser.close.side_effect = Exception("already closed")
    mock_pw_manager = MagicMock()
    b.browser = mock_browser
    b._pw_manager = mock_pw_manager
    b.playwright = MagicMock()

    b.close()  # should not propagate the exception
    assert b.browser is None
    assert b._pw_manager is None
    print("  ✅ close() survives browser.close() exception: ok")


test_close_when_never_started()
test_close_idempotent()
test_close_survives_browser_exception()
print("\n3/3 passed")
