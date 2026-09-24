"""Errors raised through the Page Store interface.

Two kinds matter to callers:

* :class:`FetchError` - this one page could not be fetched (404, retries exhausted,
  disallowed by robots.txt). The run may carry on with other pages.
* :class:`RunStoppedError` - the acquisition policy (ADR 0006) says stop the whole run,
  for example a WAF challenge with the browser fallback disabled. Callers must not
  record the page as failed; nothing is wrong with the page itself.
"""

from __future__ import annotations

from typing import Optional


class PageStoreError(Exception):
    """Base class for Page Store errors."""


class FetchError(PageStoreError):
    """One page could not be fetched.

    Attributes:
        url: The page URL.
        status: The last HTTP status seen, or ``None`` when there was no response.
    """

    def __init__(self, message: str, url: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.url = url
        self.status = status


class RunStoppedError(PageStoreError):
    """The acquisition policy requires the whole run to stop."""


class ChallengeError(RunStoppedError):
    """A WAF challenge that the policy may not, or could not, get past."""

    def __init__(self, message: str, url: str) -> None:
        super().__init__(message)
        self.url = url
