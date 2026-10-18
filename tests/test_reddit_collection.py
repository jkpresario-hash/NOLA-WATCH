import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

import collect


def post(post_id, title="Daily chat", **fields):
    return dict(title=title, url=f"https://www.reddit.com/r/NOLAPelicans/comments/{post_id}/post/",
                kind="reddit", source="Reddit · NOLAPelicans", **fields)


class RedditCollectionTests(unittest.TestCase):
    def test_fetches_new_listing_and_paginates_until_overlap(self):
        old = post("old")
        with patch.object(collect, "fetch", side_effect=[b"page1", b"page2"] ) as fetch, \
             patch.object(collect, "parse_feed", side_effect=[[post("a"), post("b")], [old, post("c")]]), \
             patch.object(collect.time, "sleep"):
            items = collect.collect_reddit([old], [])
        self.assertEqual([collect.item_key(i) for i in items], ["t3_a", "t3_b", "t3_old", "t3_c"])
        self.assertIn("/new/.rss?limit=100", fetch.call_args_list[0].args[0])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(fetch.call_args.args[0]).query)["after"], ["t3_b"])

    def test_failed_page_is_resumed_even_when_latest_page_is_known(self):
        checkpoint, errors = {}, []
        unavailable = urllib.error.HTTPError("", 429, "Rate limited", {}, None)
        with patch.object(collect, "fetch", side_effect=[b"first", unavailable]), \
             patch.object(collect, "parse_feed", return_value=[post("a")]), \
             patch.object(collect.time, "sleep"):
            first = collect.collect_reddit([], errors, checkpoint=checkpoint)
        self.assertEqual(len(first), 1)
        self.assertEqual(checkpoint["pending_after"], ["t3_a"])
        self.assertIn("HTTP 429", errors[0])
        with patch.object(collect, "fetch", side_effect=[b"known", b"missing", b"end"]) as fetch, \
             patch.object(collect, "parse_feed", side_effect=[[post("a")], [post("b")], []]), \
             patch.object(collect.time, "sleep"):
            second = collect.collect_reddit(first, [], checkpoint=checkpoint)
        self.assertEqual([collect.item_key(i) for i in second], ["t3_a", "t3_b"])
        self.assertIn("after=t3_a", fetch.call_args_list[1].args[0])
        self.assertEqual(checkpoint["pending_after"], [])

    def test_page_budget_saves_continuation_and_warns(self):
        checkpoint, errors = {}, []
        with patch.object(collect, "fetch", return_value=b"first"), \
             patch.object(collect, "parse_feed", return_value=[post("a")]):
            self.assertEqual(len(collect.collect_reddit([], errors, max_pages=1, checkpoint=checkpoint)), 1)
        self.assertEqual(checkpoint["pending_after"], ["t3_a"])
        self.assertTrue(errors)

    def test_reddit_posts_are_kept_beyond_news_retention_limit(self):
        reddit = [post(f"r{i}", title="Free streams discussion", title_ko="중계 토론") for i in range(210)]
        news = [dict(title="Pelicans news", title_ko="뉴스", url=f"https://example.com/{i}", source="News", kind="news") for i in range(220)]
        with patch.object(collect, "translate", return_value="번역"):
            items, _ = collect.collect_items(reddit + news, [], 0)
        self.assertEqual(sum(collect.is_reddit(i) for i in items), 210)
        self.assertEqual(sum(not collect.is_reddit(i) for i in items), 200)

    def test_permalink_slug_changes_do_not_duplicate_posts(self):
        old = post("abc", title_ko="기존 번역")
        new = {**old, "url": "https://www.reddit.com/r/NOLAPelicans/comments/abc/new_slug/"}
        with patch.object(collect, "translate", return_value="번역"):
            items, _ = collect.collect_items([new], [old], 0)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title_ko"], "기존 번역")


if __name__ == "__main__":
    unittest.main()
