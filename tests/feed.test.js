const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const feed = require('../feed.js');

test('exactly three tabs and news is the initial page', () => {
  const html = fs.readFileSync('index.html', 'utf8');
  assert.equal(feed.INITIAL_TAB, 'news');
  assert.deepEqual([...html.matchAll(/data-tab="([^"]+)"/g)].map(m => m[1]), feed.TABS);
  assert.deepEqual(feed.TABS, ['news', 'reddit', 'youtube']);
  assert.match(html, /data-tab="news" aria-pressed="true"/);
  assert.doesNotMatch(html, /data-tab="(?:all|x)"/);
});
test('each tab contains only its channel and cancelled posts cannot enter news', () => {
  const items = [{kind:'news',title:'News'}, {source:'Reddit · NOLAPelicans'}, {kind:'x',author:'PelicansLead'}, {kind:'youtube',channel:'Pels Film',summary_ko:['수비 분석']}];
  assert.deepEqual(feed.filterItems(items,'news'), [items[0]]);
  assert.deepEqual(feed.filterItems(items,'reddit'), [items[1]]);
  assert.deepEqual(feed.filterItems(items,'x','pelicanslead'), []);
  assert.deepEqual(feed.filterItems(items,'youtube','수비'), [items[3]]);
  assert.deepEqual(feed.filterItems(items,'all'), []);
});
test('videos show three verified Korean points and indicate their source', () => {
  const html = feed.card({kind:'youtube',title:'Video',url:'https://youtube.com/watch?v=123456789ab',summary_ko:['첫 번째 수비 분석','두 번째 공격 분석','세 번째 선수 분석'],summary_source:'transcript'});
  assert.equal((html.match(/<li>/g)||[]).length, 3);
  assert.match(html, /영상 자막에서/);
  assert.match(html, /YouTube에서 보기/);
  const pending = feed.card({kind:'youtube',title:'Video',summary_ko:['하나','둘']});
  assert.doesNotMatch(pending, /<li>/);
  assert.match(pending, /요약 3개 이상을 준비/);
});
test('untrusted social content and unsafe URLs cannot inject markup', () => {
  const html = feed.card({kind:'news',title:'<img src=x onerror=alert(1)>',title_ko:'번역 제목',url:'javascript:alert(1)',thumbnail:'javascript:alert(2)'});
  assert.doesNotMatch(html, /<script>|<img|href="javascript:/);
  assert.match(html, /&lt;img/);
  assert.equal(feed.safeURL('https://user:secret@example.com'), '');
});
test('channel notices do not leak warnings from unrelated tabs', () => {
  const data = {channels:{youtube:{message:'영상 검색 완료'}},errors:['Reddit failed','News failed']};
  assert.equal(feed.channelMessage(data,'youtube'), '영상 검색 완료');
  assert.equal(feed.channelMessage(data,'news'), 'News failed');
});
