"""Independent YouTube and X collectors. Credentials never enter public data."""
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG = json.loads(Path(__file__).with_name("social_sources.json").read_text(encoding="utf-8"))
X_START = datetime.fromisoformat(CONFIG["x"]["start_date"]).replace(tzinfo=ZoneInfo(CONFIG["timezone"])).astimezone(timezone.utc)
ACCOUNTS = {name.casefold() for name in CONFIG["x"]["accounts"]}
TOPIC = re.compile(r"pelicans|\bnola\b|펠리컨[스즈]|뉴올리언[스즈]", re.I)
ADS = re.compile(r"subscribe|sponsor|promo code|prizepicks|fanduel|draftkings|patreon|merch|copyright|welcome back|locked on podcast network|구독|좋아요|광고|협찬", re.I)

def utc(value):
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.replace(tzinfo=date.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None

def iso(date):
    return date.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def safe_url(value):
    try:
        parsed = urllib.parse.urlsplit(html.unescape(value or ""))
        return urllib.parse.urlunsplit(parsed) if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username else ""
    except ValueError:
        return ""

def error_code(exc):
    return f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__

def get_json(url, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": "NOLAWatch/3.0", **(headers or {})})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read(5_000_000))

class QuietLogger:
    def debug(self, message):
        pass
    def warning(self, message):
        pass
    def error(self, message):
        pass

def youtube_options():
    return dict(quiet=True, no_warnings=True, logger=QuietLogger(), skip_download=True,
                socket_timeout=15, retries=1, extractor_retries=1, noplaylist=True,
                ignore_no_formats_error=True)

def youtube_search_url(query):
    # Explicit search URLs work with current yt-dlp; ytsearchdate was removed.
    # YouTube removed upload-date sorting in 2026; keep the last-week/video filter.
    return "https://www.youtube.com/results?" + urllib.parse.urlencode(dict(search_query=query, sp="EgQIAxAB"))

def youtube_search(query, now):
    """Prefer the official API when configured; public search needs no API key."""
    key = os.environ.get("YOUTUBE_API_KEY", "")
    limit = CONFIG["youtube"]["results_per_query"]
    if key:
        params = dict(part="snippet", type="video", order="date", q=query,
                      maxResults=limit, publishedAfter=iso(now - timedelta(days=CONFIG["youtube"]["lookback_days"])), key=key)
        data = get_json("https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params))
        entries = []
        for entry in data.get("items", []):
            snippet = entry.get("snippet", {})
            video_id = entry.get("id", {}).get("videoId", "")
            entries.append(dict(id=video_id, title=html.unescape(snippet.get("title", "")),
                channel=snippet.get("channelTitle", ""), channel_id=snippet.get("channelId", ""),
                published=snippet.get("publishedAt", ""), description=html.unescape(snippet.get("description", "")),
                thumbnail=snippet.get("thumbnails", {}).get("high", {}).get("url", "")))
        return entries
    import yt_dlp
    with yt_dlp.YoutubeDL({**youtube_options(), "extract_flat": True, "playlistend": limit}) as client:
        data = client.extract_info(youtube_search_url(query), download=False)
    entries = []
    for entry in (data or {}).get("entries", []):
        if not entry:
            continue
        stamp = entry.get("timestamp") or entry.get("release_timestamp")
        published = iso(datetime.fromtimestamp(stamp, timezone.utc)) if stamp else ""
        entries.append(dict(id=entry.get("id", ""), title=entry.get("title", ""),
            channel=entry.get("channel") or entry.get("uploader", ""), channel_id=entry.get("channel_id", ""),
            published=published, description=entry.get("description", ""), thumbnail=entry.get("thumbnail", "")))
    return entries

def youtube_details(video_id):
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if key:
        params = urllib.parse.urlencode(dict(part="snippet", id=video_id, key=key))
        items = get_json("https://www.googleapis.com/youtube/v3/videos?" + params).get("items", [])
        return items[0].get("snippet", {}) if items else {}
    import yt_dlp
    with yt_dlp.YoutubeDL(youtube_options()) as client:
        data = client.extract_info("https://www.youtube.com/watch?v=" + video_id, download=False) or {}
    result = dict(description=data.get("description", ""), channelTitle=data.get("channel", ""))
    if data.get("upload_date"):
        # The source supplies a date without a time; preserve that precision.
        result["upload_date"] = datetime.strptime(data["upload_date"], "%Y%m%d").date().isoformat()
    if data.get("timestamp"):
        result["publishedAt"] = iso(datetime.fromtimestamp(data["timestamp"], timezone.utc))
    return result

def youtube_transcript(video_id):
    import requests
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import NoTranscriptFound
    class TimedSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs.setdefault("timeout", (5, 15))
            return super().request(*args, **kwargs)
    client = YouTubeTranscriptApi(http_client=TimedSession())
    try:
        fetched = client.fetch(video_id, languages=["ko", "en"])
    except NoTranscriptFound:
        tracks = client.list(video_id)
        fetched = next(iter(tracks)).fetch()
    return fetched.to_raw_data()

def summary_points(text, minimum=3):
    """Select three distinct source passages; never pad with metadata or guesses."""
    text = html.unescape(text or "")
    text = re.sub(r"\[(?:music|applause|laughter)\]", " ", text, flags=re.I)
    candidates = []
    for line in re.split(r"(?<=[.!?。！？])\s+|\n+", text):
        line = re.sub(r"https?://\S+|#\S+", "", line).strip(" -•\t")
        # Automatic captions often lack punctuation. Split into source passages.
        words = line.split()
        parts = [" ".join(words[index:index + 65]) for index in range(0, len(words), 65)]
        for part in parts:
            if ADS.search(part):
                continue
            if len(part) < (20 if re.search(r"[가-힣]", part) else 45):
                continue
            if any(SequenceMatcher(None, part.casefold(), old.casefold()).ratio() > .78 for old in candidates):
                continue
            candidates.append(part[:650])
    if len(candidates) < minimum:
        return []
    # Cover the beginning, middle and end, prioritizing concrete basketball content.
    selected = []
    for bucket in range(minimum):
        group = list(enumerate(candidates))[bucket * len(candidates) // minimum:(bucket + 1) * len(candidates) // minimum]
        selected.append(max(group, key=lambda pair:(bool(TOPIC.search(pair[1])), bool(re.search(r"\d|zion|murphy|fears|defen[cs]e|offen[cs]e|자이언|수비|공격", pair[1], re.I)), len(pair[1]))))
    return [text for _, text in sorted(selected)]

def enrich_video(item, translate):
    try:
        item["title_ko"] = item.get("title_ko") or translate(item["title"])
    except Exception:
        pass  # A title failure must not block the content summary.
    original = item.get("summary_original", [])
    if len(original) < 3:
        transcript_error = None
        try:
            segments = youtube_transcript(item["video_id"])
            original = summary_points(" ".join(segment["text"] for segment in segments))
            if original:
                item["summary_source"] = "transcript"
        except Exception as exc:
            transcript_error = error_code(exc)
        if not original:
            details = youtube_details(item["video_id"])
            item["channel"] = details.get("channelTitle") or item.get("channel", "")
            if details.get("publishedAt"):
                item["published"] = details["publishedAt"]
            if details.get("upload_date"):
                item["upload_date"] = details["upload_date"]
            original = summary_points(details.get("description", ""))
            if original:
                item["summary_source"] = "description"
        if len(original) < 3:
            raise ValueError("not enough verified video content" if not transcript_error else "video content unavailable")
        item["summary_original"] = original
    translated = [translate(point) for point in original]
    if len(set(translated)) < 3 or not all(re.search(r"[가-힣]", value) for value in translated):
        raise ValueError("incomplete video translation")
    item["summary_ko"] = translated
    item["status"] = "ready"
    item.pop("error", None)
    item.pop("summary_error", None)

def collect_youtube(old, translate, now, deadline):
    previous = {item["video_id"]: item for item in old if item.get("kind") == "youtube" and item.get("video_id")}
    entries, failures = {}, []
    for query in CONFIG["youtube"]["queries"]:
        try:
            for entry in youtube_search(query, now):
                video_id = entry.get("id", "")
                if not re.fullmatch(r"[a-zA-Z0-9_-]{11}", video_id):
                    continue
                if not TOPIC.search(entry.get("title", "") + " " + entry.get("description", "")):
                    continue
                stamp = utc(entry.get("published"))
                if stamp and stamp < now - timedelta(days=CONFIG["youtube"]["lookback_days"]):
                    continue
                item = dict(kind="youtube", source="YouTube", video_id=video_id,
                    title=entry.get("title", ""), url="https://www.youtube.com/watch?v=" + video_id,
                    channel=entry.get("channel", ""), thumbnail=safe_url(entry.get("thumbnail")) or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    published=entry.get("published", ""), discovered_at=iso(now), status="pending")
                entries[video_id] = {**item, **previous.get(video_id, {}), **entries.get(video_id, {})}
        except Exception as exc:
            failures.append(error_code(exc))
    merged = {**previous, **entries}
    videos = sorted(merged.values(), key=lambda item:item.get("published") or item.get("discovered_at", ""), reverse=True)[:200]
    for item in videos:
        if item.get("status") == "ready" and len(item.get("summary_ko", [])) >= 3:
            continue
        attempted = utc(item.get("summary_attempted_at"))
        if attempted and (now - attempted).total_seconds() < 21600:
            continue
        if time.monotonic() >= deadline:
            break
        item["summary_attempted_at"] = iso(now)
        try:
            enrich_video(item, translate)
        except Exception as exc:
            item["status"] = "partial"
            item["error"] = "자막·영상 설명을 확인해 한국어 요약 3개 이상을 준비하고 있습니다."
            item["summary_error"] = error_code(exc)
    status = dict(state="ready" if not failures else "partial", checked_at=iso(now),
        count=len(videos), summarized=sum(item.get("status") == "ready" for item in videos))
    if failures:
        status["message"] = "YouTube 검색이 지연되고 있습니다. 기존 영상을 유지하며 다음 수집 때 재시도합니다."
        status["error_codes"] = failures
    else:
        status["message"] = "매일 09:00·18:00에 펠리컨스 영상을 검색합니다. 자막 또는 영상 설명에서 핵심 내용 3개 이상을 한국어로 제공합니다."
    return videos, status

def eligible_x(item):
    date = utc(item.get("published"))
    return item.get("kind") == "x" and item.get("author", "").casefold() in ACCOUNTS and date is not None and date >= X_START

def x_get(path, params, token):
    return get_json("https://api.x.com/2/" + path + "?" + urllib.parse.urlencode(params), {"Authorization": "Bearer " + token})

def collect_x(old, checkpoint, translate, now, deadline, token=None, max_pages=10):
    posts = {str(item["post_id"]): dict(item) for item in old if eligible_x(item) and item.get("post_id")}
    token = token if token is not None else (os.environ.get("X_BEARER_TOKEN") or os.environ.get("X_API_BEARER_TOKEN") or os.environ.get("TWITTER_BEARER_TOKEN", ""))
    if not token:
        return list(posts.values()), dict(state="needs_connection", checked_at=iso(now), count=len(posts),
            message="X 수집 연결을 기다리고 있습니다. 연결되면 지정된 10개 계정의 10월 19일 이후 게시글을 표시합니다.")
    failures = []
    for handle in CONFIG["x"]["accounts"]:
        if time.monotonic() >= deadline:
            failures.append("collection time budget")
            break
        account = checkpoint.setdefault(handle, {})
        try:
            if not account.get("user_id"):
                resolved = x_get("users/by/username/" + urllib.parse.quote(handle), {}, token).get("data", {})
                if resolved.get("username", "").casefold() != handle.casefold() or not str(resolved.get("id", "")).isdigit():
                    raise ValueError("account lookup mismatch")
                account["user_id"] = str(resolved["id"])
            pending = account.get("pending_token")
            since = account.get("pending_since_id") if pending else account.get("since_id")
            newest = account.get("pending_newest_id", "0") if pending else account.get("since_id", "0")
            for page in range(max_pages):
                params = dict(max_results=100, start_time=iso(X_START),
                    **{"tweet.fields":"created_at,author_id,note_tweet,attachments,referenced_tweets", "expansions":"attachments.media_keys", "media.fields":"url,preview_image_url,type"})
                if since:
                    params["since_id"] = since
                if pending:
                    params["pagination_token"] = pending
                payload = x_get("users/" + account["user_id"] + "/tweets", params, token)
                if payload.get("errors") and not payload.get("data"):
                    raise ValueError("timeline response unavailable")
                media = {entry["media_key"]: entry for entry in payload.get("includes", {}).get("media", [])}
                for entry in payload.get("data", []):
                    post_id = str(entry.get("id", ""))
                    published = utc(entry.get("created_at"))
                    if not post_id.isdigit() or str(entry.get("author_id")) != account["user_id"]:
                        continue
                    newest = str(max(int(newest or "0"), int(post_id)))
                    if published is None or published < X_START:
                        continue
                    text = entry.get("note_tweet", {}).get("text") or entry.get("text", "")
                    if not text.strip():
                        continue
                    item = {**posts.get(post_id, {}), "kind":"x", "source":"X · @" + handle,
                        "author":handle, "post_id":post_id, "title":text[:180], "body_en":text,
                        "published":iso(published), "url":f"https://x.com/{handle}/status/{post_id}"}
                    if posts.get(post_id, {}).get("body_en") != text:
                        item.pop("body_ko", None)
                        item.pop("title_ko", None)
                        item["status"] = "pending"
                    media_keys = entry.get("attachments", {}).get("media_keys", [])
                    if media_keys:
                        attachment = media.get(media_keys[0], {})
                        item["thumbnail"] = safe_url(attachment.get("url") or attachment.get("preview_image_url"))
                    posts[post_id] = item
                pending = payload.get("meta", {}).get("next_token")
                if pending:
                    account.update(pending_token=pending, pending_since_id=since, pending_newest_id=newest)
                    if time.monotonic() >= deadline:
                        break
                else:
                    if int(newest or "0"):
                        account["since_id"] = newest
                    for key in ("pending_token", "pending_since_id", "pending_newest_id"):
                        account.pop(key, None)
                    break
        except Exception as exc:
            failures.append(handle + ": " + error_code(exc))
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 429):
                break
    for item in posts.values():
        if item.get("body_ko") or time.monotonic() >= deadline:
            continue
        try:
            item["body_ko"] = translate(item["body_en"])
            if not item["body_ko"].strip():
                raise ValueError("empty post translation")
            item["title_ko"] = item["body_ko"][:180]
            item["status"] = "ready"
            item.pop("error", None)
        except Exception:
            item["status"] = "partial"
            item["error"] = "한국어 번역을 준비하고 있습니다. 다음 수집 때 재시도합니다."
    pending_accounts = [handle for handle, state in checkpoint.items() if state.get("pending_token")]
    status = dict(state="partial" if failures or pending_accounts else "ready", checked_at=iso(now), count=len(posts),
        message="지정된 10개 계정의 한국시간 10월 19일 이후 게시글을 수집·번역합니다.")
    if failures:
        status.update(message="일부 X 계정을 확인하지 못했습니다. 기존 게시글을 유지하며 다음 수집 때 재시도합니다.", error_codes=failures)
    if pending_accounts:
        status["pending_accounts"] = pending_accounts
    return sorted(posts.values(), key=lambda item:item["published"], reverse=True), status

def collect(old, old_state, translate, seconds=300):
    state = dict(old_state or {})
    now = datetime.now(timezone.utc)
    deadline = time.monotonic() + seconds
    x_state = state.setdefault("x", {})
    x_posts, x_status = collect_x(old, x_state, translate, now, min(deadline, time.monotonic() + 90))
    videos, youtube_status = collect_youtube(old, translate, now, deadline)
    return x_posts + videos, state, {"x":x_status, "youtube":youtube_status}
