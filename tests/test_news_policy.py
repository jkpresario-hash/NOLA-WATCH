import unittest
import urllib.parse
from datetime import datetime, timezone

import news_policy as policy


def news(title, language="en", **extra):
    urls = {"en": "https://www.nola.com/sports/pelicans/story.html",
            "ko": "https://www.rookie.co.kr/news/articleView.html?idxno=1",
            "ja": "https://basket-count.com/article/detail/123",
            "zh": "https://news.zhibo8.com/nba/2026-10-18/123.htm"}
    return dict(title=title, url=urls[language], kind="news", language=language,
                published="2026-10-18T00:00:00+00:00", **extra)


class NewsPolicyTests(unittest.TestCase):
    def test_allowlist_does_not_accept_similar_domains_or_other_publishers(self):
        for url in ["https://www.nola.com/sports/pelicans/story.html", "https://www.hoopsrumors.com/2026/10/pelicans.html", "https://www.theathletic.com/123/story/"]:
            self.assertTrue(policy.allowed_news_url(url))
        for url in ["https://nola.com.evil.test/sports/pelicans/x", "https://yahoo.com/pelicans", "https://nba.com/pelicans", "https://nola.com@evil.test/story", "javascript:alert(1)"]:
            self.assertFalse(policy.allowed_news_url(url))

    def test_path_and_board_restrictions(self):
        accepted = ["https://www.nytimes.com/athletic/123/story/", "https://sportingnews.com/jp/nba/news/pelicans/123", "https://gall.dcinside.com/board/view/?id=nba&no=123", "https://m.dcinside.com/board/nba/123", "https://sports.news.naver.com/basketball/article/065/123", "https://sports.news.naver.com/news.nhn?oid=398&aid=123"]
        rejected = ["https://nytimes.com/politics/123", "https://sportingnews.com/us/nba/news/pelicans/123", "https://gall.dcinside.com/board/view/?id=baseball&no=123", "https://sports.news.naver.com/basketball/article/076/123"]
        self.assertTrue(all(policy.allowed_news_url(url) for url in accepted))
        self.assertFalse(any(policy.allowed_news_url(url) for url in rejected))

    def test_all_searches_are_scoped_to_selected_sources(self):
        feeds = list(policy.search_feeds())
        self.assertEqual({language for language, _ in feeds}, set(policy.LANGUAGES))
        for language, url in feeds:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["q"][0]
            self.assertIn("site:", query)
            self.assertIn("when:7d", query)
            self.assertNotIn("site:yahoo", query)

    def test_cached_forbidden_news_cannot_bypass_new_policy(self):
        allowed = news("Pelicans sign Hunter Dickinson")
        forbidden = {**allowed, "url": "https://yahoo.com/story", "source_id": "nola", "publisher": "NOLA.com"}
        kept, _, stats = policy.curate([forbidden, allowed], lambda text: text)
        self.assertEqual(len(kept), 1)
        self.assertEqual(stats["outside_allowlist"], 1)

    def test_google_rss_publisher_metadata_is_checked(self):
        item = news("Pelicans sign Hunter Dickinson - NOLA.com")
        item.update(url="https://news.google.com/rss/articles/abc", publisher_url="https://www.nola.com")
        self.assertEqual(policy.source_for_item(item)["id"], "nola")
        item["publisher_url"] = "https://yahoo.com"
        self.assertIsNone(policy.source_for_item(item))
        item.update(publisher_url="https://nola.com", original_url="https://yahoo.com/story")
        self.assertIsNone(policy.source_for_item(item))

    def test_same_story_in_korean_japanese_chinese_prefers_english(self):
        english = news("Pelicans sign Hunter Dickinson - NOLA.com")
        foreign = [news("펠리컨스, 헌터 디킨슨 영입", "ko"), news("ペリカンズがハンターと契約", "ja"), news("鹈鹕签下亨特", "zh")]
        kept, deferred, stats = policy.curate(foreign + [english], lambda title: "New Orleans Pelicans signed Hunter Dickinson")
        self.assertEqual([item["language"] for item in kept], ["en"])
        self.assertEqual(stats["duplicate_story"], 3)
        self.assertEqual(deferred, [])

    def test_new_local_information_is_retained(self):
        items = [news("Pelicans sign Hunter Dickinson"), news("자이언, 지역 아동 행사에서 독점 인터뷰", "ko")]
        kept, _, _ = policy.curate(items, lambda title: "Exclusive interview with Zion Williamson at local children's event")
        self.assertEqual(len(kept), 2)

    def test_independent_interview_is_not_dropped_as_the_same_transaction(self):
        items = [news("Pelicans sign Hunter Dickinson"), news("디킨슨 영입 배경 독점 인터뷰", "ko")]
        kept, _, _ = policy.curate(items, lambda title: "Exclusive interview on Pelicans signing Hunter Dickinson")
        self.assertEqual(len(kept), 2)

    def test_same_player_different_event_is_not_removed(self):
        items = [news("Pelicans sign Hunter Dickinson"), news("디킨슨 부상 소식", "ko")]
        kept, _, _ = policy.curate(items, lambda title: "Hunter Dickinson sidelined by ankle injury")
        self.assertEqual(len(kept), 2)

    def test_different_scores_are_not_duplicates(self):
        items = [news("Pelicans beat Wizards 107-106"), news("펠리컨스가 위저즈에 110-108 승리", "ko")]
        kept, _, _ = policy.curate(items, lambda title: "Pelicans beat Wizards 110-108")
        self.assertEqual(len(kept), 2)

    def test_matching_headlines_outside_event_window_are_retained(self):
        a = news("Pelicans beat Wizards 107-106")
        b = news("Pelicans beat Wizards 107-106", "ko")
        b["published"] = "2026-09-01T00:00:00+00:00"
        kept, _, _ = policy.curate([a, b], lambda title: title)
        self.assertEqual(len(kept), 2)

    def test_unverifiable_foreign_news_is_held_for_retry(self):
        def unavailable(title):
            raise RuntimeError("Translation unavailable")
        kept, deferred, _ = policy.curate([news("Pelicans sign Hunter Dickinson"), news("새 소식", "ko")], unavailable)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(deferred), 1)
        self.assertIn("defer_reason", deferred[0])

    def test_failed_english_search_does_not_publish_unchecked_foreign_news(self):
        kept, deferred, _ = policy.curate([news("새 소식", "ko")], lambda title: "New story", english_complete=False)
        self.assertEqual(kept, [])
        self.assertEqual(len(deferred), 1)

    def test_tracking_links_do_not_duplicate_articles(self):
        a = news("Pelicans sign Hunter Dickinson")
        b = {**a, "url": a["url"] + "?utm_source=feed"}
        kept, _, _ = policy.curate([a, b], lambda title: title)
        self.assertEqual(len(kept), 1)

    def test_reddit_all_posts_are_preserved(self):
        post = dict(kind="reddit", source="Reddit", title="Daily chat", url="https://www.reddit.com/r/NOLAPelicans/comments/abc/")
        kept, _, _ = policy.curate([post, news("Pelicans sign Hunter Dickinson")], lambda title: title)
        self.assertEqual(len(kept), 2)
        self.assertIn(post, kept)

    def test_foreign_repost_of_english_reddit_news_is_removed(self):
        post = dict(kind="reddit", source="Reddit", title="Pelicans sign Hunter Dickinson", url="https://www.reddit.com/r/NOLAPelicans/comments/abc/")
        kept, _, _ = policy.curate([post, news("펠리컨스, 디킨슨 영입", "ko")], lambda title: "Pelicans sign Hunter Dickinson")
        self.assertEqual(kept, [post])

    def test_translated_wave_typo_and_bufkin_spelling_are_duplicates(self):
        english = news('Pelicans Waive Kobe Bufkin, Jalon Moore')
        foreign = news('코비 버프킨을 웨이브한 뉴올리언스', 'ko')
        kept, _, _ = policy.curate([english, foreign], lambda title:'New Orleans waves Kobe Bufkin')
        self.assertEqual(len(kept),1)
        kept, _, _ = policy.curate([english, foreign], lambda title:'New Orleans waves Kobe Buffkin')
        self.assertEqual(len(kept),1)
        chinese = news('鹈鹕裁掉科比', 'zh')
        kept, _, _ = policy.curate([english, chinese], lambda title:'Stein: Pelicans officially waived Kobe Bavkin, the 15th pick in 2023')
        self.assertEqual(len(kept),1)

    def test_roundup_facts_can_suppress_reposted_transaction(self):
        english = news('Minor roster moves around the NBA')
        english['event_facts_en']=policy.event_facts('The Pelicans have waived Hunter Dickinson.')
        foreign = news('鹈鹕裁掉亨特迪金森','zh')
        kept, _, _=policy.curate([english,foreign],lambda title:'Pelicans have waived Hunter Dickinson')
        self.assertEqual(len(kept),1)

    def test_old_and_undated_articles_do_not_enter_current_news(self):
        current=news('Pelicans sign Hunter Dickinson')
        old=news('Pelicans trade a player','ko')
        old['published']='2023-02-09T08:00:00+00:00'
        undated=news('Pelicans announcement','ja')
        undated.pop('published')
        kept,_,stats=policy.curate([current,old,undated],lambda text:text,now=datetime(2026,10,18,tzinfo=timezone.utc))
        self.assertEqual(len(kept),1)
        self.assertEqual(stats['outside_date_window'],1)
        self.assertEqual(stats['unknown_date'],1)

    def test_indexes_profiles_and_box_scores_are_not_news(self):
        items=[news('New Orleans Pelicans Draft News, Picks, Rumors, Analysis'),news('[프리시즌 박스] 워싱턴 vs 뉴올리언스','ko'),news('Zion Williamson - News, Stats, Bio and more')]
        kept,_,stats=policy.curate(items,lambda text:text)
        self.assertEqual(kept,[])
        self.assertEqual(stats['non_article'],3)

    def test_other_hupu_boards_are_not_pelicans_source(self):
        item=news('鹈鹕讨论','zh',source_context='warriors')
        item['url']='https://bbs.hupu.com/123.html'
        kept,_,stats=policy.curate([item],lambda title:'Pelicans discussion')
        self.assertEqual(kept,[])
        self.assertEqual(stats['outside_allowlist'],1)

    def test_unverified_hupu_board_is_not_published(self):
        item=news('鹈鹕讨论','zh')
        item['url']='https://bbs.hupu.com/123.html'
        kept,deferred,_=policy.curate([item],lambda title:'Pelicans discussion',require_context=True)
        self.assertEqual(kept,[])
        self.assertEqual(len(deferred),1)

    def test_historical_subclause_does_not_create_current_waiver(self):
        facts=policy.event_facts('After waiving Hunter Dickinson in August, the Pelicans have signed Hunter Dickinson again.')
        self.assertEqual([fact['action'] for fact in facts],['sign'])


if __name__ == "__main__":
    unittest.main()
