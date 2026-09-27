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
        self.game = {"title": "Example"}
        for name, replacement in {
            "format_game": lambda game: "Descrizione italiana",
            "steam_lookup": AsyncMock(return_value={"official_match": True}),
            "get_session": AsyncMock(),
            "tg_api": AsyncMock(),
        }.items():
            patcher = patch.object(bot, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(bot.trailer_service, "send", AsyncMock(return_value=True))
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    async def test_video_is_attached_with_caption(self):
        self.assertTrue(await bot.send_game(101, self.game))
        self.assertEqual(self.send.await_args.args[:3], (101, "Example", "Descrizione italiana"))
        bot.tg_api.assert_not_awaited()

    async def test_missing_official_video_does_not_send_text_or_photo(self):
        self.send.return_value = False
        self.assertFalse(await bot.send_game(101, self.game))
        bot.tg_api.assert_not_awaited()

    async def test_final_delivery_failure_is_reported(self):
        self.send.side_effect = RuntimeError("Forbidden")
        with self.assertRaisesRegex(RuntimeError, "Forbidden"):
            await bot.send_game(101, self.game)
