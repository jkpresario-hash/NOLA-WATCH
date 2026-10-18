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
import news_policy

SOURCES = {
    "Reddit · NOLAPelicans": "https://www.reddit.com/r/NOLAPelicans/new/.rss?limit=100",
}
KEYWORDS = re.compile(r"pelicans|\bnola\b|new orleans|뉴올리언[스즈]|펠리컨[스즈]|ペリカンズ|ニューオーリンズ|鹈鹕|鵜鶘|新奥尔良|자이언|ザイオン|锡安|錫安|zion williamson|trey murphy|herb jones|jeremiah fears|dejounte murray", re.I)
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

class NewsRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not news_policy.allowed_news_url(newurl):
            raise ValueError("redirect outside news allowlist")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch(url, news=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if news and not news_policy.allowed_news_url(url):
        raise ValueError("article outside news allowlist")
    opener = urllib.request.build_opener(NewsRedirectHandler()) if news else None
    request = opener.open if opener else urllib.request.urlopen
    with request(req, timeout=20) as response:
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

def is_reddit(item):
    return item.get("kind") == "reddit" or item.get("source", "").startswith("Reddit")

def item_key(item):
    if is_reddit(item):
        match = re.search(r"/comments/([a-z0-9]+)(?:/|$)", urllib.parse.urlsplit(item["url"]).path, re.I)
        if match:
            return "t3_" + match[1].lower()
    return item["url"]

def collect_reddit(old_items, errors, max_pages=10, checkpoint=None):
    """Collect the new listing and resume pages interrupted on earlier runs."""
    if checkpoint is None:
        checkpoint = {}
    source = "Reddit · NOLAPelicans"
    base = SOURCES[source]
    known = {item_key(i) for i in old_items if is_reddit(i)}
    pending = [None] + list(dict.fromkeys(checkpoint.get("pending_after", [])))
    collected, seen, requested = [], set(), set()
    pages = 0
    while pending and pages < max_pages:
        cursor = pending.pop(0)
        if cursor in requested:
            continue
        requested.add(cursor)
        url = base + ("&" + urllib.parse.urlencode({"after": cursor}) if cursor else "")
        if pages:
            time.sleep(1)
        pages += 1
        try:
            page = parse_feed(fetch(url), source)
        except Exception as exc:
            if cursor:
                pending.insert(0, cursor)
            errors.append(f"Reddit: 최신 글 목록 수집이 중단됐습니다 ({error_detail(exc)}). 다음 수집 때 이어서 재시도합니다.")
            break
        if not page:
            continue
        overlap = False
        added = 0
        for item in page:
            key = item_key(item)
            overlap = overlap or key in known
            if key not in seen:
                seen.add(key)
                collected.append(item)
                added += 1
        # Retain every post on the overlap page, then resume any unfinished scan.
        if overlap:
            continue
        next_cursor = item_key(page[-1])
        if not added or not next_cursor.startswith("t3_") or next_cursor in requested:
            errors.append("Reddit: 다음 페이지를 확인하지 못했습니다. 과거 글 수집이 완전하지 않을 수 있습니다.")
            continue
        if next_cursor not in pending:
            pending.insert(0, next_cursor)
    checkpoint["pending_after"] = [cursor for cursor in pending if cursor]
    if pending and pages >= max_pages:
        errors.append("Reddit: 이번 수집의 페이지 한도에 도달했습니다. 남은 페이지는 다음 수집 때 이어서 확인합니다.")
    return collected

def parse_feed(data, source, language="en"):
    doc = ET.fromstring(data)
    atom = "{http://www.w3.org/2005/Atom}"
    result = []
    for node in list(doc.findall(".//item")) + list(doc.findall(atom + "entry")):
        title = node.findtext("title") or node.findtext(atom + "title") or ""
        url = node.findtext("link") or next((x.get("href", "") for x in node.findall(atom + "link") if x.get("rel", "alternate") == "alternate"), "")
        if not safe_url(url):
            continue
        if not source.startswith("Reddit") and (SPAM.search(unicodedata.normalize("NFKC", title)) or not KEYWORDS.search(title)):
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
        publisher = node.find("source")
        item = dict(title=title, url=url, source=source,
            kind="reddit" if source.startswith("Reddit") else "news",
            published=timestamp(node.findtext("pubDate") or node.findtext(atom + "published") or node.findtext(atom + "updated")),
            thumbnail=safe_url(img["src"]) if img else "",
            **({"body_en": excerpt} if source.startswith("Reddit") and excerpt else
               {"excerpt_en": excerpt} if excerpt else {}))
        if not source.startswith("Reddit"):
            item["language"] = language
            if publisher is not None:
                item["publisher"] = publisher.text or ""
                item["publisher_url"] = publisher.get("url", "")
        result.append(item)
    return result

def translate(text, target="ko"):
    if not text.strip():
        return ""
    key = ("" if target == "ko" else target + ":") + hashlib.sha256(text.encode()).hexdigest()
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
        query = urllib.parse.urlencode(dict(client="gtx", sl="auto", tl=target, dt="t", q=chunk))
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
    if not news_policy.allowed_news_url(url):
        item["policy_rejected"] = True
        raise ValueError("resolved article outside news allowlist")
    try:
        raw = fetch(url, news=True)
    except ValueError:
        item["policy_rejected"] = True
        raise
    soup = BeautifulSoup(raw, "html.parser")
    source = news_policy.source_for_url(url)
    if source and source.get("required_context"):
        item["context_attempted_at"] = datetime.now(timezone.utc).isoformat()
        if source["id"] == "hupu":
            board = re.search(r"发表在\s*\S+\s+https?://bbs\.hupu\.com/([a-zA-Z0-9]+)", soup.get_text(" ", strip=True))
            if board:
                item["source_context"] = board[1]
        elif source["id"] == "tieba" and soup.title and "鹈鹕吧" in soup.title.get_text():
            item["source_context"] = "鹈鹕吧"
        if item.get("source_context") and item["source_context"] != source["required_context"]:
            item["policy_rejected"] = True
            raise ValueError("article outside selected forum board")
        if not item.get("source_context"):
            raise RuntimeError("forum board verification unavailable")
    image = soup.find("meta", attrs={"property": "og:image"}) or soup.find("meta", attrs={"name": "twitter:image"})
    if image:
        item["thumbnail"] = safe_url(urllib.parse.urljoin(url, image.get("content", "")))
    item["original_url"] = safe_url(url)
    body = trafilatura.extract(raw, include_comments=False, include_tables=False) or ""
    if len(body) < 120:
        raise RuntimeError("article text unavailable")
    if item.get("language") == "en":
        item["event_facts_en"] = news_policy.event_facts(body)
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

def collect_items(entries, old_items, limit, content_seconds=900, news_filter=None):
    previous = {item_key(item): item for item in old_items}
    seen, merged = set(), []
    for entry in entries + old_items:
        key = item_key(entry)
        if key in seen or (not is_reddit(entry) and SPAM.search(unicodedata.normalize("NFKC", entry["title"]))):
            continue
        seen.add(key)
        item = {**entry, **previous.get(key, {})}
        for field in ("body_en", "excerpt_en", "thumbnail"):
            if not item.get(field) and entry.get(field):
                item[field] = entry[field]
        item.setdefault("kind", "reddit" if item["source"].startswith("Reddit") else "news")
        item.setdefault("status", "pending")
        merged.append(item)
    merged.sort(key=lambda i: i.get("published", ""), reverse=True)
    if news_filter:
        merged = news_filter(merged)
    # Community posts must not age out when more news articles arrive.
    news_count, retained = 0, []
    for item in merged:
        if is_reddit(item):
            retained.append(item)
        elif news_count < 200:
            retained.append(item)
            news_count += 1
    merged = retained
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
        source = news_policy.source_for_item(item) if not is_reddit(item) else None
        if source and source.get("required_context") and not item.get("context_attempted_at"):
            return True
        if item.get("language") == "en" and item["status"] == "ready" and "event_facts_en" not in item:
            return True
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
    english_complete = True
    for language, url in news_policy.search_feeds():
        try:
            entries.extend(parse_feed(fetch(url), "Curated news", language=language))
        except Exception as exc:
            if language == "en":
                english_complete = False
            errors.append(f"허용 소스 {language} 검색 수집 실패 ({error_detail(exc)})")
    reddit_scan = old.get("reddit_scan", {})
    for name, url in SOURCES.items():
        if name.startswith("Reddit"):
            entries.extend(collect_reddit(old["items"], errors, checkpoint=reddit_scan))
            continue
        try:
            entries.extend(parse_feed(fetch(url), name))
        except Exception as exc:
            errors.append(name + ": 수집에 실패했습니다 (" + type(exc).__name__ + ")")
    # Interleave sources so Reddit is processed even during the initial news backlog.
    news = [i for i in entries if i["kind"] == "news"]
    community = [i for i in entries if i["kind"] == "reddit"]
    entries = [entry for pair in __import__('itertools').zip_longest(news, community) for entry in pair if entry]
    policy_result = {}
    def curate(items):
        selected, deferred, stats = news_policy.curate(items, lambda text: translate(text, target="en"), english_complete, now=datetime.now(timezone.utc))
        policy_result.update(deferred=deferred, stats=stats)
        return selected
    previous = old["items"] + old.get("deferred_news", [])
    merged, processed = collect_items(entries, previous, args.limit, news_filter=curate)
    merged, final_deferred, final_stats = news_policy.curate(merged, lambda text: translate(text, target="en"), english_complete,
        now=datetime.now(timezone.utc), require_context=True)
    policy_result["deferred"].extend(final_deferred)
    for key, count in final_stats.items():
        if not key.startswith("kept_") and key != "deferred":
            policy_result["stats"][key] = policy_result["stats"].get(key, 0) + count
    policy_result["stats"]["deferred"] = len(policy_result["deferred"])
    for language in news_policy.LANGUAGES:
        policy_result["stats"]["kept_" + language] = sum(item.get("kind") != "reddit" and item.get("language") == language for item in merged)
    if policy_result["deferred"]:
        errors.append(f"비영어 뉴스 {len(policy_result['deferred'])}건은 영어와의 중복 확인이 끝날 때까지 보류합니다.")
    state = dict(updated=datetime.now(timezone.utc).isoformat(), errors=errors, items=merged, reddit_scan=reddit_scan,
                 deferred_news=policy_result["deferred"], news_policy=dict(version=news_policy.CONFIG["version"],
                 english_complete=english_complete, **policy_result["stats"]))
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    cache_path.write_text(json.dumps(CACHE, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(state['items'])} entries; processed {processed}")

if __name__ == "__main__":
    main()

