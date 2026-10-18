import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch

import collect


def post(number, **fields):
    return dict(title=f"Pelicans news {number}", url=f"https://example.com/{number}",
                source="Google News", kind="news", published=f"2026-10-{18-number:02d}", **fields)


class TranslationPipelineTests(unittest.TestCase):
    def test_all_headlines_translate_even_with_zero_content_budget(self):
        with patch.object(collect, "translate", side_effect=lambda text: "번역 " + text), \
             patch.object(collect, "article") as article:
            items, processed = collect.collect_items([post(i) for i in range(5)], [], 0)
        self.assertEqual(processed, 0)
        article.assert_not_called()
        self.assertTrue(all(item["title_ko"].startswith("번역 ") for item in items))

    def test_failed_items_do_not_starve_unattempted_items(self):
        failed = post(0, status="partial", title_ko="기존 제목", last_attempt_at="2020-01-01T00:00:00+00:00")
        new = post(1)
        with patch.object(collect, "translate", return_value="번역"), \
             patch.object(collect, "article") as article:
            collect.collect_items([failed, new], [], 1)
        self.assertEqual(article.call_args.args[0]["url"], new["url"])

    def test_source_failure_preserves_available_translation(self):
        item = post(0, body_en="Original post", comments=[{"body_en": "Comment", "url": "https://example.com/c"}])
        with patch.object(collect, "translate", side_effect=lambda text: "번역 " + text), \
             patch.object(collect, "article", side_effect=urllib.error.HTTPError("", 403, "Forbidden", {}, None)):
            items, _ = collect.collect_items([item], [], 1)
        self.assertEqual(items[0]["body_ko"], "번역 Original post")
        self.assertEqual(items[0]["comments"][0]["body_ko"], "번역 Comment")
        self.assertEqual(items[0]["content_error"], "HTTP 403")
        self.assertEqual(items[0]["status"], "partial")

    def test_single_comment_failure_does_not_drop_other_translations(self):
        item = post(0, body_en="Body", comments=[{"body_en": "Bad"}, {"body_en": "Good"}])
        def translate(text):
            if text == "Bad":
                raise RuntimeError("unavailable")
            return "번역 " + text
        with patch.object(collect, "translate", side_effect=translate):
            failures = collect.translate_available(item)
        self.assertIn("comment_0", failures)
        self.assertEqual(item["body_ko"], "번역 Body")
        self.assertEqual(item["comments"][1]["body_ko"], "번역 Good")
        self.assertEqual(item["comments"][0]["body_en"], "Bad")

    def test_retry_translates_saved_summary_without_fetching_source(self):
        item = post(0, summary_en=["Saved sentence"], status="partial",
                    last_attempt_at=datetime.now(timezone.utc).isoformat())
        with patch.object(collect, "translate", side_effect=lambda text: "번역 " + text), \
             patch.object(collect, "article") as article:
            items, _ = collect.collect_items([], [item], 1)
        article.assert_not_called()
        self.assertEqual(items[0]["summary_ko"], ["번역 Saved sentence"])

    def test_unchanged_original_preserves_successful_translation(self):
        item = {"body_en": "Original", "body_ko": "기존 번역"}
        collect.set_original(item, "body_en", "body_ko", "Original")
        self.assertEqual(item["body_ko"], "기존 번역")
        collect.set_original(item, "body_en", "body_ko", "Changed")
        self.assertNotIn("body_ko", item)

    def test_content_budget_keeps_translated_headlines(self):
        with patch.object(collect, "translate", return_value="번역"), \
             patch.object(collect, "article") as article:
            items, processed = collect.collect_items([post(0)], [], 1, content_seconds=0)
        self.assertEqual(processed, 0)
        self.assertEqual(items[0]["title_ko"], "번역")
        article.assert_not_called()


if __name__ == "__main__":
    unittest.main()
