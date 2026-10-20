import copy
import json
import time
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import article_collect as a

NOW = datetime(2026,10,20,6,tzinfo=timezone.utc)
SOURCE = a.SOURCES['intheno']
URL = 'https://intheno.substack.com/p/rotation-analysis'
BODY = '''The New Orleans Pelicans changed the starting lineup to create more space for their primary offensive players. Zion Williamson now catches the ball closer to the basket, giving the second defender less time to rotate across the lane.
Trey Murphy increased his role as a ball handler and continues to create open looks for his teammates. His shooting gravity allows the Pelicans to use smaller lineups without sacrificing their ability to rebound on the defensive end.
The Pelicans must decide how to balance the rookies with the veterans in the second unit. Their defensive rotations still need work, but the team can create more transition opportunities by keeping two reliable perimeter defenders on the floor.'''

def translation(text): return '한국어 '+text
def candidate(**fields): return dict(url=URL,source_id='intheno',title='A new approach to the rotation',published=a.iso(NOW-timedelta(days=45)),**fields)
def doc(**fields): return dict(title='A new approach to the rotation',published=a.iso(NOW-timedelta(days=45)),body=BODY,author='Writer',thumbnail='',access='public',**fields)
def discover(source,state,now,deadline): return ([candidate(feed_body='<p>Public publisher body</p>')] if source['id']=='intheno' else []),{'state':'ready','discovered':1 if source['id']=='intheno' else 0}
def old_article(days=30,**fields):
    return dict(kind='article',source_id='intheno',source='In The N.O.',url=URL,title='Pelicans analysis',published=a.iso(NOW-timedelta(days=days)),relevance_verified=True,relevance_policy=a.RELEVANCE_POLICY,status='ready',title_ko='기존 제목',summary_original=['Original sentence'],summary_ko=['기존 번역'],**fields)

class DiscoveryTests(unittest.TestCase):
    def test_only_five_selected_publishers_and_real_article_paths(self):
        self.assertEqual(set(a.SOURCES),{'hardwood','intheno','poetry','swish','noceilings'})
        for url in ['https://intheno.substack.com.evil.test/p/x','https://substack.com/p/x','https://user:secret@intheno.substack.com/p/x','http://intheno.substack.com/p/x','https://intheno.substack.com:444/p/x','https://intheno.substack.com/p/']:
            self.assertEqual(a.source_url(url,SOURCE,article=True),'')
        self.assertTrue(a.source_url(URL,SOURCE,article=True))
        self.assertFalse(a.source_url('https://theswishtheory.com/category/nba/',a.SOURCES['swish'],article=True))
        self.assertFalse(a.source_url('https://thehardwoodcollective.com/articles/nba',a.SOURCES['hardwood'],article=True))

    def test_url_identity_removes_tracking_and_folds_www_alias(self):
        source=a.SOURCES['poetry']
        self.assertEqual(a.source_url('https://basketballpoetry.com/p/test/?utm_source=x#comments',source,article=True),'https://www.basketballpoetry.com/p/test')

    def test_feed_keeps_public_article_content_and_publication_date(self):
        raw=b'''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item><title>Rotation &amp; spacing</title><link>https://intheno.substack.com/p/rotation-analysis?utm=x</link><pubDate>Tue, 20 Oct 2026 00:00:00 GMT</pubDate><content:encoded>&lt;p&gt;Pelicans analysis&lt;/p&gt;</content:encoded></item><item><title>Offsite</title><link>https://example.com/p/x</link></item></channel></rss>'''
        items=a.parse_feed(raw,SOURCE)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['url'],URL)
        self.assertIn('<p>Pelicans',items[0]['feed_body'])
        self.assertEqual(a.utc(items[0]['published']).year,2026)

    def test_listing_finds_article_links_and_forward_pagination(self):
        raw='<a href="/p/rotation-analysis">Rotations</a><a href="https://x.com/user">Other</a><link rel="alternate" type="application/rss+xml" href="/feed"><a rel="next" href="/archive?page=2">Next</a>'
        items,feeds,pages=a.parse_listing(raw,SOURCE['home'],SOURCE)
        self.assertEqual([i['url'] for i in items],[URL])
        self.assertEqual(feeds,['https://intheno.substack.com/feed'])
        self.assertEqual(pages,['https://intheno.substack.com/archive?page=2'])

    def test_archive_uses_public_metadata_and_ignores_hidden_body_fields(self):
        raw=json.dumps([{'slug':'rotation-analysis','title':'Rotations','post_date':'2026-10-01T00:00:00Z','body_html':'private content must not be used'}])
        item=a.parse_archive(raw,SOURCE)[0]
        self.assertEqual(item['url'],URL)
        self.assertNotIn('body_html',item)
        self.assertNotIn('private content',str(item))

    def test_pagination_reaches_90_day_boundary_and_stops(self):
        offsets=[]
        def fetch(url,source):
            if '/api/v1/archive' not in url:
                return b'<rss><channel/></rss>'
            offset=int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['offset'][0]);offsets.append(offset)
            age=10 if offset<100 else 100
            return json.dumps([dict(slug=f'post-{offset+i}',title='Analysis',post_date=a.iso(NOW-timedelta(days=age))) for i in range(50)]).encode()
        with patch.object(a,'fetch',side_effect=fetch):
            items,state=a.discover(SOURCE,{},NOW,time.monotonic()+10)
        self.assertEqual(offsets,[0,50,100])
        self.assertFalse(state['backfill_pending'])
        self.assertEqual(state['archive_offset'],0)
        self.assertEqual(len(items),150)

    def test_archive_failure_preserves_unfinished_cursor(self):
        def fetch(url,source):
            if '/api/v1/archive' not in url:return b'<rss><channel/></rss>'
            offset=int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['offset'][0])
            if offset:raise urllib.error.HTTPError(url,429,'limited',{},None)
            return json.dumps([dict(slug=f'post-{i}',title='Analysis',post_date=a.iso(NOW-timedelta(days=1))) for i in range(50)]).encode()
        with patch.object(a,'fetch',side_effect=fetch):
            _,state=a.discover(SOURCE,{},NOW,time.monotonic()+10)
        self.assertEqual(state['archive_offset'],50)
        self.assertTrue(state['backfill_pending'])

