import json
import unittest
from unittest.mock import patch
import collect

class FeedTests(unittest.TestCase):
    def test_atom_metadata(self):
        data = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Defense</title><link href="https://www.reddit.com/r/NOLAPelicans/comments/abc/"/><updated>2026-10-17T10:00:00Z</updated><content>&lt;img src="https://example.com/a.jpg"/&gt;</content></entry></feed>'''
        item = collect.parse_feed(data, 'Reddit')[0]
        self.assertEqual(item['thumbnail'], 'https://example.com/a.jpg')
        self.assertEqual(item['kind'], 'reddit')
        self.assertTrue(item['published'])

    def test_spam_and_unsafe_links(self):
        data = b'<rss><channel><item><title>Pelicans WATCHLIVE free streams</title><link>https://example.com</link></item><item><title>Pelicans</title><link>javascript:alert(1)</link></item></channel></rss>'
        self.assertEqual(collect.parse_feed(data, 'Google'), [])
        self.assertEqual(collect.safe_url('javascript:alert(1)'), '')

    def test_reddit_all_topics_are_included(self):
        data = b'<rss><channel><item><title>Free streams discussion</title><link>https://www.reddit.com/r/NOLAPelicans/comments/abc/</link></item><item><title>Daily chat</title><link>https://www.reddit.com/r/NOLAPelicans/comments/def/</link></item><item><title>Image post</title><link>javascript:alert(1)</link></item></channel></rss>'
        self.assertEqual(len(collect.parse_feed(data, 'Reddit')), 2)

    def test_reddit_comments_and_image(self):
        payload = [{'data':{'children':[{'data':{'selftext':'Hello','num_comments':3,'preview':{'images':[{'source':{'url':'https://example.com/img.jpg'}}]}}}]}}, {'data':{'children':[{'kind':'t1','data':{'body':'Great defense','author':'fan','score':4,'permalink':'/r/NOLAPelicans/comments/abc/comment/'}},{'kind':'t1','data':{'body':'[deleted]'}},{'kind':'more','data':{}}]}}]
        with patch.object(collect,'fetch',return_value=json.dumps(payload).encode()), patch.object(collect,'translate',side_effect=lambda s:'번역 '+s):
            item={'url':'https://www.reddit.com/r/NOLAPelicans/comments/abc/'}
            collect.reddit(item)
        self.assertEqual(len(item['comments']),1)
        self.assertEqual(item['comments'][0]['body_ko'],'번역 Great defense')
        self.assertEqual(item['thumbnail'],'https://example.com/img.jpg')

    def test_translation_cached(self):
        collect.CACHE.clear()
        with patch.object(collect,'fetch',return_value=json.dumps([[['안녕하세요','Hello',None,None]]]).encode()) as fetch:
            self.assertEqual(collect.translate('Hello'),'안녕하세요')
            self.assertEqual(collect.translate('Hello'),'안녕하세요')
            self.assertEqual(fetch.call_count,1)

    def test_article_summary(self):
        body='The Pelicans played excellent defense in the opening quarter. The team then took the lead and held it through the final whistle. Fans were excited about the performance of the young players.'
        with patch.object(collect,'fetch',return_value=b'<html><meta property="og:image" content="/photo.jpg"></html>'),patch.object(collect.trafilatura,'extract',return_value=body),patch.object(collect,'translate',side_effect=lambda s:'요약 '+s):
            item={'url':'https://www.nola.com/sports/pelicans/news.html'}
            collect.article(item)
        self.assertEqual(len(item['summary_ko']),3)
        self.assertEqual(item['thumbnail'],'https://www.nola.com/photo.jpg')

    def test_reddit_rss_fallback(self):
        import urllib.error
        xml = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>t3_abc</id><content>&lt;div class="md"&gt;Post text&lt;/div&gt;</content></entry><entry><id>t1_c1</id><author><name>/u/fan</name></author><content>Comment text</content><link href="https://www.reddit.com/r/NOLAPelicans/comments/abc/c1/"/></entry></feed>'''
        with patch.object(collect,'reddit_json',side_effect=urllib.error.HTTPError('',403,'Forbidden',{},None)),patch.object(collect,'fetch',return_value=xml),patch.object(collect,'translate',side_effect=lambda s:'번역 '+s):
            item={'url':'https://www.reddit.com/r/NOLAPelicans/comments/abc/title/'}
            collect.reddit(item)
        self.assertEqual(item['body_ko'],'번역 Post text')
        self.assertEqual(item['comments'][0]['author'],'fan')
        self.assertEqual(item['comments'][0]['body_ko'],'번역 Comment text')

    def test_decoder_dependency(self):
        from googlenewsdecoder import gnewsdecoder
        self.assertTrue(callable(gnewsdecoder))

    def test_article_outside_allowlist_is_not_fetched(self):
        item={'url':'https://yahoo.com/pelicans-news'}
        with patch.object(collect, 'fetch') as fetch:
            with self.assertRaises(ValueError):
                collect.article(item)
        fetch.assert_not_called()
        self.assertTrue(item['policy_rejected'])

    def test_external_redirect_is_blocked_before_request(self):
        import urllib.request
        request=urllib.request.Request('https://nola.com/sports/pelicans/story')
        with self.assertRaises(ValueError):
            collect.NewsRedirectHandler().redirect_request(request,None,302,'Found',{},'https://yahoo.com/story')

    def test_translation_targets_have_separate_cache_entries(self):
        collect.CACHE.clear()
        with patch.object(collect, 'fetch', side_effect=[json.dumps([[['영입','signed',None,None]]]).encode(),json.dumps([[['signed','영입',None,None]]]).encode()]) as fetch:
            self.assertEqual(collect.translate('영입'), '영입')
            self.assertEqual(collect.translate('영입', target='en'), 'signed')
        self.assertEqual(fetch.call_count,2)

if __name__=='__main__': unittest.main()

