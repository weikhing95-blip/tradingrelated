"""Domain whitelist filter — PRD §4 hard rule.

Anything whose resolved publisher is outside the approved list is dropped,
never sent. We match against both the item's `publisher` field and its URL
host, so a wire item is kept whether the source labelled it "Reuters" or only
gave a reuters.com link.

Matching is substring-based against the lowercase whitelist entries (which mix
bare names like "reuters" and domains like "reuters.com"); this is deliberately
lenient on the publisher *name* but the URL host is checked by suffix so
``evil-reuters.com`` is not accepted as ``reuters.com``.
"""

from __future__ import annotations

from typing import List
from urllib.parse import urlparse

from ..schemas import RawItem


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_approved(item: RawItem, whitelist: List[str]) -> bool:
    publisher = (item.publisher or "").lower()
    host = _host(item.url)
    for entry in whitelist:
        e = entry.lower()
        # Domain-style entry: require host equality or a dotted suffix match.
        if "." in e:
            if host == e or host.endswith("." + e):
                return True
        # Name-style entry: substring match on the publisher label.
        if e and publisher and e in publisher:
            return True
    return False


def filter_approved(items: List[RawItem], whitelist: List[str]) -> List[RawItem]:
    return [it for it in items if is_approved(it, whitelist)]
