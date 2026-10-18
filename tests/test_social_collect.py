import copy
import time
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch

import social_collect as social

NOW = datetime(2026, 10, 19, 2, tzinfo=timezone.utc)
CONTENT = ('The Pelicans used a smaller starting lineup to improve their spacing on offense. '
           'Zion Williamson attacked the rim while the second unit moved the ball around the perimeter. '
           'The coaching staff focused on defensive rotations and rebounding during the final quarter.')

def translated(text):
    return '한국어 번역: ' + text

def video(**fields):
    return dict(kind='youtube', video_id='123456789ab', title='New Orleans Pelicans analysis', **fields)

def post(number, **fields):
    return dict(id=str(number), author_id='1', created_at='2026-10-19T00:00:00Z', text='Pelicans update', **fields)

class YouTubeTests(unittest.TestCase):
    def test_public_search_url_matches_current_youtube_extractor(self):
        try:
            from yt_dlp.extractor.youtube import YoutubeSearchURLIE
        except ModuleNotFoundError:
            self.skipTest('yt-dlp is installed in CI')
        self.assertTrue(YoutubeSearchURLIE.suitable(social.youtube_search_url('뉴올리언스 펠리컨스')))

    def test_three_distinct_content_points_without_promotional_padding(self):
        points = social.summary_points(CONTENT + ' Subscribe to our channel for more basketball content.')
        self.assertEqual(len(points), 3)
        self.assertEqual(len(set(points)), 3)
        self.assertTrue(all('Subscribe' not in point for point in points))
        self.assertEqual(social.summary_points('Pelicans video. Subscribe now. Watch today.'), [])

    def test_chapters_adverts_channel_boilerplate_and_questions_do_not_count(self):
        padding = '''Download the Gametime app, create an account, and use code LOCKEDON for $20 off your first purchase.
Pelicans Digest is a YouTube channel dedicated to covering all the latest news, trades, reports, and rumors about the New Orleans Pelicans.
01:46:42 Philadelphia Seventy Sixers and their new roster heading into this season
Follow @thegametimehighlights on Instagram to Enter!
This video was edited to follow youtube's fair use guidelines for basketball highlights.
Are the Pelicans bench mob the key to postseason dreams?'''
        self.assertEqual(social.summary_points(padding), [])
        self.assertEqual(social.summary_points(CONTENT + '\n' + padding), social.summary_points(CONTENT))

    def test_empty_optional_search_description_does_not_abort_query(self):
        entries = [{'id':'123456789ab','title':'Pelicans update','description':None},
                   {'id':'123456789ac','title':'New Orleans Pelicans news'}]
        with patch.object(social, 'youtube_search', return_value=entries), \
             patch.object(social, 'youtube_transcript', return_value=[{'text':CONTENT}]):
            items, status = social.collect_youtube([], translated, NOW, time.monotonic()+10)
        self.assertEqual(len(items), 2)
        self.assertEqual(status['state'], 'ready')

    def test_description_summary_ignores_advert_legal_text_and_tag_lists(self):
        extras = '''Bonuses issued as non-withdrawable free bets that expire in 14 days.
Anson Belt & Buckle: Step up your belt game this fall and get two free bonuses with our box set.
Zion Williamson, Jeremiah Fears, Saddiq Bey, Jordan Poole, NBA Trade Rumors, NBA Free Agency, NBA Predictions
NBA 2K25 features ProPlay, a technology that transforms real NBA footage into animations for players.'''
        self.assertEqual(social.summary_points(CONTENT + '\n' + extras, content_only=True), social.summary_points(CONTENT))
        self.assertEqual(social.summary_points(extras, content_only=True), [])

    def test_unpunctuated_captions_survive_subscription_request(self):
        words = ' '.join('different%d' % index for index in range(300))
        self.assertEqual(len(social.summary_points('subscribe ' + words)), 3)

    def test_caption_summary_is_translated_and_metadata_is_not_a_summary(self):
        item = video()
        with patch.object(social, 'youtube_transcript', return_value=[{'text': CONTENT}]), \
             patch.object(social, 'youtube_details') as details:
            social.enrich_video(item, translated)
        details.assert_not_called()
        self.assertEqual(item['summary_source'], 'transcript')
        self.assertEqual(item['status'], 'ready')
        self.assertEqual(len(item['summary_ko']), 3)
        self.assertTrue(all(point.startswith('한국어') for point in item['summary_ko']))

    def test_description_is_labeled_and_insufficient_content_stays_pending(self):
        item = video()
        with patch.object(social, 'youtube_transcript', side_effect=RuntimeError), \
             patch.object(social, 'youtube_details', return_value={'description': CONTENT, 'channelTitle':'Pels Film'}):
            social.enrich_video(item, translated)
        self.assertEqual(item['summary_source'], 'description')
        self.assertEqual(item['channel'], 'Pels Film')
        with patch.object(social, 'youtube_transcript', return_value=[{'text':'Short clip'}]), \
             patch.object(social, 'youtube_details', return_value={'description':'Pelicans highlights'}):
            with self.assertRaises(ValueError):
                social.enrich_video(video(), translated)

    def test_failed_translation_retains_original_for_retry(self):
        item = video()
        with patch.object(social, 'youtube_transcript', return_value=[{'text':CONTENT}]):
            with self.assertRaises(ValueError):
                social.enrich_video(item, lambda text:'')
        self.assertEqual(len(item['summary_original']), 3)
        with patch.object(social, 'youtube_transcript') as transcript:
            social.enrich_video(item, translated)
        transcript.assert_not_called()
        self.assertEqual(len(item['summary_ko']), 3)

    def test_no_ready_status_for_untranslated_or_duplicate_points(self):
        for translator in (lambda text:text, lambda text:'같은 번역'):
            with patch.object(social, 'youtube_transcript', return_value=[{'text':CONTENT}]):
                with self.assertRaises(ValueError):
                    social.enrich_video(video(), translator)

    def test_two_searches_deduplicate_videos_and_keep_prior_success_on_failure(self):
        entry = dict(id='123456789ab', title='New Orleans Pelicans analysis', published='2026-10-19T00:00:00Z')
        with patch.object(social, 'youtube_search', return_value=[entry]) as search, \
             patch.object(social, 'youtube_transcript', return_value=[{'text':CONTENT}]):
            items, status = social.collect_youtube([], translated, NOW, time.monotonic()+10)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(len(items), 1)
        self.assertEqual(status['summarized'], 1)
        with patch.object(social, 'youtube_search', side_effect=RuntimeError('secret')):
            kept, status = social.collect_youtube(items, translated, NOW, time.monotonic()+10)
        self.assertEqual(kept[0]['summary_ko'], items[0]['summary_ko'])
        self.assertEqual(status['state'], 'partial')
        self.assertNotIn('secret', str(status))

    def test_irrelevant_old_and_invalid_video_ids_are_excluded(self):
        entries = [dict(id='123456789ab',title='Other basketball team'),
                   dict(id='123456789ac',title='Pelicans',published='2026-09-01T00:00:00Z'),
                   dict(id='javascript:',title='Pelicans')]
        with patch.object(social, 'youtube_search', return_value=entries):
            items, _ = social.collect_youtube([], translated, NOW, time.monotonic()+10)
        self.assertEqual(items, [])

