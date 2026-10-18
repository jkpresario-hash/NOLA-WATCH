"""Collect public sources and publish Korean excerpts. No browser-side API keys."""
import argparse
import hashlib
import html
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from bs4 import BeautifulSoup
import trafilatura

SOURCES = {
    "Google News · Pelicans": "https://news.google.com/rss/search?q=New+Orleans+Pelicans+when:7d&hl=en-US&gl=US&ceid=US:en",
    "Reddit · NOLAPelicans": "https://www.reddit.com/r/NOLAPelicans/.rss",
}
KEYWORDS = re.compile(r"pelicans|\bnola\b|new orleans|뉴올리언스|펠리컨스", re.I)
SPAM = re.compile(r"livestream|watchlive|free streams?|live free|free reddit|universidad de", re.I)
UA = "NOLAWatch/2.0 (public Pelicans news reader; https://jkpresario-hash.github.io/NOLA-WATCH/)"
CACHE = {}

def set_original(item, source, translated, value):
    if item.get(source) != value:
        item.pop(translated, None)
    item[source] = value

def retain_comment_translations(item, old_comments):
    previous = {(c.get("url"), c.get("body_en")): c.get("body_ko") for c in old_comments}
    for comment in item.get("comments", []):
        translated = previous.get((comment.get("url"), comment.get("body_en")))
        if translated:
            comment["body_ko"] = translated

def error_detail(exc):
    if exc.__cause__ is not None:
        return error_detail(exc.__cause__)
    return f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__

def seed_cache(items):
    for item in items:
        pairs = [(item.get("title"), item.get("title_ko")),
                 (item.get("body_en"), item.get("body_ko"))]
        pairs.extend((c.get("body_en"), c.get("body_ko")) for c in item.get("comments", []))
        pairs.extend(zip(item.get("summary_en", []), item.get("summary_ko", [])))
        for original, translated in pairs:
            if original and translated:
                CACHE[hashlib.sha256(original.encode()).hexdigest()] = translated

def safe_url(value):
    value = html.unescape(value or "")
    parsed = urllib.parse.urlsplit(value)
    return value if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username else ""

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read(4_000_000)

def plain(markup):
    return BeautifulSoup(markup or "", "html.parser").get_text(" ", strip=True)

def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except (ValueError, AttributeError):
        try:
            return parsedate_to_datetime(value).isoformat()
        except (ValueError, TypeError):
            return ""

def parse_feed(data, source):
    doc = ET.fromstring(data)
    atom = "{http://www.w3.org/2005/Atom}"
    result = []
    for node in list(doc.findall(".//item")) + list(doc.findall(atom + "entry")):
        title = node.findtext("title") or node.findtext(atom + "title") or ""
        url = node.findtext("link") or next((x.get("href", "") for x in node.findall(atom + "link") if x.get("rel", "alternate") == "alternate"), "")
        if not safe_url(url) or SPAM.search(unicodedata.normalize("NFKC", title)):
            continue
        if not source.startswith("Reddit") and not KEYWORDS.search(title):
            continue
        content = node.findtext("description") or node.findtext(atom + "content") or ""
        soup = BeautifulSoup(content, "html.parser")
        img = soup.find("img", src=True)
        post_body = soup.select_one(".md")
        excerpt = post_body.get_text("\n", strip=True) if post_body else ""
        if not source.startswith("Reddit"):
            excerpt = plain(content)
            # Google News descriptions usually repeat only linked headlines.
            if soup.find("a") or excerpt.strip() == plain(title).strip():
                excerpt = ""
        result.append(dict(title=title, url=url, source=source,
            kind="reddit" if source.startswith("Reddit") else "news",
            published=timestamp(node.findtext("pubDate") or node.findtext(atom + "published") or node.findtext(atom + "updated")),
            thumbnail=safe_url(img["src"]) if img else "",
            **({"body_en": excerpt} if source.startswith("Reddit") and excerpt else
               {"excerpt_en": excerpt} if excerpt else {})))
    return result

def translate(text):
    if not text.strip():
        return ""
    key = hashlib.sha256(text.encode()).hexdigest()
    if key in CACHE:
        return CACHE[key]
    # Split at paragraph/sentence boundaries, retaining every character.
    chunks, current = [], ""
    for part in re.split(r"(?<=[.!?\n])\s+", text):
        for start in range(0, len(part), 1400):
            piece = part[start:start + 1400]
            if len(current) + len(piece) > 1400:
                chunks.append(current); current = ""
            current += ("\n" if current else "") + piece
    if current:
        chunks.append(current)
    translated = []
    for chunk in chunks:
        query = urllib.parse.urlencode(dict(client="gtx", sl="auto", tl="ko", dt="t", q=chunk))
        last = None
        for attempt in range(3):
            try:
                payload = json.loads(fetch("https://translate.googleapis.com/translate_a/single?" + query))
                value = "".join(x[0] for x in payload[0] if x[0])
                if not value:
                    raise ValueError("empty translation")
                translated.append(value)
                time.sleep(0.15)
                break
            except Exception as exc:
                last = exc
                if attempt < 2:
                    time.sleep(attempt + 1)
        else:
            raise RuntimeError("translation unavailable") from last
    CACHE[key] = "\n".join(translated)
    return CACHE[key]