class ArticlePolicyTests(unittest.TestCase):
    def test_generic_title_can_qualify_from_relevant_body(self):
        self.assertTrue(a.related_passages(BODY,'The next step'))
        self.assertTrue(a.related_passages(BODY.replace('Pelicans','team').replace('New Orleans','The'),'The next step'))

    def test_title_or_fleeting_reference_is_not_enough(self):
        unrelated=('The Celtics are adjusting their rotation and looking for better perimeter defense. '*12)
        self.assertEqual(a.related_passages(unrelated,'Pelicans analysis'),[])
        self.assertEqual(a.related_passages(unrelated+'\nHe once played for the Pelicans.','League notes'),[])

    def test_long_prospect_profile_with_only_one_team_credit_is_not_analysis(self):
        paragraph=('Mallory is a smaller college guard with excellent rebounding instincts and strong timing. '*8)+'His teammate was current Pelicans forward Derik Queen.'
        self.assertFalse(a.related_passages(paragraph,'Tyler Tanner scouting report'))

    def test_redirect_handler_rejects_external_publisher_before_request(self):
        with self.assertRaises(ValueError):
            a.PublisherRedirect(SOURCE).redirect_request(None,None,302,'',{},'https://example.com/p/analysis')

    def test_body_verified_45_day_article_publishes_and_translates(self):
        with patch.object(a,'discover',side_effect=discover),patch.object(a,'document',return_value=doc()),patch.object(a,'fetch') as fetch:
            items,state,status=a.collect([],{},translation,now=NOW,seconds=10)
        fetch.assert_not_called()
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['kind'],'article')
        self.assertTrue(items[0]['relevance_verified'])
        self.assertTrue(items[0]['summary_ko'])
        self.assertNotIn('body',items[0])
        self.assertNotIn('feed_body',str(state))
        self.assertEqual(status['retention_days'],90)

    def test_90_day_boundary_is_inclusive_and_older_verified_items_expire(self):
        with patch.object(a,'discover',return_value=([],{'state':'ready'})):
            items,_,_=a.collect([old_article(90),{**old_article(91),'url':URL+'-old'}],{},translation,now=NOW)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['url'],URL)

    def test_new_undated_or_old_article_is_not_published(self):
        for published in ('',a.iso(NOW-timedelta(days=91))):
            with patch.object(a,'discover',side_effect=discover),patch.object(a,'document',return_value={**doc(),'published':published}):
                items,state,_=a.collect([],{},translation,now=NOW)
            self.assertEqual(items,[])
            self.assertEqual(bool(state['queue']),not bool(published))

    def test_unrelated_body_is_rejected_and_not_refetched_immediately(self):
        with patch.object(a,'discover',side_effect=discover),patch.object(a,'document',return_value={**doc(),'body':'Other basketball teams have different goals this season. '*20}):
            items,state,_=a.collect([],{},translation,now=NOW)
        self.assertFalse(items)
        self.assertEqual(state['checks'][URL]['result'],'unrelated')
        with patch.object(a,'discover',side_effect=discover),patch.object(a,'document') as document:
            a.collect([],state,translation,now=NOW)
        document.assert_not_called()

    def test_source_failure_keeps_previous_success_and_pending_retry(self):
        with patch.object(a,'discover',return_value=([],{'state':'partial','errors':['HTTP 403']})):
            items,_,status=a.collect([old_article()],{},translation,now=NOW)
        self.assertEqual(items[0]['summary_ko'],['기존 번역'])
        self.assertEqual(status['state'],'partial')
        entry=candidate();entry.pop('feed_body',None)
        with patch.object(a,'discover',return_value=([entry],{'state':'ready'})),patch.object(a,'fetch',side_effect=RuntimeError):
            items,state,_=a.collect([],{},translation,now=NOW)
        self.assertFalse(items)
        self.assertTrue(state['queue'])
        self.assertNotIn('feed_body',str(state))

    def test_translation_failure_preserves_verified_original_for_retry(self):
        with patch.object(a,'discover',side_effect=discover),patch.object(a,'document',return_value=doc()):
            items,state,_=a.collect([],{},lambda text:(_ for _ in ()).throw(RuntimeError()),now=NOW)
        self.assertEqual(items[0]['status'],'partial')
        originals=items[0]['summary_original'][:]
        with patch.object(a,'discover',return_value=([],{'state':'ready'})),patch.object(a,'fetch') as fetch:
            kept,_,_=a.collect(items,state,translation,now=NOW+timedelta(hours=9))
        fetch.assert_not_called()
        self.assertEqual(kept[0]['status'],'ready')
        self.assertEqual(kept[0]['summary_original'],originals)

