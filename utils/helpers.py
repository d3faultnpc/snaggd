import random
import time
from typing import Any

def random_delay(min_ms: int, max_ms: int) -> int:
    """
    Human-like delay in the given range.
    Returns actual delay in milliseconds.
    """
    delay = random.randint(min_ms, max_ms)
    time.sleep(delay / 1000.0)
    return delay

def safe_get_text(element, default: str = "") -> str:
    """Safely extracts inner text from an element."""
    try:
        return element.inner_text().strip() if element else default
    except:
        return default

def safe_get_attribute(element, attr: str, default: str = "") -> str:
    """Safely extracts an attribute from an element."""
    try:
        return element.get_attribute(attr) or default if element else default
    except:
        return default

def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncates text to max_length, appending suffix if cut."""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix