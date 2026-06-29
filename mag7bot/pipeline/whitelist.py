"""Domain whitelist filter — PRD §4 hard rule.

Anything whose resolved publisher is outside the approved list is dropped,
never sent. We match against both the item's `publisher` field and its URL
host, so a wire item is kept whether the source labelled it "Reuters" or only
gave a reuters.com link.

Whitelist entries mix bare names ("reuters") and domains ("reuters.com").
URL hosts are matched by exact/suffix equality so ``evil-reuters.com`` is not
accepted as ``reuters.com``. Publisher *names* are matched as whole words (not
substrings), so a label like ``"Reutersclone"`` no longer slips through on the
``reuters`` entry.
"""

from __future__ import annotations

import re
from typing import List
from urllib.parse import urlparse

from ..schemas import RawItem


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def approved(publisher: str, url: str, whitelist: List[str]) -> bool:
    """Whitelist check on a (publisher name, url) pair."""
    publisher = (publisher or "").lower()
    host = _host(url)
    for entry in whitelist:
        e = entry.lower()
        # Domain-style entry: require host equality or a dotted suffix match.
        if "." in e:
            if host == e or host.endswith("." + e):
                return True
        # Name-style entry: whole-word match on the publisher label, so
        # "reuters" matches "Reuters" / "Thomson Reuters" but not "reutersbot".
        elif e and publisher and re.search(r"\b" + re.escape(e) + r"\b", publisher):
            return True
    return False


def is_approved(item: RawItem, whitelist: List[str]) -> bool:
    return approved(item.publisher, item.url, whitelist)


def filter_approved(items: List[RawItem], whitelist: List[str]) -> List[RawItem]:
    return [it for it in items if is_approved(it, whitelist)]
