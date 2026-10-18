'use strict';

const INITIAL_TAB = 'news';
const TABS = ['news', 'reddit', 'x', 'youtube'];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function safeURL(value) {
  try {
    const u = new URL(value);
    return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password ? u.href : '';
  } catch { return ''; }
}
function link(url, label, cls = 'source-link') {
  const safe = safeURL(url);
  return safe ? `<a class="${cls}" href="${escapeHTML(safe)}" target="_blank" rel="noopener noreferrer">${label}</a>` : '';
}
function date(value) {
  if (!value) return '';
  const d = new Date(value);
  return isNaN(d) ? '' : new Intl.DateTimeFormat('ko-KR', {timeZone:'Asia/Seoul', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}).format(d);
}
function kindOf(item) {
  return item.kind || (String(item.source || '').startsWith('Reddit') ? 'reddit' : 'news');
}
function filterItems(items, tab, query = '') {
  if (!TABS.includes(tab)) return [];
  const q = query.toLocaleLowerCase().trim();
  return items.filter(i => kindOf(i) === tab && [i.title, i.title_ko, i.channel, i.author, ...(i.summary_ko || []), i.body_ko].join(' ').toLocaleLowerCase().includes(q));
}
function card(i) {
  const kind = kindOf(i), reddit = kind === 'reddit', video = kind === 'youtube', x = kind === 'x';
  const img = safeURL(i.thumbnail), comments = i.comments || [];
  const summaries = Array.isArray(i.summary_ko) ? i.summary_ko : [];
  const validVideoSummary = summaries.length >= 3 && new Set(summaries).size >= 3 && summaries.every(s => /[가-힣]/.test(s));
  const displayedSummary = video && !validVideoSummary ? [] : summaries;
  const published = date(i.published) || (i.upload_date ? `${escapeHTML(i.upload_date)} 업로드` : (video ? '업로드 시각 확인 중' : ''));
  const badge = {news:'NEWS', reddit:'REDDIT', x:'X.COM', youtube:'YOUTUBE'}[kind] || 'NEWS';
  const title = link(i.original_url || i.url, escapeHTML(i.title_ko || i.title), 'headline');
  const source = video ? i.channel || 'YouTube' : x ? `@${i.author || ''}` : i.source;
  let content = '';
  if (x) {
    content = i.body_ko ? `<p class="body">${escapeHTML(i.body_ko)}</p>` : '<p class="notice">한국어 번역을 준비하고 있습니다.</p>';
    content += `<details class="original"><summary>원문 게시글 보기</summary><p class="body">${escapeHTML(i.body_en || i.title)}</p></details>`;
  } else {
    content += `<h2>${title}</h2>`;
    if (i.title_ko) content += `<details class="original"><summary>원문 제목 보기</summary><p>${escapeHTML(i.title)}</p></details>`;
    if (displayedSummary.length) {
      const label = video ? (i.summary_source === 'description' ? '영상 설명에서 뽑은 핵심 내용 · 한국어' : '영상 자막에서 뽑은 핵심 내용 · 한국어') : reddit ? '게시글 요약' : i.summary_source === 'feed' ? '피드 소개문 번역' : '한국어 발췌 요약';
      content += `<div class="label">${label}</div><ul class="summary">${displayedSummary.map(s => `<li>${escapeHTML(s)}</li>`).join('')}</ul>`;
    }
  }
  if (video && !validVideoSummary) content += `<p class="notice">${escapeHTML(i.error || '영상 내용에서 한국어 요약 3개 이상을 준비하고 있습니다.')}</p>`;
  else if (!x && !video && i.status !== 'ready') content += `<p class="notice">${escapeHTML(i.error || (i.title_ko ? '본문 번역 대기 중입니다.' : '번역 대기 중입니다. 원문을 먼저 확인할 수 있습니다.'))}</p>`;
  if (reddit) {
    content += `<details><summary>게시글 · 댓글 ${comments.length}개 읽기</summary>${i.body_ko ? `<p class="body">${escapeHTML(i.body_ko)}</p>` : `<p class="original">${i.status === 'ready' ? '텍스트 본문이 없는 이미지·링크 게시글입니다.' : '게시글 본문을 아직 가져오지 못했습니다.'}</p>`}${comments.map(c => `<div class="comment"><small>u/${escapeHTML(c.author)}${c.score == null ? '' : ` · 추천 ${escapeHTML(c.score)}`}</small><p class="body">${escapeHTML(c.body_ko || '댓글 번역 대기 중입니다.')}</p><details class="original"><summary>원문 댓글 보기</summary><p class="body">${escapeHTML(c.body_en)}</p></details>${link(c.url, '댓글 원문 ↗')}</div>`).join('')}${comments.length ? '' : '<p class="original">표시할 번역 댓글이 없습니다.</p>'}</details>`;
  }
  return `<article class="card">${img ? `${video ? `<a href="${escapeHTML(safeURL(i.url))}" target="_blank" rel="noopener noreferrer" aria-label="영상 보기">` : ''}<img class="image ${video ? 'video-image' : ''}" src="${escapeHTML(img)}" alt="${escapeHTML(i.title_ko || i.title)} 관련 이미지" loading="lazy" referrerpolicy="no-referrer">${video ? '</a>' : ''}` : ''}<div class="content"><div class="meta"><span class="badge ${kind}">${badge}</span><span>${escapeHTML(source)}</span>${kind === 'news' && i.language ? `<span>${escapeHTML(({en:'영어',ko:'한국어',ja:'일본어',zh:'중국어'})[i.language] || i.language)} 원문</span>` : ''}<time>${published}</time></div>${content}<div class="footer"><span class="original">${video ? '내용 발췌 · 자동 번역' : i.title_ko || i.body_ko ? '자동 번역 · 원문 확인 권장' : '원문 제공'}</span>${link(i.original_url || i.url, video ? 'YouTube에서 보기 ↗' : '원문 보기 ↗')}</div></div></article>`;
}
function channelMessage(data, tab) {
  if (data.channels?.[tab]?.message) return data.channels[tab].message;
  if (tab === 'x') return '지정된 10개 계정의 한국시간 2026년 10월 19일 이후 게시글을 표시합니다.';
  if (tab === 'youtube') return '매일 한국시간 09:00·18:00에 펠리컨스 영상을 검색하고 핵심 내용 3개 이상을 한국어로 제공합니다.';
  return (data.errors || []).filter(error => tab === 'reddit' ? error.startsWith('Reddit') : !error.startsWith('Reddit')).join(' · ');
}
function init() {
  const $ = selector => document.querySelector(selector);
  let data = {items:[]}, tab = INITIAL_TAB, limit = 20;
  const empty = {news:'조건에 맞는 뉴스가 없습니다.', reddit:'조건에 맞는 Reddit 게시글이 없습니다.', x:'수집된 X 게시글이 없습니다. 연결 후 새 게시글이 여기에 표시됩니다.', youtube:'수집된 YouTube 영상이 없습니다. 다음 검색 결과가 여기에 표시됩니다.'};
  function render() {
    const filtered = filterItems(data.items || [], tab, $('#search').value);
    $('#count').textContent = `${filtered.length}개의 ${tab === 'youtube' ? '영상' : '소식'} · 최신순`;
    $('#feed').innerHTML = filtered.slice(0, limit).map(card).join('') || `<div class="empty">${empty[tab]}</div>`;
    $('#more').hidden = filtered.length <= limit;
    const message = channelMessage(data, tab);
    $('#warning').hidden = !message;
    $('#warning').textContent = message;
    document.querySelectorAll('.image').forEach(img => img.addEventListener('error', () => img.remove()));
  }
  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
    tab = button.dataset.tab; limit = 20;
    document.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    render();
  }));
  $('#search').addEventListener('input', () => {limit = 20; render();});
  $('#more').addEventListener('click', () => {limit += 20; render();});
  fetch('data.json', {cache:'no-cache'}).then(r => {if (!r.ok) throw Error(); return r.json();}).then(result => {
    data = result;
    $('#updated').textContent = `마지막 수집 ${date(data.updated)} KST`;
    render();
  }).catch(() => {
    $('#updated').textContent = '피드 연결 실패';
    $('#feed').innerHTML = '<div class="empty">피드를 불러오지 못했습니다. 잠시 후 새로고침해 주세요.</div>';
  });
}
if (typeof document !== 'undefined') init();
if (typeof module !== 'undefined') module.exports = {INITIAL_TAB, TABS, escapeHTML, safeURL, kindOf, filterItems, card, channelMessage};
