"""Read selected publishers directly; publish only dated, body-verified articles."""
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

CONFIG = json.loads(Path(__file__).with_name('article_sources.json').read_text(encoding='utf-8'))
SOURCES = {source['id']: source for source in CONFIG['sources']}
TEAM = re.compile(r'\bpelicans\b|\bnew orleans\b|펠리컨[스즈]|뉴올리언[스즈]', re.I)
PLAYERS = re.compile('|'.join(re.escape(name) + r'(?:\s+III)?\b' for name in CONFIG['players']), re.I)
PROMO = re.compile(r'subscribe|sign up|share this|thanks for reading|sponsored|promo code|affiliate|follow me|all rights reserved|cookie policy', re.I)
PAYWALL = re.compile(r'this post is for paid subscribers|subscribe to (?:continue|keep) reading|to keep reading.{0,80}subscribe', re.I)
UA = 'Mozilla/5.0 (compatible; NOLAWatch/4.0; public basketball analysis reader)'

def utc(value):
    if not value:
        return None
    try:
        date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None
    return date.replace(tzinfo=date.tzinfo or timezone.utc).astimezone(timezone.utc)

def iso(date):
    return date.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

def source_url(url, source, article=False):
    try:
        p = urllib.parse.urlsplit(html.unescape(url or ''))
        if p.scheme != 'https' or p.hostname not in source['hosts'] or p.username or p.password or p.port not in (None, 443):
            return ''
        path = urllib.parse.unquote(p.path)
        if '..' in path.split('/') or '\\' in path:
            return ''
        if article:
            prefix = source.get('article_prefix', '\0')
            slug = path[len(prefix):].strip('/') if path.startswith(prefix) else ''
            if not (slug and '/' not in slug or re.fullmatch(source.get('article_pattern', r'(?!)'), path)):
                return ''
        # Fold www aliases and tracking parameters for stable article identity.
        host = urllib.parse.urlsplit(source['home']).netloc if article else p.netloc
        return urllib.parse.urlunsplit(('https', host, p.path.rstrip('/') if article else p.path, '' if article else p.query, ''))
    except (ValueError, TypeError):
        return ''

def public_image(url):
    try:
        p = urllib.parse.urlsplit(url or '')
        return url if p.scheme in ('http', 'https') and p.hostname and not p.username and not p.password else ''
    except ValueError:
        return ''

def error_code(exc):
    return f'HTTP {exc.code}' if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__

class PublisherRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, source):
        self.source = source
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not source_url(newurl, self.source):
            raise ValueError('redirect outside selected publisher')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch(url, source):
    if not source_url(url, source):
        raise ValueError('URL outside selected publisher')
    req = urllib.request.Request(url, headers={'User-Agent':UA, 'Accept':'application/rss+xml, application/atom+xml, application/xml, text/html, application/json, */*;q=0.5'})
    with urllib.request.build_opener(PublisherRedirect(source)).open(req, timeout=15) as response:
        return response.read(6_000_000)

