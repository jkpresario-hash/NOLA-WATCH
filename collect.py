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
        result.append(dict(title=title, url=url, source=source,
            kind="reddit" if source.startswith("Reddit") else "news",
            published=timestamp(node.findtext("pubDate") or node.findtext(atom + "published") or node.findtext(atom + "updated")),
            thumbnail=safe_url(img["src"]) if img else ""))
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
        query = urllib.parse.urlencode(dict(client="gtx", sl="en", tl="ko", dt="t", q=chunk))
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
    url = item['url']
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
    item["summary_ko"] = [translate(s) for s in summarize(body)]
    if not item["summary_ko"]:
        raise RuntimeError("summary unavailable")

def reddit_json(item):
    url = item["url"].split("?", 1)[0].rstrip("/")
    payload = json.loads(fetch(url + ".json?sort=top&limit=10&raw_json=1"))
    post = payload[0]["data"]["children"][0]["data"]
    item["body_ko"] = translate(post.get("selftext", ""))
    item["body_en"] = post.get("selftext", "")
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
            body_ko=translate(body), body_en=body,
            url=safe_url("https://www.reddit.com" + data.get("permalink", ""))))
    item["comment_count"] = post.get("num_comments", 0)
    item["summary_ko"] = [translate(s) for s in summarize(post.get("selftext", ""))]

def reddit(item):
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
    item["body_en"] = body.get_text("\n", strip=True) if body else ""
    item["body_ko"] = translate(item["body_en"])
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
            score=None, body_en=body, body_ko=translate(body), url=safe_url(link.get("href", "")) if link is not None else ""))
        if len(item["comments"]) == 10:
            break
    item["summary_ko"] = [translate(s) for s in summarize(item["body_en"])]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    path = Path("data.json")
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}
    cache_path = Path(".translation-cache.json")
    if cache_path.exists():
        CACHE.update(json.loads(cache_path.read_text(encoding="utf-8")))
    previous = {i["url"]: i for i in old["items"]}
    entries, errors = [], []
    for name, url in SOURCES.items():
        try:
            entries.extend(parse_feed(fetch(url), name))
        except Exception as exc:
            errors.append(name + ": 수집에 실패했습니다 (" + type(exc).__name__ + ")")
    seen, merged, processed = set(), [], 0
    # Interleave sources so Reddit is processed even during the initial news backlog.
    news = [i for i in entries if i["kind"] == "news"]
    community = [i for i in entries if i["kind"] == "reddit"]
    entries = [entry for pair in __import__('itertools').zip_longest(news, community) for entry in pair if entry]
    for entry in entries + old["items"]:
        if entry["url"] in seen or SPAM.search(unicodedata.normalize("NFKC", entry["title"])):
            continue
        seen.add(entry["url"])
        item = {**entry, **previous.get(entry["url"], {})}
        item.setdefault("kind", "reddit" if item["source"].startswith("Reddit") else "news")
        item.setdefault("status", "pending")
        fresh = item.get("processed_at") and (datetime.now(timezone.utc) - datetime.fromisoformat(item["processed_at"])).total_seconds() < 21600
        needs_update = item["status"] != "ready" or (item["kind"] == "reddit" and not fresh)
        if needs_update and processed < args.limit:
            processed += 1
            try:
                item["title_ko"] = translate(item["title"])
                if item["kind"] == "reddit":
                    reddit(item)
                else:
                    article(item)
                item["status"] = "ready"
                item.pop("error", None)
                item["processed_at"] = datetime.now(timezone.utc).isoformat()
            except Exception as exc:
                item["status"] = "partial"
                item["error"] = "본문·댓글 수집 또는 번역을 완료하지 못했습니다. 원문을 확인해 주세요."
                print(item["source"], type(exc).__name__, flush=True)
        merged.append(item)
    merged.sort(key=lambda i: i.get("published", ""), reverse=True)
    state = dict(updated=datetime.now(timezone.utc).isoformat(), errors=errors, items=merged[:200])
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    cache_path.write_text(json.dumps(CACHE, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(state['items'])} entries; processed {processed}")

if __name__ == "__main__":
    main()