class ExtractionTests(unittest.TestCase):
    def setUp(self):
        try: import bs4,trafilatura
        except ImportError:self.skipTest('HTML packages are installed in CI')

    def test_navigation_and_recommendations_cannot_prove_relevance(self):
        unrelated='Boston is changing its perimeter defense and moving the ball more quickly. '*15
        raw=f'<html><nav>New Orleans Pelicans Zion Williamson</nav><article><h1>League notes</h1><p>{unrelated}</p><div class="related-posts"><p>{BODY}</p></div></article></html>'
        d=a.document(raw,candidate())
        self.assertNotIn('Pelicans',d['body'])
        self.assertFalse(a.related_passages(d['body'],d['title']))

    def test_recommendation_article_before_main_body_is_not_selected(self):
        raw=f'<html><article>Another prospect and other unrelated basketball stories.</article><main><h1>Pelicans analysis</h1><div class="article-body"><p>{BODY}</p></div></main></html>'
        d=a.document(raw,candidate())
        self.assertTrue(a.related_passages(d['body'],d['title']))
        self.assertNotIn('Another prospect',d['body'])

    def test_publication_date_not_modified_date_and_paywall_is_labeled(self):
        raw=f'''<html><meta property="article:published_time" content="2026-08-01T00:00:00Z"><meta property="article:modified_time" content="2026-10-20T00:00:00Z"><article><h1>Pelicans analysis</h1><p>{BODY}</p><p>Subscribe to keep reading.</p></article></html>'''
        d=a.document(raw,candidate())
        self.assertEqual(d['published'],'2026-08-01T00:00:00Z')
        self.assertEqual(d['access'],'preview')

if __name__=='__main__':unittest.main()