def summarize(body):
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', body) if len(s.strip()) > 45]
    # Extractive summary: retain original facts and order; translate selected sentences.
    ranked = sorted(enumerate(sentences), key=lambda pair: (bool(KEYWORDS.search(pair[1])), -pair[0]), reverse=True)
    selected = sorted(ranked[:3])
    return [sentence[:650] for _, sentence in selected]

def article(item):
    url = safe_url(item.get('original_url')) or item['url']
    if urllib.parse.urlsplit(url).hostname == "news.google.com":
        from googlenewsdecoder import gnewsdecoder
        decoded = gnewsdecoder(url, interval=1)
        if not decoded.get("status"):
            raise RuntimeError("article URL unavailable")
        url = decoded["decoded_url"]
    raw = fetch(url)
    soup = BeautifulSoup(raw, "html.parser")
    image = soup.find("meta", attrs={"property": "og:image"}) or soup.find("meta", attrs={"name": "twitter:image"})
    if image:
        item["thumbnail"] = safe_url(urllib.parse.urljoin(url, image.get("content", "")))
    item["original_url"] = safe_url(url)
    body = trafilatura.extract(raw, include_comments=False, include_tables=False) or ""
    if len(body) < 120:
        raise RuntimeError("article text unavailable")
    set_original(item, "summary_en", "summary_ko", summarize(body))
    item["summary_source"] = "article"
    if not item["summary_en"]:
        raise RuntimeError("summary unavailable")
    translate_available(item)

def reddit_json(item):
    old_comments = item.get("comments", [])
    url = item["url"].split("?", 1)[0].rstrip("/")
    payload = json.loads(fetch(url + ".json?sort=top&limit=10&raw_json=1"))
    post = payload[0]["data"]["children"][0]["data"]
    set_original(item, "body_en", "body_ko", post.get("selftext", ""))
    images = post.get("preview", {}).get("images", [])
    candidate = images[0]["source"]["url"] if images else post.get("thumbnail", "")
    if not safe_url(candidate) and post.get("media_metadata"):
        candidate = next(iter(post["media_metadata"].values())).get("s", {}).get("u", "")
    item["thumbnail"] = safe_url(candidate) or item.get("thumbnail", "")
    item["comments"] = []
    for node in payload[1]["data"]["children"]:
        data = node.get("data", {})
        body = data.get("body", "")
        if node.get("kind") != "t1" or body in ("", "[deleted]", "[removed]"):
            continue
        item["comments"].append(dict(author=data.get("author", ""), score=data.get("score", 0),
            body_en=body,
            url=safe_url("https://www.reddit.com" + data.get("permalink", ""))))
    item["comment_count"] = post.get("num_comments", 0)
    set_original(item, "summary_en", "summary_ko", summarize(post.get("selftext", "")))
    retain_comment_translations(item, old_comments)
    translate_available(item)

def reddit(item):
    old_comments = item.get("comments", [])
    try:
        reddit_json(item)
        return
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, KeyError):
        pass
    atom = "{http://www.w3.org/2005/Atom}"
    root = ET.fromstring(fetch(re.match(r"https://[^/]+/r/[^/]+/comments/[^/]+", item["url"])[0] + "/.rss"))
    entries = root.findall(atom + "entry")
    post = next((e for e in entries if (e.findtext(atom + "id") or "").startswith("t3_")), None)
    if post is None:
        raise ValueError("Reddit post missing")
    soup = BeautifulSoup(post.findtext(atom + "content") or "", "html.parser")
    body = soup.select_one(".md")
    set_original(item, "body_en", "body_ko", body.get_text("\n", strip=True) if body else "")
    image = soup.find("img", src=True)
    if image:
        item["thumbnail"] = safe_url(image["src"])
    item["comments"] = []
    for entry in entries:
        if not (entry.findtext(atom + "id") or "").startswith("t1_"):
            continue
        body = plain(entry.findtext(atom + "content"))
        if body in ("", "[deleted]", "[removed]"):
            continue
        link = entry.find(atom + "link")
        item["comments"].append(dict(author=(entry.findtext(atom + "author/" + atom + "name") or "").removeprefix("/u/"),
            score=None, body_en=body, url=safe_url(link.get("href", "")) if link is not None else ""))
        if len(item["comments"]) == 10:
            break
    set_original(item, "summary_en", "summary_ko", summarize(item["body_en"]))
    retain_comment_translations(item, old_comments)
    translate_available(item)

