"""Publisher allowlist and English-first cross-language news admission."""
import json
import re
import unicodedata
import urllib.parse
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

CONFIG = json.loads(Path(__file__).with_name("news_sources.json").read_text(encoding="utf-8"))
SOURCES = CONFIG["sources"]
LANGUAGES = ("en", "ko", "ja", "zh")
GOOGLE_HOST = "news.google.com"
TOPICS = {
    "en": '("New Orleans Pelicans" OR Pelicans OR "Zion Williamson" OR "Trey Murphy")',
    "ko": '(펠리컨스 OR 펠리컨즈 OR 뉴올리언스 OR 뉴올리언즈 OR 자이언)',
    "ja": '(ペリカンズ OR ニューオーリンズ OR ザイオン)',
    "zh": '(鹈鹕 OR 鵜鶘 OR 新奥尔良 OR 錫安 OR 锡安)',
}

def host_matches(host, domain):
    return host == domain or host.endswith("." + domain)

def source_for_url(url, publisher=False):
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in ("http", "https") or parsed.username:
            return None
        path = urllib.parse.unquote(parsed.path).lower()
        query = urllib.parse.parse_qs(parsed.query)
    except ValueError:
        return None
    for source in SOURCES:
        if not any(host_matches(host, domain) for domain in source["hosts"]):
            continue
        if source.get("publisher_id"):
            oid = source["publisher_id"]
            if oid not in query.get("oid", []) and not re.search(r"/article/" + oid + r"(?:/|$)", path):
                continue
        if not publisher:
            if source.get("paths") and not (source.get("legacy_host") and host_matches(host, source["legacy_host"])):
                if not any(path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/") for prefix in source["paths"]):
                    continue
            if source.get("path_contains") and source["path_contains"] not in path:
                continue
            if source.get("board"):
                if source["board"] not in query.get("id", []) and not path.startswith("/board/" + source["board"] + "/"):
                    continue
        return source
    return None

def allowed_news_url(url):
    return source_for_url(url) is not None

def source_for_item(item):
    # Resolved addresses are authoritative. Never rescue a forbidden destination
    # with a publisher name or an old cached policy tag.
    url = item.get("original_url") or item.get("url", "")
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return None
    if host != GOOGLE_HOST:
        return source_for_url(url)
    publisher_url = item.get("publisher_url", "")
    if publisher_url:
        return source_for_url(publisher_url, publisher=True)
    publisher = item.get("publisher") or item.get("title", "").rsplit(" - ", 1)[-1]
    publisher = publisher.casefold().strip()
    for source in SOURCES:
        if publisher in {name.casefold() for name in source.get("aliases", [])}:
            return source
    return None

def search_feeds():
    """Every query contains explicit site scopes, including the English query."""
    settings = {"en": ("en-US", "US", "US:en"), "ko": ("ko", "KR", "KR:ko"),
                "ja": ("ja", "JP", "JP:ja"), "zh": ("zh-CN", "CN", "CN:zh-Hans")}
    for language in LANGUAGES:
        scopes = list(dict.fromkeys(scope for source in SOURCES if source["language"] == language for scope in source["search"]))
        for offset in range(0, len(scopes), 6):
            sites = " OR ".join("site:" + scope for scope in scopes[offset:offset + 6])
            query = f"{TOPICS[language]} ({sites}) when:7d"
            hl, gl, ceid = settings[language]
            url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(dict(q=query, hl=hl, gl=gl, ceid=ceid))
            yield language, url

def strip_publisher(title):
    suffix = title.rsplit(" - ", 1)
    if len(suffix) == 2:
        names = {alias.casefold() for source in SOURCES for alias in [source["name"]] + source.get("aliases", [])}
        if suffix[1].casefold().strip() in names:
            return suffix[0]
    return title

STOP = set("a an the and or of to for in on with from at by as is are was were be has have had its their this that new orleans pelicans pelican nba basketball team news says said say report reports reporting reportedly vs versus about after before first latest update updates".split())
SYNONYMS = {word: event for event, words in {
    "sign": "sign signs signed signing agrees agree agreement",
    "waive": "waive waives waived waiving release releases released cut cuts",
    "trade": "trade trades traded trading deal deals swap swaps",
    "injury": "injury injuries injured hurt sidelined absence misses missing",
    "return": "return returns returned returning comeback back",
    "result": "win wins won victory beat beats defeat defeats defeated loss lose loses lost",
    "coach": "coach coaches coaching hire hires hired",
}.items() for word in words.split()}
EVENTS = set(SYNONYMS.values())

def tokens(text):
    words = re.findall(r"[a-z]+|\d+(?:\.\d+)?", unicodedata.normalize("NFKC", text).casefold())
    return {SYNONYMS.get(word, word) for word in words if word not in STOP}

def comparable_dates(left, right):
    try:
        return abs((datetime.fromisoformat(left["published"]) - datetime.fromisoformat(right["published"])).total_seconds()) <= 72 * 3600
    except (KeyError, ValueError, TypeError):
        return True

def same_story(left, right):
    if not comparable_dates(left, right):
        return False
    a, b = tokens(left["dedupe_text_en"]), tokens(right["dedupe_text_en"])
    if not a or not b:
        return False
    independent = {"exclusive", "interview", "analysis", "scouting", "opinion", "breakdown"}
    if left.get("language") != "en" and (a & independent) - b:
        return False
    # Different scores/contract amounts distinguish different facts. Missing
    # numbers in a short translated headline are not themselves a new fact.
    numbers_a, numbers_b = {x for x in a if x[0].isdigit()}, {x for x in b if x[0].isdigit()}
    if numbers_a and numbers_b and numbers_a != numbers_b:
        return False
    actions_a, actions_b = a & EVENTS, b & EVENTS
    if actions_a and actions_b and not actions_a & actions_b:
        return False
    f1 = 2 * len(a & b) / (len(a) + len(b))
    if len(a & b) >= 3 and f1 >= .80:
        return True
    if actions_a & actions_b:
        anchors_a, anchors_b = a - EVENTS, b - EVENTS
        common = anchors_a & anchors_b
        # A short translated headline may omit English qualifiers. Require two
        # matching identifying words and no new subject in the shorter headline.
        if len(common) >= 2 and common == min((anchors_a, anchors_b), key=len):
            return True
    return len(a & b) >= 3 and SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio() >= .9

def canonical_url(url):
    parsed = urllib.parse.urlsplit(url)
    tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
    query = urllib.parse.urlencode(sorted((key, value) for key, value in urllib.parse.parse_qsl(parsed.query) if key not in tracking))
    return urllib.parse.urlunsplit(("https", parsed.netloc.lower().removeprefix("www."), parsed.path.rstrip("/"), query, ""))

def curate(items, to_english, english_complete=True):
    """Keep English first; publish other languages only after dedupe succeeds."""
    community, news, deferred = [], [], []
    counts = Counter()
    seen_urls = set()
    for original in items:
        item = dict(original)
        if item.get("kind") == "reddit" or item.get("source", "").startswith("Reddit"):
            community.append(item)
            continue
        source = source_for_item(item)
        if not source or item.get("policy_rejected"):
            counts["outside_allowlist"] += 1
            continue
        item.update(source_id=source["id"], language=source["language"], source=source["name"])
        key = canonical_url(item.get("original_url") or item["url"])
        if key in seen_urls:
            counts["duplicate_url"] += 1
            continue
        seen_urls.add(key)
        news.append(item)
    news.sort(key=lambda item: item.get("published", ""), reverse=True)
    news.sort(key=lambda item: LANGUAGES.index(item["language"]))
    accepted = []
    community_baseline = [{**item, "dedupe_text_en": strip_publisher(item["title"])} for item in community]
    for item in news:
        title = strip_publisher(item["title"])
        if item["language"] != "en" and not english_complete:
            item["defer_reason"] = "영어 소스 수집이 완료되지 않아 중복 확인을 보류했습니다."
            deferred.append(item)
            continue
        try:
            if item.get("dedupe_original_title") != title or not item.get("dedupe_text_en"):
                item["dedupe_text_en"] = title if item["language"] == "en" else to_english(title)
                if not item["dedupe_text_en"] or (item["language"] != "en" and not re.search(r"[a-zA-Z]{2}", item["dedupe_text_en"])):
                    raise ValueError("English comparison unavailable")
                item["dedupe_original_title"] = title
        except Exception:
            item["defer_reason"] = "언어 간 중복 비교를 완료하지 못해 다음 수집 때 재시도합니다."
            deferred.append(item)
            continue
        baseline = accepted + (community_baseline if item["language"] != "en" else [])
        if any(same_story(item, prior) for prior in baseline):
            counts["duplicate_story"] += 1
            continue
        item.pop("defer_reason", None)
        if len(accepted) < 200:
            accepted.append(item)
            counts["kept_" + item["language"]] += 1
    counts["deferred"] = len(deferred)
    return community + accepted, deferred, dict(counts)
