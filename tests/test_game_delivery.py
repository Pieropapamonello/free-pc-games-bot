import asyncio
import unittest
import tempfile
from unittest.mock import AsyncMock, patch

from test_start import bot


class TranslationTests(unittest.TestCase):
    def setUp(self):
        bot._translation_cache.clear()
        patcher = patch.object(bot, "_persist_translation")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_failed_translation_never_publishes_english_description(self):
        original = "Celebrate Castlevania's 40th Anniversary and claim the original NES classic."
        with patch.object(bot, "translate_it", return_value=bot.TRANSLATION_UNAVAILABLE):
            card = bot.format_game({"title": "Castlevania", "description": original})
        self.assertNotIn(original, card)
        self.assertNotIn("Scopri dettagli", card)
        self.assertIn(bot.TRANSLATION_UNAVAILABLE, card)

    def test_epic_description_is_translated_despite_source_flag(self):
        with patch.object(bot, "_google_translation", return_value="Esplora un mondo fantastico."):
            result = bot.format_game({"title": "Example", "translate": False,
                                      "description": "Explore a fantasy world."})
            self.assertIn("Esplora un mondo fantastico.", result)
            self.assertNotIn("Explore a fantasy world.", result)

    def test_failure_uses_italian_notice_and_can_recover(self):
        with patch.object(bot, "_google_translation", side_effect=[
                RuntimeError("offline"), RuntimeError("offline"), "Un mondo fantastico."]) as translator, \
             patch.object(bot, "_mymemory_translation", side_effect=RuntimeError("offline")):
            self.assertEqual(bot.translate_it("A fantasy world."), bot.TRANSLATION_UNAVAILABLE)
            self.assertEqual(bot.translate_it("A fantasy world."), "Un mondo fantastico.")
            self.assertEqual(bot.translate_it("A fantasy world."), "Un mondo fantastico.")
            self.assertEqual(translator.call_count, 3)

    def test_alternative_used_when_google_returns_english(self):
        source = "Build bridges and explore a fantasy world."
        with patch.object(bot, "_google_translation", return_value=source), \
             patch.object(bot, "_mymemory_translation", return_value="Costruisci ponti ed esplora un mondo fantastico.") as alternate:
            self.assertEqual(bot.translate_it(source), "Costruisci ponti ed esplora un mondo fantastico.")
        alternate.assert_called_once()

    def test_italian_source_requires_no_network(self):
        source = "Costruisci ponti ed esplora un mondo fantastico."
        with patch.object(bot, "_google_translation") as google:
            self.assertEqual(bot.translate_it(source), source)
        google.assert_not_called()

    def test_cache_survives_restart_and_reuses_translation_offline(self):
        source = "Build bridges and explore a fantasy world."
        translated = "Costruisci ponti ed esplora un mondo fantastico."
        key = bot.hashlib.sha256(source.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(bot, "DATA_DIR", bot.Path(folder)), patch.object(bot, "USE_FIREBASE", False):
            bot._translation_cache[key] = translated
            # Chiamata reale al salvataggio, non al mock del setUp.
            path = bot.DATA_DIR / "translations_it.json"
            path.write_text(bot.json.dumps(bot._translation_cache), encoding="utf-8")
            bot._translation_cache.clear()
            bot._load_translation_cache()
            with patch.object(bot, "_google_translation") as google:
                self.assertEqual(bot.translate_it(source), translated)
            google.assert_not_called()

    def test_mymemory_quota_is_not_treated_as_translation(self):
        with patch.object(bot.requests, "get") as get:
            get.return_value.json.return_value = {"responseStatus": 200, "quotaFinished": True,
                "responseData": {"translatedText": "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS"}}
            with self.assertRaises(ValueError):
                bot._mymemory_translation("Build bridges and explore a fantasy world.")
            self.assertEqual(get.call_args.kwargs["timeout"], (4, 8))

    def test_english_cache_is_rejected_and_retranslated(self):
        source = "Build bridges and explore a fantasy world."
        key = bot.hashlib.sha256(source.encode()).hexdigest()
        bot._translation_cache[key] = source
        with patch.object(bot, "_google_translation", return_value="Costruisci ponti ed esplora un mondo fantastico.") as google:
            result = bot.translate_it(source)
        self.assertNotEqual(result, source)
        google.assert_called_once()


class DescriptionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_updates_existing_text_message(self):
        with patch.object(bot.asyncio, "sleep", AsyncMock()), \
             patch.object(bot, "format_game", return_value="Una descrizione tradotta in italiano."), \
             patch.object(bot, "tg_api", AsyncMock(side_effect=[
                 {"ok": False, "error_code": 400}, {"ok": True}])) as api:
            result = await bot._refresh_description(101, 37, {"title": "Example"}, bot.TRANSLATION_UNAVAILABLE)
        self.assertEqual(result, "Una descrizione tradotta in italiano.")
        self.assertEqual([call.args[0] for call in api.await_args_list], ["editMessageCaption", "editMessageText"])
        self.assertTrue(all(call.kwargs["message_id"] == 37 for call in api.await_args_list))

    async def test_failed_recovery_keeps_card_without_english(self):
        with patch.object(bot.asyncio, "sleep", AsyncMock()), \
             patch.object(bot, "format_game", return_value=bot.TRANSLATION_UNAVAILABLE), \
             patch.object(bot, "tg_api", AsyncMock()) as api:
            result = await bot._refresh_description(101, 37, {"title": "Example"}, bot.TRANSLATION_UNAVAILABLE)
        self.assertEqual(result, bot.TRANSLATION_UNAVAILABLE)
        api.assert_not_called()


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
        patcher = patch.object(bot.trailer_service, "gameplay", AsyncMock(return_value=None))
        patcher.start()
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