class LinkList(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.feeds, self.next_pages = [], [], []
        self.href, self.parts = None, []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.href, self.parts = attrs.get('href'), []
        if tag == 'link' and 'alternate' in attrs.get('rel', '') and ('xml' in attrs.get('type', '') or 'rss' in attrs.get('type', '')):
            self.feeds.append(attrs.get('href', ''))
        if tag in ('link', 'a') and 'next' in attrs.get('rel', '').split():
            self.next_pages.append(attrs.get('href', ''))
    def handle_data(self, data):
        if self.href:
            self.parts.append(data)
    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            title = ' '.join(self.parts).strip()
            self.links.append((self.href, title))
            if re.fullmatch(r'(?:older(?: posts)?|next(?: page)?|next\s*[»→]?|load more)', title, re.I):
                self.next_pages.append(self.href)
            self.href = None

def parse_feed(raw, source):
    root = ET.fromstring(raw)
    items = []
    atom = '{http://www.w3.org/2005/Atom}'
    for node in root.findall('.//item') + root.findall(atom + 'entry'):
        link = node.findtext('link') or next((a.get('href') for a in node.findall(atom+'link') if a.get('rel','alternate') == 'alternate'), '')
        url = source_url(link, source, article=True)
        if not url:
            continue
        encoded = node.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or node.findtext(atom+'content') or ''
        items.append(dict(url=url, title=html.unescape(node.findtext('title') or node.findtext(atom+'title') or ''),
            published=node.findtext('pubDate') or node.findtext(atom+'published') or '', feed_body=encoded,
            author=node.findtext('{http://purl.org/dc/elements/1.1/}creator') or node.findtext(atom+'author/'+atom+'name') or '',
            source_id=source['id']))
    return items

def parse_listing(raw, url, source):
    markup = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else raw
    parser = LinkList(); parser.feed(markup)
    items = []
    for href, title in parser.links:
        link = source_url(urllib.parse.urljoin(url, href), source, article=True)
        if link:
            items.append(dict(url=link, title=title, source_id=source['id']))
    # Public JSON-LD/hydration may carry links absent from rendered anchors.
    for match in re.finditer(r'"(?:canonical_url|url)"\s*:\s*"([^"<>]+)"', markup):
        link = source_url(urllib.parse.urljoin(url, html.unescape(match[1].replace('\\/', '/'))), source, article=True)
        if link:
            items.append(dict(url=link, source_id=source['id']))
    feeds = [source_url(urllib.parse.urljoin(url, href), source) for href in parser.feeds]
    pages = [source_url(urllib.parse.urljoin(url, href), source) for href in parser.next_pages]
    return items, list(dict.fromkeys(v for v in feeds if v)), list(dict.fromkeys(v for v in pages if v))

def parse_archive(raw, source):
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError('invalid public article list')
    items = []
    for post in payload:
        url = post.get('canonical_url') or post.get('link') or urllib.parse.urljoin(source['home'], 'p/'+post.get('slug',''))
        url = source_url(url, source, article=True)
        if not url:
            continue
        title = post.get('title', '')
        if isinstance(title, dict):
            title = title.get('rendered', '')
        items.append(dict(url=url, title=html.unescape(title or ''),
            published=post.get('post_date') or post.get('date_gmt') or post.get('date') or '', source_id=source['id']))
    return items

def discover(source, old_state, now, deadline):
    state = dict(old_state or {})
    found, errors, used_feeds, pages = [], [], set(), []
    succeeded = 0
    for listing in source.get('listings', [source['home']]):
        if time.monotonic() >= deadline:
            break
        try:
            raw = fetch(listing, source)
            entries, feeds, following = parse_listing(raw, listing, source)
            found.extend(entries); pages.extend(following); used_feeds.update(feeds); succeeded += 1
        except Exception as exc:
            errors.append('list: '+error_code(exc))
    for url in list(dict.fromkeys(source.get('feeds', []) + sorted(used_feeds))):
        if time.monotonic() >= deadline:
            break
        try:
            found.extend(parse_feed(fetch(url, source), source)); succeeded += 1
            break
        except Exception as exc:
            errors.append('rss: '+error_code(exc))
    cutoff = now - timedelta(days=CONFIG['retention_days'])
    if source.get('archive'):
        offset = int(state.get('archive_offset', 0))
        resume = offset
        offsets = list(dict.fromkeys([0, offset]))
        # The newest page is always checked; an unfinished historical scan resumes.
        for value in offsets:
            if time.monotonic() >= deadline:
                break
            if source['archive'] == 'substack':
                params = urllib.parse.urlencode(dict(sort='new', limit=50, offset=value))
                url = urllib.parse.urljoin(source['home'], 'api/v1/archive') + '?' + params
            else:
                params = urllib.parse.urlencode(dict(after=iso(cutoff), per_page=100, page=value+1, _fields='link,date,date_gmt,title'))
                url = urllib.parse.urljoin(source['home'], 'wp-json/wp/v2/posts') + '?' + params
            try:
                entries = parse_archive(fetch(url, source), source); succeeded += 1; found.extend(entries)
                dates = [utc(item.get('published')) for item in entries]
                dates = [date for date in dates if date]
                more = len(entries) >= (50 if source['archive']=='substack' else 100) and not (dates and min(dates) <= cutoff)
                if value == resume or not more:
                    resume = value + (50 if source['archive']=='substack' else 1) if more else 0
                    state['archive_offset'] = resume
                    state['backfill_pending'] = more
                    if more and len(offsets)<4 and resume not in offsets:
                        offsets.append(resume)
                if not more:
                    break
            except Exception as exc:
                errors.append('archive: '+error_code(exc)); state['backfill_pending'] = True
                break
    elif source.get('sitemap') and time.monotonic() < deadline:
        try:
            root = ET.fromstring(fetch(source['sitemap'], source)); succeeded += 1
            nested = []
            for node in root:
                loc = next((child.text for child in node if child.tag.endswith('loc')), '')
                link = source_url(loc, source, article=True)
                if link:
                    found.append(dict(url=link, source_id=source['id']))
                elif node.tag.endswith('sitemap') and source_url(loc, source):
                    nested.append(loc)
            pages.extend(nested[:2])
        except Exception as exc:
            errors.append('sitemap: '+error_code(exc))
    next_page = state.get('listing_next')
    if next_page:
        pages.insert(0, next_page)
    for url in list(dict.fromkeys(pages))[:2]:
        if time.monotonic() >= deadline or not source_url(url, source):
            break
        try:
            raw = fetch(url, source)
            if b'<urlset' in raw[:1000]:
                root = ET.fromstring(raw)
                for loc in root.iter():
                    if loc.tag.endswith('loc') and source_url(loc.text, source, article=True):
                        found.append(dict(url=source_url(loc.text, source, article=True), source_id=source['id']))
                state.pop('listing_next', None)
            else:
                entries, _, following = parse_listing(raw, url, source); found.extend(entries)
                state['listing_next'] = following[0] if following else ''
            succeeded += 1
        except Exception as exc:
            errors.append('next: '+error_code(exc))
    for url in source.get('seed_urls', []):
        found.append(dict(url=url, source_id=source['id']))
    state.update(checked_at=iso(now), discovered=len({item['url'] for item in found}), errors=errors,
                 state='ready' if succeeded else 'partial')
    return found, state

def document(raw, candidate):
    from bs4 import BeautifulSoup
    import trafilatura
    soup = BeautifulSoup(raw, 'html.parser')
    def meta(name):
        node = soup.find('meta', attrs={'property':name}) or soup.find('meta', attrs={'name':name})
        return node.get('content','') if node else ''
    published = meta('article:published_time') or meta('datePublished')
    author = meta('author') or candidate.get('author','')
    preview = False
    for script in soup.find_all('script',type='application/ld+json'):
        try:
            value = json.loads(script.string or script.get_text())
            objects = value if isinstance(value,list) else value.get('@graph',[value])
            for entry in objects:
                if 'Article' in str(entry.get('@type','')) or entry.get('@type')=='BlogPosting':
                    published = published or entry.get('datePublished','')
                    preview = preview or entry.get('isAccessibleForFree') in (False,'False','false')
                    by = entry.get('author',{})
                    if isinstance(by,dict): author = author or by.get('name','')
        except (ValueError, TypeError, AttributeError):
            continue
    stamp = soup.find('time', attrs={'datetime':True})
    published = published or (stamp.get('datetime','') if stamp else '') or candidate.get('published','')
    heading = soup.find('h1')
    title = meta('og:title') or (heading.get_text(' ',strip=True) if heading else '') or candidate.get('title','')
    image = public_image(meta('og:image'))
    scope = soup.select_one('.body.markup') or soup.select_one('[itemprop="articleBody"]') or soup.find('article') or soup.find('main') or soup
    preview = preview or bool(PAYWALL.search(scope.get_text(' ',strip=True)))
    for node in scope.select('script,style,noscript,nav,footer,header,aside,form,button,.comments,.related-posts,.recommended-posts,.subscribe-widget,.subscription-widget,.post-footer,.share-dialog'):
        node.decompose()
    body = trafilatura.extract(str(scope), include_comments=False, include_tables=True) or scope.get_text('\n',strip=True)
    paragraphs = [p.strip() for p in body.splitlines() if p.strip() and not PROMO.search(p)]
    return dict(title=html.unescape(title), published=published, author=author, thumbnail=image,
                body='\n'.join(paragraphs)[:160000], access='preview' if preview else 'public')

def related_passages(body, title=''):
    if len(body) < CONFIG['minimum_body_chars']:
        return []
    paragraphs = [p.strip() for p in body.splitlines() if len(p.strip()) >= 80 and not PROMO.search(p)]
    relevant = [p for p in paragraphs if TEAM.search(p) or PLAYERS.search(p)]
    if not relevant or (sum(map(len,relevant)) < 240 and not (TEAM.search(title) or PLAYERS.search(title))):
        return []
    return relevant

def summarize(passages):
    candidates = []
    for paragraph in passages:
        for sentence in re.split(r'(?<=[.!?])\s+', paragraph):
            sentence = sentence.strip()
            if len(sentence) < 55 or PROMO.search(sentence):
                continue
            if any(SequenceMatcher(None,sentence.casefold(),old.casefold()).ratio() > .8 for old in candidates):
                continue
            candidates.append(sentence[:650])
    ranked = sorted(enumerate(candidates), key=lambda pair:(bool(TEAM.search(pair[1]) or PLAYERS.search(pair[1])), bool(re.search(r'\d|defen[cs]e|offen[cs]e|rotation|contract|shoot|rebound',pair[1],re.I)), -pair[0]), reverse=True)
    return [point for _,point in sorted(ranked[:CONFIG['summary_points']])]

def translate_item(item, translate):
    try:
        item['title_ko'] = item.get('title_ko') or translate(item['title'])
        if not item.get('summary_ko'):
            item['summary_ko'] = [translate(point) for point in item['summary_original']]
        if not item['title_ko'] or not all(item['summary_ko']):
            raise ValueError('incomplete translation')
        item['status'] = 'ready'; item.pop('error',None)
    except Exception as exc:
        item['status'] = 'partial'
        item['error'] = '본문 관련성은 확인했습니다. 한국어 번역은 다음 수집에서 재시도합니다.'
        item['translation_error'] = error_code(exc)

def collect(old, old_state, translate, seconds=360, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now-timedelta(days=CONFIG['retention_days'])
    deadline = time.monotonic()+seconds
    previous, candidates = {}, {}
    state = dict(old_state or {}); source_states = dict(state.get('sources',{}))
    checks = dict(state.get('checks',{}))
    for item in old:
        source = SOURCES.get(item.get('source_id'))
        stamp = utc(item.get('published'))
        if item.get('kind')=='article' and source and source_url(item.get('url'),source,article=True) and stamp and cutoff<=stamp<=now+timedelta(days=1) and item.get('relevance_verified'):
            previous[source_url(item['url'],source,article=True)] = dict(item)
    for item in state.get('queue',[]):
        source = SOURCES.get(item.get('source_id'))
        stamp = utc(item.get('published'))
        discovered = utc(item.get('discovered_at'))
        if source and source_url(item.get('url'),source,article=True) and (not stamp or cutoff<=stamp<=now+timedelta(days=1)) and (not discovered or discovered>=cutoff):
            candidates[source_url(item['url'],source,article=True)] = item
    # Different publishers are independent; all five get a discovery turn each run.
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {source_id:pool.submit(discover,source,source_states.get(source_id,{}),now,min(deadline,time.monotonic()+90)) for source_id,source in SOURCES.items()}
        for source_id,future in futures.items():
            try:
                entries, source_states[source_id] = future.result()
                for item in entries:
                    stamp = utc(item.get('published'))
                    if stamp and (stamp<cutoff or stamp>now+timedelta(days=1)):
                        continue
                    key = source_url(item.get('url'),SOURCES[source_id],article=True)
                    if key:
                        candidates[key] = {**candidates.get(key,{}), **{k:v for k,v in item.items() if v}}
                        candidates[key]['url'] = key
                        candidates[key].setdefault('discovered_at',iso(now))
            except Exception as exc:
                source_states[source_id] = {**source_states.get(source_id,{}), 'state':'partial', 'errors':[error_code(exc)],'checked_at':iso(now)}
    queue, translated_urls = {}, set()
    def priority(item):
        title = item.get('title') or ''
        body = item.get('feed_body','')
        return (not bool(TEAM.search(title) or PLAYERS.search(title)),item.get('source_id')!='intheno',not bool(TEAM.search(body) or PLAYERS.search(body)),item.get('published',''))
    ordered = sorted(candidates.values(),key=priority)
    for index,candidate in enumerate(ordered):
        url = candidate['url']; source = SOURCES[candidate['source_id']]
        if url in previous:
            continue
        checkpoint = checks.get(url,{})
        attempted = utc(checkpoint.get('checked_at'))
        cooldown = 7*86400 if checkpoint.get('result') in ('unrelated','old') else 21600
        if attempted and (now-attempted).total_seconds()<cooldown:
            if checkpoint.get('result') not in ('unrelated','old'):
                queue[url] = {k:v for k,v in candidate.items() if k!='feed_body'}
            continue
        if time.monotonic()>=deadline:
            queue.update({item['url']:{k:v for k,v in item.items() if k!='feed_body'} for item in ordered[index:] if item['url'] not in previous})
            break
        result = 'pending'
        try:
            raw = candidate.get('feed_body')
            doc = document(raw,candidate) if raw else None
            if not doc or len(doc['body'])<CONFIG['minimum_body_chars'] or doc['access']=='preview':
                doc = document(fetch(url,source),candidate)
            stamp = utc(doc.get('published'))
            if not stamp:
                result = 'undated'
            elif not cutoff<=stamp<=now+timedelta(days=1):
                result = 'old'
            else:
                passages = related_passages(doc['body'],doc['title'])
                points = summarize(passages)
                if not points:
                    result = 'preview_insufficient' if doc['access']=='preview' else 'unrelated'
                else:
                    item = dict(kind='article',source_id=source['id'],source=source['name'],url=url,
                        title=doc['title'] or candidate.get('title') or '펠리컨스 분석',author=doc['author'],published=iso(stamp),
                        thumbnail=doc['thumbnail'],summary_original=points,summary_source='article_body',access=doc['access'],
                        relevance_verified=True,relevance_checked_at=iso(now),body_characters=len(doc['body']),status='pending')
                    translate_item(item,translate); translated_urls.add(url); previous[url]=item; result='included'
        except Exception as exc:
            result='fetch_error'; candidate['error_code']=error_code(exc)
        checks[url] = dict(result=result,checked_at=iso(now),source_id=source['id'])
        if result not in ('included','unrelated','old'):
            queue[url] = {k:v for k,v in candidate.items() if k!='feed_body'}
    for item in previous.values():
        if item['url'] not in translated_urls and item.get('status')!='ready' and time.monotonic()<deadline:
            translate_item(item,translate)
    checks = {url:entry for url,entry in checks.items() if (utc(entry.get('checked_at')) or cutoff)>=cutoff}
    state = dict(sources=source_states,checks=checks,queue=list(queue.values()))
    delayed = [SOURCES[s]['name'] for s,status in source_states.items() if status.get('state')!='ready']
    backfill = [SOURCES[s]['name'] for s,status in source_states.items() if status.get('backfill_pending') or status.get('listing_next')]
    message = '선택한 5개 소스의 최근 90일 글 중 본문에서 펠리컨스 관련 내용을 확인한 아티클입니다.'
    if delayed:
        message += ' 수집 지연: '+', '.join(delayed)+'.'
    if queue:
        message += f' 본문·발행일 확인 대기 {len(queue)}건은 다음 수집에서 이어서 확인합니다.'
    if backfill:
        message += ' 90일 글 목록 추가 확인 중: '+', '.join(backfill)+'.'
    status = dict(state='partial' if delayed or queue or backfill else 'ready',checked_at=iso(now),count=len(previous),
        pending=len(queue),retention_days=CONFIG['retention_days'],message=message,
        sources=[dict(id=s,name=SOURCES[s]['name'],count=sum(item['source_id']==s for item in previous.values()),**meta) for s,meta in source_states.items()])
    print('Articles:',len(previous),'included;',len(queue),'pending;',[(s,v.get('state'),v.get('discovered'),v.get('errors')) for s,v in source_states.items()],flush=True)
    return sorted(previous.values(),key=lambda item:item['published'],reverse=True),state,status
