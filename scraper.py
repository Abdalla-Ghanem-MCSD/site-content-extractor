"""
scraper.py
-----------
Same-domain website crawler + content extractor.

Walks every internal page (BFS) starting from a URL, strips boilerplate
(nav / header / footer / scripts / menus), and returns clean ordered content
(headings, paragraphs, list items) for each page.

Designed to be driven by app.py, which streams progress to the browser,
but it also works standalone:

    from scraper import crawl
    pages = crawl("https://example.com", max_pages=100)
"""

import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 SiteContentExtractor/1.0"
    )
}

# File extensions we never want to crawl as "pages"
SKIP_EXT = re.compile(
    r"\.(jpg|jpeg|png|gif|webp|svg|ico|css|js|json|xml|pdf|zip|rar|7z|"
    r"mp4|mp3|avi|mov|woff2?|ttf|eot|doc|docx|xls|xlsx|ppt|pptx)$",
    re.I,
)

# Containers that are almost always boilerplate – removed before extraction
BOILERPLATE_TAGS = ["script", "style", "noscript", "svg", "iframe",
                    "header", "footer", "nav", "form"]

# class / id substrings that usually mark boilerplate
BOILERPLATE_HINT = re.compile(
    r"(nav|menu|header|footer|cookie|breadcrumb|sidebar|social|"
    r"newsletter|subscribe|search|drawer|offcanvas)",
    re.I,
)


def _normalize_domain(netloc: str) -> str:
    """example.com and www.example.com are treated as the same site."""
    return netloc.lower().lstrip().replace("www.", "", 1)


def _clean_url(base: str, href: str) -> str | None:
    """Resolve a link to an absolute, fragment-free URL (or None to skip)."""
    if not href:
        return None
    href = href.strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return None
    absolute = urljoin(base, href)
    absolute, _ = urldefrag(absolute)          # drop #fragment
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return None
    if SKIP_EXT.search(parsed.path):
        return None
    return absolute.rstrip("/") or absolute


def _extract_meta(soup: BeautifulSoup) -> tuple[str, str]:
    """Return (title, meta_description) using OG tags when available."""
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    desc = ""
    for sel in [("meta", {"name": "description"}),
                ("meta", {"property": "og:description"})]:
        tag = soup.find(*sel)
        if tag and tag.get("content"):
            desc = tag["content"].strip()
            break
    return title, desc


def _extract_blocks(soup: BeautifulSoup) -> list[dict]:
    """Return ordered content blocks after stripping boilerplate."""
    # 1) remove obvious boilerplate tags
    for tag in soup(BOILERPLATE_TAGS):
        tag.decompose()

    # 2) remove elements whose class/id looks like navigation / chrome
    for el in soup.find_all(attrs={"class": BOILERPLATE_HINT}):
        el.decompose()
    for el in soup.find_all(attrs={"id": BOILERPLATE_HINT}):
        el.decompose()

    body = soup.body or soup
    blocks: list[dict] = []
    seen: set[str] = set()

    for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
        text = el.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        if len(text) < 2:
            continue
        key = text.lower()
        if key in seen:                      # drop repeated nav / duplicates
            continue
        seen.add(key)

        name = el.name
        if name[0] == "h" and name[1:].isdigit():
            blocks.append({"type": "heading", "level": int(name[1]), "text": text})
        elif name == "li":
            blocks.append({"type": "list", "text": text})
        else:
            blocks.append({"type": "para", "text": text})
    return blocks


def crawl(start_url, max_pages=100, delay=0.3, same_domain=True,
          on_event=None, should_stop=None):
    """
    Crawl a site and return a list of page dicts:
        {url, title, meta_description, blocks: [...]}

    on_event(dict)   -> optional callback for live progress (used by the UI)
    should_stop()    -> optional callable returning True to abort early
    """
    def emit(payload):
        if on_event:
            on_event(payload)

    start_url = _clean_url(start_url, start_url) or start_url
    root_domain = _normalize_domain(urlparse(start_url).netloc)

    queue = deque([start_url])
    visited = set([start_url])
    pages = []
    errors = 0

    emit({"type": "log", "msg": f"Starting at {start_url}"})

    while queue and len(pages) < max_pages:
        if should_stop and should_stop():
            emit({"type": "log", "msg": "Stopped by user."})
            break

        url = queue.popleft()
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            ctype = resp.headers.get("Content-Type", "")
            if resp.status_code != 200 or "text/html" not in ctype:
                errors += 1
                emit({"type": "log", "msg": f"Skip ({resp.status_code}) {url}"})
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            # collect links BEFORE we strip the DOM for extraction
            links = soup.find_all("a", href=True)

            # a second soup for clean extraction (decompose mutates the tree)
            content_soup = BeautifulSoup(resp.text, "lxml")
            title, desc = _extract_meta(content_soup)
            blocks = _extract_blocks(content_soup)

            page = {"url": url, "title": title or url,
                    "meta_description": desc, "blocks": blocks}
            pages.append(page)

            emit({"type": "page", "url": url,
                  "title": page["title"], "blocks": len(blocks),
                  "index": len(pages)})

            # enqueue new internal links
            for a in links:
                clean = _clean_url(url, a["href"])
                if not clean or clean in visited:
                    continue
                if same_domain and _normalize_domain(urlparse(clean).netloc) != root_domain:
                    continue
                visited.add(clean)
                queue.append(clean)

            emit({"type": "progress",
                  "crawled": len(pages),
                  "queued": len(queue),
                  "errors": errors})

        except requests.RequestException as exc:
            errors += 1
            emit({"type": "log", "msg": f"Error {url}: {exc}"})

        if delay:
            time.sleep(delay)

    emit({"type": "log", "msg": f"Finished. {len(pages)} pages, {errors} errors."})
    return pages