def translate_available(item):
    """Translate each available field independently; keep originals on failure."""
    failures = {}
    def field(target, source, dest, label):
        if not target.get(source) or target.get(dest):
            return
        try:
            target[dest] = translate(target[source])
        except Exception as exc:
            failures[label] = error_detail(exc)
    field(item, "title", "title_ko", "title")
    field(item, "body_en", "body_ko", "body")
    for index, comment in enumerate(item.get("comments", [])):
        field(comment, "body_en", "body_ko", f"comment_{index}")
    if not item.get("summary_en") and item.get("excerpt_en"):
        item["summary_en"] = summarize(item["excerpt_en"]) or [item["excerpt_en"][:650]]
        item["summary_source"] = "feed"
    if item.get("summary_en") and not item.get("summary_ko"):
        try:
            item["summary_ko"] = [translate(s) for s in item["summary_en"]]
        except Exception as exc:
            failures["summary"] = error_detail(exc)
    item["translation_errors"] = failures
    return failures

def collect_items(entries, old_items, limit, content_seconds=900):
    previous = {item["url"]: item for item in old_items}
    seen, merged = set(), []
    for entry in entries + old_items:
        if entry["url"] in seen or SPAM.search(unicodedata.normalize("NFKC", entry["title"])):
            continue
        seen.add(entry["url"])
        item = {**entry, **previous.get(entry["url"], {})}
        for field in ("body_en", "excerpt_en", "thumbnail"):
            if not item.get(field) and entry.get(field):
                item[field] = entry[field]
        item.setdefault("kind", "reddit" if item["source"].startswith("Reddit") else "news")
        item.setdefault("status", "pending")
        merged.append(item)
    merged.sort(key=lambda i: i.get("published", ""), reverse=True)
    merged = merged[:200]
    # The content-fetch budget must never prevent headline/available-text translation.
    for item in merged:
        translate_available(item)
    now = datetime.now(timezone.utc)
    def age(value):
        try:
            return (now - datetime.fromisoformat(value)).total_seconds()
        except (TypeError, ValueError):
            return float("inf")
    def needs_update(item):
        if age(item.get("last_attempt_at")) < 21600:
            return False
        return item["status"] != "ready" or (item["kind"] == "reddit" and age(item.get("processed_at")) >= 21600)
    # Never-attempted items precede retries. Oldest attempts get the next turn.
    candidates = sorted((i for i in merged if needs_update(i)),
        key=lambda i: (i.get("status") == "ready", i.get("last_attempt_at", "")))
    processed = 0
    deadline = time.monotonic() + content_seconds
    for item in candidates[:max(0, limit)]:
        if time.monotonic() >= deadline:
            break
        processed += 1
        item["last_attempt_at"] = now.isoformat()
        try:
            if item["kind"] == "reddit":
                reddit(item)
            else:
                article(item)
            item.pop("content_error", None)
            item["processed_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            item["content_error"] = error_detail(exc)
            print(item["source"], item["content_error"], flush=True)
        failures = item.get("translation_errors", {})
        if item.get("content_error") or failures:
            item["status"] = "partial"
            item["error"] = ("일부 번역이 지연되고 있습니다. 다음 수집 때 재시도합니다." if failures else
                "원문 본문·댓글을 가져오지 못했습니다. 수집 가능한 내용은 번역했으며 다음 수집 때 재시도합니다.")
        else:
            item["status"] = "ready"
            item.pop("error", None)
    return merged, processed

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    path = Path("data.json")
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}
    cache_path = Path(".translation-cache.json")
    if cache_path.exists():
        CACHE.update(json.loads(cache_path.read_text(encoding="utf-8")))
    seed_cache(old["items"])
    entries, errors = [], []
    for name, url in SOURCES.items():
        try:
            entries.extend(parse_feed(fetch(url), name))
        except Exception as exc:
            errors.append(name + ": 수집에 실패했습니다 (" + type(exc).__name__ + ")")
    # Interleave sources so Reddit is processed even during the initial news backlog.
    news = [i for i in entries if i["kind"] == "news"]
    community = [i for i in entries if i["kind"] == "reddit"]
    entries = [entry for pair in __import__('itertools').zip_longest(news, community) for entry in pair if entry]
    merged, processed = collect_items(entries, old["items"], args.limit)
    state = dict(updated=datetime.now(timezone.utc).isoformat(), errors=errors, items=merged[:200])
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    cache_path.write_text(json.dumps(CACHE, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(state['items'])} entries; processed {processed}")

if __name__ == "__main__":
    main()

