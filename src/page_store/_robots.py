"""robots.txt evaluated per RFC 9309. Internal to the Page Store module.

``urllib.robotparser`` is not used: it normalises rule paths through
``urlparse``/``urlunparse``, which turns ddowiki's ``Disallow: /?`` into ``Disallow: /``
and so blocks the whole site. Here a rule path is a literal prefix of the URL's raw
path plus query, with ``*`` (any run of characters) and a trailing ``$`` (end of URL).

* Groups: one or more ``User-agent`` lines followed by rules. Every group whose
  user-agent value is contained in our product token (case-insensitive) applies, merged;
  when none does, the ``*`` groups apply, merged.
* The longest matching ``Allow``/``Disallow`` pattern wins; on a tie ``Allow`` wins. No
  match means allowed. An empty ``Disallow`` is no rule. ``/robots.txt`` is always
  allowed.
* ``Crawl-delay`` (not in RFC 9309, but honoured) is the largest value in the applying
  groups.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import urlsplit


@dataclass
class _Group:
    agents: List[str] = field(default_factory=list)
    rules: List[Tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)
    crawl_delay: Optional[float] = None


class RobotsRules:
    """The rules one robots.txt sets for one user agent."""

    def __init__(self, text: str, user_agent: str) -> None:
        token = _product_token(user_agent)
        groups = _parse(text)
        mine = [g for g in groups if any(a != "*" and a in token for a in g.agents)]
        if not mine:
            mine = [g for g in groups if "*" in g.agents]
        self._rules = [rule for g in mine for rule in g.rules]
        delays = [g.crawl_delay for g in mine if g.crawl_delay is not None]
        self.crawl_delay: Optional[float] = max(delays) if delays else None

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if target == "/robots.txt":
            return True
        best_len, allowed = -1, True
        for allow, pattern in self._rules:
            if _matches(pattern, target):
                length = len(pattern)
                if length > best_len or (length == best_len and allow):
                    best_len, allowed = length, allow
        return allowed


def _product_token(user_agent: str) -> str:
    match = re.match(r"[A-Za-z_-]+", user_agent.strip())
    return (match.group(0) if match else user_agent).lower()


def _parse(text: str) -> List[_Group]:
    groups: List[_Group] = []
    current: Optional[_Group] = None
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is None or in_rules:
                current = _Group()
                groups.append(current)
                in_rules = False
            current.agents.append(value.lower())
        elif key in ("allow", "disallow") and current is not None:
            in_rules = True
            if value:
                current.rules.append((key == "allow", value))
        elif key == "crawl-delay" and current is not None:
            in_rules = True
            try:
                current.crawl_delay = float(value)
            except ValueError:
                pass
    return groups


def _matches(pattern: str, target: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(piece) for piece in body.split("*"))
    return re.match(regex + ("$" if anchored else ""), target) is not None
