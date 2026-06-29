"""Best-effort article fetch + main-text extraction.

News sources hand us only a headline and a short blurb, so an LLM summary can't
surface the facts buried in the article body. This module fetches the article
URL and extracts its readable text (paragraph/heading content + the meta
description), which the summarizer then compresses — with the faithfulness guard
ensuring nothing is invented.

Dependency-free (stdlib ``html.parser``): robust extraction of full article
prose isn't the goal — capturing the lede and key paragraphs is enough for a
bite-size summary. Everything is best-effort: any failure (timeout, paywall,
non-HTML, JS-only page) returns "" and the caller falls back to the blurb.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import List
from urllib.parse import urlparse

import httpx

_UA = "Mozilla/5.0 (compatible; mag7bot/1.0; +https://t.me/)"

# Aggregator/consent-wall boilerplate that carries no real news. When a page
# yields only this (e.g. a Google News interstitial's og:description, or a
# cookie-consent gate), we treat it as no-content and fall back to the headline.
JUNK_BLURBS = (
    "comprehensive up-to-date news coverage, aggregated from sources all over the world by google news",
    "aggregated from sources all over the world by google news",
    "your browser is not supported",
    "to continue, please enable",
    "we and our partners use cookies",
    "by continuing to use this site",
    "please enable javascript",
    "enable javascript to",
    "access denied",
    "you are being redirected",
)


def is_boilerplate(text: str) -> bool:
    """True if the text is aggregator/consent boilerplate, not real content."""
    t = (text or "").strip().lower()
    return bool(t) and any(j in t for j in JUNK_BLURBS)

# Containers whose text is boilerplate, not article body.
_SKIP_TAGS = {
    "script", "style", "noscript", "nav", "aside", "footer", "header",
    "form", "figure", "figcaption", "button", "svg",
}
# Tags whose text we treat as readable content.
_TEXT_TAGS = {"p", "h1", "h2", "h3", "li"}


class _ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._text_depth = 0
        self._buf: List[str] = []
        self.paragraphs: List[str] = []
        self.meta_description = ""

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _TEXT_TAGS:
            self._text_depth += 1
        elif tag == "meta" and not self.meta_description:
            a = dict(attrs)
            if a.get("property") == "og:description" or a.get("name") == "description":
                self.meta_description = (a.get("content") or "").strip()

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _TEXT_TAGS and self._text_depth:
            self._text_depth -= 1
            text = "".join(self._buf).strip()
            self._buf.clear()
            if len(text) >= 40:  # skip nav scraps / one-word list items
                self.paragraphs.append(" ".join(text.split()))

    def handle_data(self, data):
        if self._skip_depth == 0 and self._text_depth > 0:
            self._buf.append(data)


def extract_text(html: str, max_chars: int = 4000) -> str:
    """Extract readable article text from raw HTML. Returns "" if too thin."""
    if not html:
        return ""
    parser = _ArticleParser()
    try:
        parser.feed(html)
    except Exception:
        return ""
    body = "\n".join(parser.paragraphs).strip()
    # If the page yielded little prose (JS-rendered, paywalled), the meta
    # description is often the only usable summary text.
    if len(body) < 200 and parser.meta_description:
        body = parser.meta_description
    body = body[:max_chars].strip()
    if len(body) < 80 or is_boilerplate(body):
        return ""
    return body


async def fetch_article_text(url: str, timeout: float = 8.0, max_chars: int = 4000) -> str:
    """Fetch ``url`` and return extracted article text (or "" on any failure)."""
    if not url:
        return ""
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "").lower()
            if "html" not in ctype and "xml" not in ctype and ctype:
                return ""
            # Unresolved Google redirects / consent gates aren't the article —
            # their only text is aggregator boilerplate, so don't extract them.
            final_host = (urlparse(str(resp.url)).hostname or "").lower()
            if "google." in final_host or "consent." in final_host or "news.google" in final_host:
                return ""
            return extract_text(resp.text, max_chars=max_chars)
    except Exception:
        return ""
