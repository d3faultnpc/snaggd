"""SiteAdapter — base class for all job-site adapters."""

from abc import ABC, abstractmethod


class SiteAdapter(ABC):
    """
    Base class for all job-site adapters.

    To add a new site (e.g. SuperJob), subclass SiteAdapter and implement
    all abstract methods. See adapters/hh/adapter.py for a reference impl.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable site name, e.g. 'hh.ru'."""

    @property
    @abstractmethod
    def auth_method(self) -> str:
        """Auth type: 'cookie' | 'oauth' | 'login'."""

    @abstractmethod
    def verify(self) -> bool:
        """Pre-flight check: env vars set, cookies exist, etc.
        Returns True if the adapter is ready to run."""

    @abstractmethod
    def start(self) -> bool:
        """Launch browser / open session. Returns True on success."""

    @abstractmethod
    def close(self) -> None:
        """Tear down browser / close session."""

    @abstractmethod
    def get_vacancies(self) -> list:
        """Return list of (url, title, index) tuples from the search page."""

    @abstractmethod
    def process_vacancy(self, url: str, title: str, index: int,
                        llm_cover, hr_matcher,
                        debug: bool = False, session_dir=None) -> dict:
        """Process one vacancy end-to-end.

        Returns dict: {'status', 'reason', 'scenario', 'details'}.
        """
