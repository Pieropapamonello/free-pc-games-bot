import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from test_start import bot


class TranslationTests(unittest.TestCase):
    def setUp(self):
        bot._translation_cache.clear()

    def test_failed_translation_preserves_real_source_description(self):
        original = "Celebrate Castlevania's 40th Anniversary and claim the original NES classic."
        with patch.object(bot, "translate_it", return_value=bot.TRANSLATION_UNAVAILABLE):
            card = bot.format_game({"title": "Castlevania", "description": original})
        self.assertIn(original, card)
        self.assertNotIn("Scopri dettagli", card)
        self.assertNotIn(bot.TRANSLATION_UNAVAILABLE, card)

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
            "tg_api": AsyncMock(return_value={"ok": True, "result": {"message_id": 37}}),
        }.items():
            patcher = patch.object(bot, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(bot.trailer_service, "send", AsyncMock(return_value=True))
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        if bot._media_tasks:
            await asyncio.gather(*list(bot._media_tasks))

    async def test_banner_is_sent_with_description_and_kept_when_video_is_missing(self):
        self.send.return_value = False
        game = dict(self.game, image="https://example.com/banner.jpg")
        self.assertTrue(await bot.send_game(101, game))
        await asyncio.gather(*list(bot._media_tasks))
        self.assertEqual(bot.tg_api.await_count, 1)
        self.assertEqual(bot.tg_api.await_args.args[0], "sendPhoto")
        self.assertEqual(bot.tg_api.await_args.kwargs["photo"], game["image"])
        self.assertEqual(bot.tg_api.await_args.kwargs["caption"], "Descrizione italiana")

    async def test_unavailable_banner_still_shows_the_game(self):
        bot.tg_api.side_effect = [{"ok": False, "error_code": 400, "description": "Invalid photo"},
                                 {"ok": True, "result": {"message_id": 37}}]
        self.assertTrue(await bot.send_game(101, dict(self.game, image="https://example.com/banner.jpg")))
        await asyncio.gather(*list(bot._media_tasks))
        self.assertEqual([call.args[0] for call in bot.tg_api.await_args_list], ["sendPhoto", "sendMessage"])

    async def test_video_is_attached_to_the_existing_card_with_caption(self):
        self.assertTrue(await bot.send_game(101, self.game))
        await asyncio.gather(*list(bot._media_tasks))
        self.assertEqual(self.send.await_args.args[:3], (101, "Example", "Descrizione italiana"))
        self.assertEqual(self.send.await_args.kwargs["message_id"], 37)
        self.assertEqual(bot.tg_api.await_args.args[0], "sendMessage")
        self.assertEqual(bot.tg_api.await_count, 1)

    async def test_missing_official_video_keeps_game_card_visible(self):
        self.send.return_value = False
        self.assertTrue(await bot.send_game(101, self.game))
        await asyncio.gather(*list(bot._media_tasks))
        self.assertEqual(bot.tg_api.await_count, 1)
        self.assertEqual(bot.tg_api.await_args.kwargs["text"], "Descrizione italiana")

    async def test_slow_trailer_does_not_delay_the_card(self):
        gate = asyncio.Event()
        async def delayed(*args, **kwargs):
            await gate.wait()
            return True
        self.send.side_effect = delayed
        try:
            self.assertTrue(await asyncio.wait_for(bot.send_game(101, self.game), timeout=1))
            self.assertEqual(bot.tg_api.await_count, 1)
        finally:
            gate.set()

    async def test_trailer_error_does_not_hide_or_duplicate_the_card(self):
        self.send.side_effect = RuntimeError("Trailer lookup failed")
        self.assertTrue(await bot.send_game(101, self.game))
        await asyncio.gather(*list(bot._media_tasks))
        self.assertEqual(bot.tg_api.await_count, 1)

    async def test_games_command_shows_results_when_all_trailers_are_missing(self):
        self.send.return_value = False
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=[self.game])):
            await bot._handle_giochi(101)
        await asyncio.gather(*list(bot._media_tasks))
        self.assertEqual(bot.tg_api.await_count, 1)
        self.assertEqual(bot.tg_api.await_args.kwargs["text"], "Descrizione italiana")

    async def test_final_delivery_failure_is_reported(self):
        bot.tg_api.return_value = {"ok": False, "description": "Forbidden"}
        with self.assertRaisesRegex(RuntimeError, "Forbidden"):
            await bot.send_game(101, self.game)
