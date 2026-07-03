#!/usr/bin/env python3
"""
Scrape and download images from a web page.

Designed for pages like https://milelion.com/credit-cards/guide/ — handles
lazy-loaded images (data-src / data-lazy-src / srcset) and collapses
WordPress's resized variants (foo-300x200.jpg, foo-768x512.jpg, foo.jpg) down
to the single largest version.

Only dependency: `requests`  ->  pip install requests

Examples
--------
    # Download every image into ./images/
    python scrape_images.py https://milelion.com/credit-cards/guide/

    # Only images whose URL/alt mentions a card keyword, into ./cards/
    python scrape_images.py https://milelion.com/credit-cards/guide/ \
        --out cards --filter card,credit,amex,visa,mastercard,uob,dbs,citi,ocbc,hsbc,maybank,scb,standard-chartered

    # Skip tiny icons/logos (bytes), be gentle on the server
    python scrape_images.py URL --min-bytes 8000 --delay 0.5
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".svg")
# Attributes that may hold an image URL (covers common lazy-load schemes).
URL_ATTRS = ("src", "data-src", "data-lazy-src", "data-original", "data-lazy")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class ImageCollector(HTMLParser):
    """Pull candidate image URLs from <img>, <source>, and <a href=...>."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, str]] = []  # (url, alt/text)

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k: (v or "") for k, v in attrs_list}
        alt = attrs.get("alt", "") or attrs.get("title", "")

        if tag in ("img", "source"):
            for a in URL_ATTRS:
                if attrs.get(a):
                    self.found.append((attrs[a], alt))
            for srcset_attr in ("srcset", "data-srcset"):
                if attrs.get(srcset_attr):
                    self.found.append((_largest_from_srcset(attrs[srcset_attr]), alt))
        elif tag == "a":
            href = attrs.get("href", "")
            if href.lower().split("?")[0].endswith(IMG_EXT):
                self.found.append((href, alt))


def _largest_from_srcset(srcset: str) -> str:
    """srcset = 'a.jpg 300w, b.jpg 768w' -> pick the largest-width candidate."""
    best_url, best_w = "", -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url = bits[0]
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            w = int(re.sub(r"\D", "", bits[1]) or 0)
        if w >= best_w:
            best_url, best_w = url, w
    return best_url


def _resized_key(url: str) -> str:
    """Collapse WordPress variants: '.../foo-768x512.jpg' -> '.../foo.jpg'."""
    path = urlparse(url).path
    return re.sub(r"-\d+x\d+(?=\.[a-zA-Z]+$)", "", path)


def _variant_width(url: str) -> int:
    m = re.search(r"-(\d+)x\d+\.[a-zA-Z]+$", urlparse(url).path)
    return int(m.group(1)) if m else 10**9  # un-suffixed original sorts largest


def collect(page_url: str, html: str) -> list[tuple[str, str]]:
    parser = ImageCollector()
    parser.feed(html)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw, alt in parser.found:
        if not raw or raw.startswith("data:"):
            continue
        url = urljoin(page_url, raw.strip())
        if not urlparse(url).path.lower().split("?")[0].endswith(IMG_EXT):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append((url, alt))
    return out


def dedupe_resized(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Keep one URL per base image — the largest variant."""
    best: dict[str, tuple[str, str]] = {}
    for url, alt in items:
        key = _resized_key(url)
        if key not in best or _variant_width(url) > _variant_width(best[key][0]):
            best[key] = (url, alt)
    return list(best.values())


def matches_filter(url: str, alt: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    hay = (url + " " + alt).lower()
    return any(k in hay for k in keywords)


def safe_name(url: str, taken: set[str]) -> str:
    name = os.path.basename(urlparse(url).path) or "image"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    base, ext = os.path.splitext(name)
    candidate, n = name, 1
    while candidate in taken:
        candidate = f"{base}_{n}{ext}"
        n += 1
    taken.add(candidate)
    return candidate


def main() -> None:
    p = argparse.ArgumentParser(description="Download images from a web page.")
    p.add_argument("url", help="Page URL to scrape.")
    p.add_argument("--out", "-o", default="images", help="Output directory (default: images).")
    p.add_argument("--filter", default="", help="Comma-separated keywords; keep only matching images.")
    p.add_argument("--min-bytes", type=int, default=3000, help="Skip files smaller than this (icons).")
    p.add_argument("--delay", type=float, default=0.3, help="Seconds between downloads (be polite).")
    p.add_argument("--keep-resized", action="store_true", help="Keep every resized variant, don't collapse.")
    p.add_argument("--list", action="store_true", help="List URLs only; don't download.")
    args = p.parse_args()

    keywords = [k.strip().lower() for k in args.filter.split(",") if k.strip()]

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        r = session.get(args.url, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        sys.exit(f"Failed to fetch page: {e}")

    items = collect(args.url, r.text)
    if not args.keep_resized:
        items = dedupe_resized(items)
    items = [(u, a) for u, a in items if matches_filter(u, a, keywords)]

    print(f"Found {len(items)} candidate image(s)"
          + (f" matching {keywords}" if keywords else "") + ".")
    if args.list:
        for u, a in items:
            print(f"  {u}    [{a[:50]}]")
        return

    os.makedirs(args.out, exist_ok=True)
    taken: set[str] = set()
    saved = skipped = failed = 0

    for url, _alt in items:
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.content
            if len(data) < args.min_bytes:
                skipped += 1
                continue
            fname = safe_name(url, taken)
            with open(os.path.join(args.out, fname), "wb") as fh:
                fh.write(data)
            saved += 1
            print(f"  saved {fname}  ({len(data)//1024} KB)")
        except requests.RequestException as e:
            failed += 1
            print(f"  FAILED {url}: {e}", file=sys.stderr)
        time.sleep(args.delay)

    print(f"\nDone. saved={saved}  skipped_small={skipped}  failed={failed}  -> {args.out}/")


if __name__ == "__main__":
    main()
