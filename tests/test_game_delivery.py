import unittest
from unittest.mock import AsyncMock, patch

from test_start import bot


class TranslationTests(unittest.TestCase):
    def setUp(self):
        bot._translation_cache.clear()

    def test_epic_description_is_translated_despite_source_flag(self):
        with patch.object(bot, "GoogleTranslator") as translator:
            translator.return_value.translate.return_value = "Esplora un mondo fantastico."
            result = bot.format_game({"title": "Example", "translate": False,
                                      "description": "Explore a fantasy world."})
            self.assertIn("Esplora un mondo fantastico.", result)
            self.assertNotIn("Explore a fantasy world.", result)

    def test_failure_uses_italian_notice_and_can_recover(self):
        with patch.object(bot, "GoogleTranslator") as translator:
            translator.return_value.translate.side_effect = [
                RuntimeError("offline"), RuntimeError("offline"), "Un mondo fantastico."]
            self.assertEqual(bot.translate_it("A fantasy world."), bot.TRANSLATION_UNAVAILABLE)
            self.assertEqual(bot.translate_it("A fantasy world."), "Un mondo fantastico.")
            self.assertEqual(bot.translate_it("A fantasy world."), "Un mondo fantastico.")
            self.assertEqual(translator.return_value.translate.call_count, 3)


class VideoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.game = {"title": "Example", "image": "https://example.com/cover.jpg"}
        for name, replacement in {
            "format_game": lambda game: "Descrizione italiana",
            "search_steam_trailer": AsyncMock(return_value="https://example.com/steam.mp4"),
            "search_youtube_video": AsyncMock(return_value=("https://youtube.com/watch?v=example", "thumb")),
            "extract_youtube_mp4": AsyncMock(return_value="https://example.com/youtube.mp4"),
            "tg_api": AsyncMock(return_value={"ok": True}),
        }.items():
            patcher = patch.object(bot, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_steam_success_does_not_need_youtube(self):
        await bot.send_game(101, self.game)
        self.assertEqual(bot.tg_api.await_args.args[0], "sendVideo")
        self.assertEqual(bot.tg_api.await_args.kwargs["video"], "https://example.com/steam.mp4")
        bot.search_youtube_video.assert_not_awaited()

    async def test_rejected_steam_video_tries_youtube(self):
        bot.tg_api.side_effect = [{"ok": False}, {"ok": True}]
        await bot.send_game(101, self.game)
        self.assertEqual([c.args[0] for c in bot.tg_api.await_args_list], ["sendVideo", "sendVideo"])
        self.assertEqual(bot.tg_api.await_args.kwargs["video"], "https://example.com/youtube.mp4")

    async def test_unavailable_videos_send_text_and_link_without_photo(self):
        bot.tg_api.side_effect = [{"ok": False}, {"ok": False}, {"ok": True}]
        await bot.send_game(101, self.game)
        self.assertEqual([c.args[0] for c in bot.tg_api.await_args_list],
                         ["sendVideo", "sendVideo", "sendMessage"])
        self.assertIn("reply_markup", bot.tg_api.await_args.kwargs)
        self.assertTrue(bot.tg_api.await_args.kwargs["disable_web_page_preview"])

    async def test_long_description_is_sent_separately(self):
        with patch.object(bot, "format_game", return_value="a" * 1100):
            await bot.send_game(101, self.game)
        self.assertEqual([c.args[0] for c in bot.tg_api.await_args_list], ["sendVideo", "sendMessage"])
        self.assertLess(len(bot.tg_api.await_args_list[0].kwargs["caption"]), 1024)

    async def test_final_delivery_failure_is_reported(self):
        bot.tg_api.return_value = {"ok": False, "description": "Forbidden: bot blocked"}
        with self.assertRaisesRegex(RuntimeError, "Forbidden"):
            await bot.send_game(101, self.game)
