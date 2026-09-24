"""Canned-response adapter for the Page Store's transport seam (tests only)."""

from __future__ import annotations

from typing import Callable, Dict, List, Union

from page_store import Response

Reply = Union[Response, Exception]

CHALLENGE = Response(status=202, text="", headers={"x-amzn-waf-action": "challenge"})


def ok(html: str) -> Response:
    return Response(status=200, text=html)


class CannedTransport:
    """Plays the wiki from a script: per-URL queues of replies, then a fallback.

    Each reply is a :class:`Response` or an exception to raise. When a URL's queue is
    empty, *default* decides (a function of the URL); without one, the URL gets a 404.
    ``requests`` lists every URL fetched, in order.
    """

    def __init__(self, default: Callable[[str], Reply] | None = None) -> None:
        self.script: Dict[str, List[Reply]] = {}
        self.default = default
        self.requests: List[str] = []
        self.closed = False

    def reply(self, url: str, *replies: Reply) -> CannedTransport:
        self.script.setdefault(url, []).extend(replies)
        return self

    def fetch(self, url: str) -> Response:
        self.requests.append(url)
        queue = self.script.get(url)
        if queue:
            reply = queue.pop(0)
        elif self.default is not None:
            reply = self.default(url)
        else:
            reply = Response(status=404, text="not found")
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self) -> None:
        self.closed = True
