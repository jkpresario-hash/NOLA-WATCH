import json, re, html, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone

SOURCES = {
    "Google News · Pelicans": "https://news.google.com/rss/search?q=New+Orleans+Pelicans&hl=en-US&gl=US&ceid=US:en",
    "Reddit · NOLAPelicans": "https://www.reddit.com/r/NOLAPelicans/.rss",
}
KEYWORDS = re.compile(r"pelicans|\bnola\b|new orleans|뉴올리언스|뉴올|펠리컨스", re.I)
def parse_feed(data, source):
    doc = ET.fromstring(data)
    result = []
    atom = "{http://www.w3.org/2005/Atom}"
    for item in list(doc.findall(".//item")) + list(doc.findall(atom+"entry")):
        title = item.findtext("title") or item.findtext(atom+"title") or ""
        url = item.findtext("link") or ""
        if not url:
            for link in item.findall(atom+"link"):
                if link.get("rel", "alternate") == "alternate":
                    url = link.get("href", ""); break
        if urllib.parse.urlparse(url).scheme not in ("http", "https"):
            continue
        if source.startswith("Reddit") or KEYWORDS.search(title):
            result.append({"title": title, "url": url, "source": source})
    return result

def main():
    path = Path("data.json")
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}
    entries, errors = [], []
    for name, url in SOURCES.items():
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "NOLAWatch/1.0 RSS reader"})
            with urllib.request.urlopen(request, timeout=30) as response:
                entries.extend(parse_feed(response.read(4_000_000), name))
        except Exception as exc:
            errors.append(name + ": " + str(exc))
    seen, merged = set(), []
    for item in entries + old["items"]:
        if item["url"] not in seen:
            seen.add(item["url"]); merged.append(item)
    state = {"updated": datetime.now(timezone.utc).isoformat(), "errors": errors, "items": merged[:200]}
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    esc = html.escape
    cards = "".join('<article><small>'+esc(i["source"])+ '</small><h2><a rel="noopener noreferrer" target="_blank" href="'+esc(i["url"], quote=True)+'">'+esc(i["title"])+ '</a></h2></article>' for i in state["items"])
    message = " / ".join(errors) if errors else "수집 완료"
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>NOLA WATCH</title><style>body{background:#071b30;color:#eee;max-width:850px;margin:40px auto;padding:20px;font:16px system-ui}h1{color:#d5b46f}article{padding:18px 0;border-bottom:1px solid #435367}a{color:#fff}small{color:#d5b46f}h2{font-size:20px}</style><h1>NOLA WATCH</h1><p>뉴올리언스 펠리컨스 소식 · 한국시간 09:00 / 18:00 수집 예약</p><p>마지막 실행 (UTC): '+esc(state["updated"])+'</p><p>'+esc(message)+'</p>'+ (cards or '<p>아직 수집된 글이 없습니다.</p>')+'</html>'
    Path("index.html").write_text(page, encoding="utf-8")
    for error in errors: print(error)
if __name__ == "__main__": main()
