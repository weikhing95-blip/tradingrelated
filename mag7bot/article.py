"""Best-effort article fetch + main-text extraction.

News sources hand us only a headline and a short blurb, so an LLM summary can't
surface the facts buried in the article body. This module fetches the article
URL and extracts its readable text, which the summarizer then compresses — with
the faithfulness guard ensuring nothing is invented.

Extraction follows a priority ladder, best source first:

  1. **JSON-LD ``articleBody``** — most reputable publishers (Reuters, CNBC,
     Yahoo, Bloomberg, …) embed the full, clean article text in a
     ``<script type="application/ld+json">`` block for SEO. On JS-rendered pages
     the server HTML has little real ``<p>`` prose, so this is often the *only*
     place the body exists — and it's cleaner than scraped paragraphs (no nav,
     ads, or related-links).
  2. **Readable prose**, preferring the ``<article>`` / ``<main>`` region over
     the whole page, so footers and link lists don't leak in.
  3. **A description blurb** (JSON-LD ``description`` or the meta description)
     when the page yielded too little prose (paywall / JS-only).

Dependency-free (stdlib ``html.parser`` + ``json``). Everything is best-effort:
any failure (timeout, paywall, non-HTML) returns "" and the caller falls back
to the blurb.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Iterator, List, Tuple
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

# Containers whose text is boilerplate, not article body. ``script`` is handled
# explicitly (JSON-LD is captured, everything else discarded), so it's not here.
_SKIP_TAGS = {
    "style", "noscript", "nav", "aside", "footer", "header",
    "form", "figure", "figcaption", "button", "svg",
}
# Tags whose text we treat as readable content.
_TEXT_TAGS = {"p", "h1", "h2", "h3", "li"}
# Main-content containers; prose inside these is preferred over the whole page.
_REGION_TAGS = {"article", "main"}


class _ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._text_depth = 0
        self._region_depth = 0
        self._cur_in_region = False
        self._buf: List[str] = []
        self._ld_capture = False
        self._ld_buf: List[str] = []
        self.paragraphs: List[str] = []
        self.region_paragraphs: List[str] = []  # prose inside <article>/<main>
        self.ldjson_blocks: List[str] = []
        self.meta_description = ""

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            # Capture JSON-LD payloads; treat every other script as skipped
            # content (its body is code, not prose).
            if (dict(attrs).get("type") or "").strip().lower() == "application/ld+json":
                self._ld_capture = True
                self._ld_buf = []
            else:
                self._skip_depth += 1
        elif tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _REGION_TAGS:
            self._region_depth += 1
        elif tag in _TEXT_TAGS:
            self._text_depth += 1
            self._cur_in_region = self._region_depth > 0
        elif tag == "meta" and not self.meta_description:
            a = dict(attrs)
            if a.get("property") == "og:description" or a.get("name") == "description":
                self.meta_description = (a.get("content") or "").strip()

    def handle_endtag(self, tag):
        if tag == "script":
            if self._ld_capture:
                self.ldjson_blocks.append("".join(self._ld_buf))
                self._ld_capture = False
                self._ld_buf = []
            elif self._skip_depth:
                self._skip_depth -= 1
        elif tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _REGION_TAGS and self._region_depth:
            self._region_depth -= 1
        elif tag in _TEXT_TAGS and self._text_depth:
            self._text_depth -= 1
            text = "".join(self._buf).strip()
            self._buf.clear()
            if len(text) >= 40:  # skip nav scraps / one-word list items
                cleaned = " ".join(text.split())
                self.paragraphs.append(cleaned)
                if self._cur_in_region:
                    self.region_paragraphs.append(cleaned)

    def handle_data(self, data):
        if self._ld_capture:
            self._ld_buf.append(data)
        elif self._skip_depth == 0 and self._text_depth > 0:
            self._buf.append(data)


def _iter_jsonld_objects(data) -> Iterator[dict]:
    """Walk a parsed JSON-LD payload, yielding every object (handles lists and
    the ``@graph`` container publishers wrap multiple objects in)."""
    if isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, (list, dict)):
            yield from _iter_jsonld_objects(graph)
    elif isinstance(data, list):
        for item in data:
            yield from _iter_jsonld_objects(item)


def _jsonld_fields(blocks: List[str]) -> Tuple[str, str]:
    """Harvest (articleBody, description) from JSON-LD blocks. ``articleBody`` is
    the full SEO-embedded text; ``description`` is a shorter fallback. Returns the
    longest of each found (publishers sometimes emit several blocks)."""
    body, desc = "", ""
    for raw in blocks:
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for obj in _iter_jsonld_objects(data):
            if not isinstance(obj, dict):
                continue
            ab = obj.get("articleBody")
            if isinstance(ab, str) and len(ab.strip()) > len(body):
                body = " ".join(ab.split())
            d = obj.get("description")
            if isinstance(d, str) and len(d.strip()) > len(desc):
                desc = " ".join(d.split())
    return body, desc


def extract_text(html: str, max_chars: int = 4000) -> str:
    """Extract readable article text from raw HTML. Returns "" if too thin."""
    if not html:
        return ""
    parser = _ArticleParser()
    try:
        parser.feed(html)
    except Exception:
        return ""

    # 1. JSON-LD articleBody — the cleanest, most complete text when present.
    ld_body, ld_desc = _jsonld_fields(parser.ldjson_blocks)
    if len(ld_body) >= 80 and not is_boilerplate(ld_body):
        return ld_body[:max_chars].strip()

    # 2. Readable prose — prefer the <article>/<main> region over the whole page.
    paragraphs = parser.region_paragraphs or parser.paragraphs
    body = "\n".join(paragraphs).strip()

    # 3. Thin prose (JS-rendered / paywalled) → fall back to the best blurb.
    if len(body) < 200:
        desc = ld_desc if len(ld_desc) >= len(parser.meta_description) else parser.meta_description
        if len(desc) > len(body):
            body = desc

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
