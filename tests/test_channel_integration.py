import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collect

class ChannelIntegrationTests(unittest.TestCase):
    def test_social_items_bypass_news_allowlist_and_keep_channel_state(self):
        news = dict(kind='news', source='NOLA.com', language='en', title='Pelicans lineup', published='2026-10-19T00:00:00Z')
        video = dict(kind='youtube', video_id='123456789ab', title='Pelicans analysis', published='2026-10-19T01:00:00Z')
        article = dict(kind='article', source_id='intheno', title='Pelicans contracts', published='2026-09-01T00:00:00Z')
        tweet = dict(kind='x', author='PelicansLead', post_id='123', title='Pelicans post', published='2026-10-19T02:00:00Z')
        passed = []
        def curate(items, *args, **kwargs):
            self.assertTrue(all(item['kind'] not in ('x','youtube','article') for item in items))
            passed.extend(items)
            return items, [], {}
        def collect_items(entries, previous, limit, **kwargs):
            return kwargs['news_filter'](previous), 0
        current = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                Path('data.json').write_text(json.dumps({'items':[news,video,tweet,article], 'social_state':{'x':{'cursor':'cancelled'}}, 'channels':{'x':{'state':'needs_connection'}}}))
                with patch('sys.argv', ['collect.py']), \
                     patch.object(collect.news_policy, 'search_feeds', return_value=[]), \
                     patch.dict(collect.SOURCES, {}, clear=True), \
                     patch.object(collect.news_policy, 'curate', side_effect=curate), \
                     patch.object(collect, 'collect_items', side_effect=collect_items), \
                     patch.object(collect.article_collect, 'collect', return_value=([article], {'queue':[]}, {'state':'ready','retention_days':90})), \
                     patch.object(collect.social_collect, 'collect', return_value=([video], {}, {'youtube':{'state':'ready'}})):
                    collect.main()
                result = json.loads(Path('data.json').read_text())
            finally:
                os.chdir(current)
        self.assertEqual({item['kind'] for item in result['items']}, {'news','youtube','article'})
        self.assertTrue(passed)
        self.assertNotIn('x', result['channels'])
        self.assertNotIn('x', result['social_state'])
        self.assertEqual(result['channels']['article']['retention_days'], 90)
        self.assertEqual(result['article_state'], {'queue':[]})

if __name__ == '__main__':
    unittest.main()
