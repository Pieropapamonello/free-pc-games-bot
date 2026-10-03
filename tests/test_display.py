import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import test_start

bot = test_start.bot


class DisplayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_start.StartRoutingTests.asyncSetUp(self)
        self.games = [dict(id=f"game_{i}", title=f"Game <{i}>",
                           url=f"https://store.example/{i}", categories=["pc"],
                           content_type="game") for i in range(3)]

    async def test_format_selection_is_local_to_chat_and_survives_restart(self):
        with patch.object(bot, "tg_api", AsyncMock(return_value={"ok": True})):
            await bot._save_display(101, 5, "digest")
        self.assertEqual(bot.state.get_display(202), "cards")
        restored = bot.State()
        self.assertEqual(restored.get_display(101), "digest")

    async def test_firebase_display_restored(self):
        with patch.object(bot, "USE_FIREBASE", True), patch.object(
                bot, "firebase_get", side_effect=lambda path:
                {"202": {"display": "digest"}} if path == "prefs" else {}):
            restored = bot.State()
        self.assertEqual(restored.get_display(202), "digest")

    async def test_manual_digest_has_links_and_no_media(self):
        bot.state.set_display(101, "digest")
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=self.games)), \
             patch.object(bot, "tg_api", AsyncMock(return_value={"ok": True})) as api, \
             patch.object(bot, "send_game", AsyncMock()) as cards:
            await bot._handle_giochi(101)
        cards.assert_not_called()
        api.assert_awaited_once()
        text = api.call_args.kwargs["text"]
        self.assertIn("PC", text)
        self.assertIn("Game &lt;0&gt;", text)
        self.assertIn('href="https://store.example/0"', text)
        self.assertEqual(bot.state.sent, {"already_seen"})

    async def test_broadcast_keeps_failed_digest_pending_without_repeating_success(self):
        bot.state.chats = {101, 202}
        for chat in bot.state.chats:
            bot.state.set_display(chat, "digest")
        async def telegram(method, **params):
            return {"ok": False, "description": "Temporary failure"} if params["chat_id"] == 202 else {"ok": True}
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=self.games)), \
             patch.object(bot, "tg_api", AsyncMock(side_effect=telegram)):
            await bot.broadcast_new_games()
        self.assertNotIn("game_0", bot.state.sent)
        self.assertEqual(bot.state.deliveries["game_0"], {101})
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=self.games)), \
             patch.object(bot, "tg_api", AsyncMock(return_value={"ok": True})) as api:
            await bot.broadcast_new_games()
        api.assert_awaited_once()
        self.assertEqual(api.call_args.kwargs["chat_id"], 202)
        self.assertTrue({g["id"] for g in self.games} <= bot.state.sent)

    async def test_digest_groups_devices_and_splits_long_lists(self):
        bot.state.set_display(101, "digest")
        bot.state.set_prefs(101, {"pc", "android"})
        games = [dict(self.games[0], id=str(i), title="Long game name " * 10)
                 for i in range(70)]
        games.append(dict(self.games[0], id="mobile", categories=["android"]))
        units = bot.delivery_units(101, games)
        self.assertGreater(len(units), 2)
        self.assertTrue(all(len(text) <= 3900 for _, text in units))
        self.assertEqual(sum(len(batch) for batch, _ in units), 71)
        self.assertIn("Android / iOS", units[-1][1])

    async def test_command_and_callback_change_only_requesting_chat(self):
        reply = bot.handle_update({"message": {"chat": {"id": 101}, "text": "/formato"}})
        self.assertEqual(reply["chat_id"], 101)
        with patch.object(bot, "_save_display", AsyncMock()) as save:
            bot.handle_update({"callback_query": {"id": "cb", "data": "display:digest",
                "message": {"chat": {"id": 101}, "message_id": 5}}})
            await asyncio.sleep(0)
        save.assert_awaited_once_with(101, 5, "digest")

    async def test_digest_orders_by_store_then_deadline_without_year(self):
        bot.state.set_display(101, "digest")
        games = [dict(self.games[0], id="late", title="Later (Steam)", end_date="2026-10-09"),
                 dict(self.games[0], id="unknown", title="Unknown (Steam)", end_date="N/A"),
                 dict(self.games[0], id="early", title="Earlier (Steam)", end_date="2026-10-05"),
                 dict(self.games[0], id="gog", title="GOG game", end_date="2026-10-08"),
                 dict(self.games[0], id="prime", title="Prime game", source="Amazon Prime Gaming",
                      content_type="subscription")]
        units = bot.delivery_units(101, games)
        self.assertEqual([g["id"] for g in units[0][0]], ["prime", "gog", "early", "late", "unknown"])
        text = units[0][1]
        self.assertIn("<b>Amazon Prime</b>", text)
        self.assertNotIn("💳 Amazon Prime", text)
        self.assertNotIn("Scadenza non comunicata", text)
        self.assertNotIn("Abbonamento", text)
        self.assertNotIn("2026", text)
        self.assertIn("5 Ottobre", text)
        self.assertIn("<b>Steam</b>", text)

    def test_deadline_order_retains_year_internally(self):
        older = dict(self.games[0], title="Same (Steam)", end_date="2026-12-31")
        newer = dict(older, end_date="2027-01-01")
        self.assertLess(bot.game_sort_key(older), bot.game_sort_key(newer))
        self.assertEqual(bot.format_date_it("2026-10-05T12:00:00Z"), "5 Ottobre")