class XTests(unittest.TestCase):
    def setUp(self):
        self.accounts = patch.dict(social.CONFIG['x'], {'accounts':['PelicansLead']})
        self.accounts.start()
        self.addCleanup(self.accounts.stop)

    def test_exact_account_scope_and_korean_midnight_cutoff(self):
        good = dict(kind='x',author='PelicansLead',published='2026-10-18T15:00:00Z')
        self.assertTrue(social.eligible_x(good))
        self.assertFalse(social.eligible_x({**good,'published':'2026-10-18T14:59:59Z'}))
        self.assertFalse(social.eligible_x({**good,'author':'OtherPelicansAccount'}))
        self.assertEqual(social.iso(social.X_START), '2026-10-18T15:00:00Z')

    def test_no_token_is_honest_and_does_not_call_x(self):
        with patch.object(social, 'x_get') as api:
            items, status = social.collect_x([], {}, translated, NOW, time.monotonic()+10, token='')
        api.assert_not_called()
        self.assertEqual(status['state'], 'needs_connection')
        self.assertEqual(items, [])

    def test_paginated_run_resumes_before_advancing_high_water_mark(self):
        checkpoint = {'PelicansLead':{'user_id':'1','since_id':'100'}}
        with patch.object(social, 'x_get', return_value={'data':[post(300)],'meta':{'next_token':'page2'}}):
            items, status = social.collect_x([], checkpoint, translated, NOW, time.monotonic()+10, token='secret', max_pages=1)
        self.assertEqual(checkpoint['PelicansLead']['since_id'], '100')
        self.assertEqual(checkpoint['PelicansLead']['pending_token'], 'page2')
        with patch.object(social, 'x_get', return_value={'data':[post(200)],'meta':{}}) as api:
            items, _ = social.collect_x(items, checkpoint, translated, NOW, time.monotonic()+10, token='secret')
        self.assertEqual(api.call_args.args[1]['pagination_token'], 'page2')
        self.assertEqual(api.call_args.args[1]['since_id'], '100')
        self.assertEqual(checkpoint['PelicansLead']['since_id'], '300')
        self.assertNotIn('pending_token', checkpoint['PelicansLead'])
        self.assertEqual({item['post_id'] for item in items}, {'200','300'})
        self.assertTrue(all(item['body_ko'] for item in items))

    def test_wrong_account_and_before_cutoff_posts_are_rejected(self):
        response = {'data':[post(10), {**post(11),'author_id':'2'}, {**post(12),'created_at':'2026-10-18T14:59:59Z'}]}
        with patch.object(social, 'x_get', return_value=response):
            items, _ = social.collect_x([], {'PelicansLead':{'user_id':'1'}}, translated, NOW, time.monotonic()+10, token='secret')
        self.assertEqual([item['post_id'] for item in items], ['10'])

    def test_usernames_are_not_silently_substituted(self):
        with patch.object(social, 'x_get', return_value={'data':{'id':'1','username':'DifferentHandle'}}) as api:
            items, status = social.collect_x([], {}, translated, NOW, time.monotonic()+10, token='secret')
        self.assertEqual(api.call_count, 1)
        self.assertEqual(items, [])
        self.assertEqual(status['state'], 'partial')

    def test_error_keeps_cursor_and_never_exposes_token(self):
        checkpoint = {'PelicansLead':{'user_id':'1','since_id':'100'}}
        original = copy.deepcopy(checkpoint)
        error = urllib.error.HTTPError('https://api.x.com',403,'secret-token',{},None)
        with patch.object(social, 'x_get', side_effect=error):
            _, status = social.collect_x([], checkpoint, translated, NOW, time.monotonic()+10, token='secret-token')
        self.assertEqual(checkpoint, original)
        self.assertNotIn('secret-token', str(status))
        self.assertIn('HTTP 403', str(status))

    def test_long_post_is_translated_and_changed_content_invalidates_cache(self):
        old = dict(kind='x',author='PelicansLead',post_id='10',published='2026-10-19T00:00:00Z',
                   body_en='Old content',body_ko='이전 번역',status='ready')
        long = 'The entire long post instead of a truncated preview.'
        with patch.object(social, 'x_get', return_value={'data':[post(10,note_tweet={'text':long})]}):
            items, _ = social.collect_x([old], {'PelicansLead':{'user_id':'1'}}, translated, NOW, time.monotonic()+10, token='secret')
        self.assertEqual(items[0]['body_en'], long)
        self.assertEqual(items[0]['body_ko'], translated(long))

    def test_error_payload_does_not_mark_pagination_complete(self):
        checkpoint = {'PelicansLead':{'user_id':'1','since_id':'100','pending_token':'page2','pending_since_id':'100','pending_newest_id':'300'}}
        old = copy.deepcopy(checkpoint)
        with patch.object(social, 'x_get', return_value={'errors':[{'detail':'Not available'}]}):
            _, status = social.collect_x([], checkpoint, translated, NOW, time.monotonic()+10, token='secret')
        self.assertEqual(checkpoint, old)
        self.assertEqual(status['state'], 'partial')

if __name__ == '__main__':
    unittest.main()
