import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


# Import isolato: nessun accesso a Firebase o ai dati reali del bot.
with tempfile.TemporaryDirectory() as data_dir:
    with patch.dict(os.environ, {"DATA_DIR": data_dir, "FIREBASE_URL": "",
                                "FIREBASE_SECRET": ""}):
        with patch("dotenv.load_dotenv"):
            import bot


class StartRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_patch = patch.multiple(
            bot, USE_FIREBASE=False,
            CHATS_FILE=bot.Path(self.tmp.name) / "chats.json",
            SENT_FILE=bot.Path(self.tmp.name) / "sent.json",
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.state_patch = patch.object(bot, "state", bot.State())
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        bot.state.chats = {101, 202, -303}
        bot.state.sent = {"already_seen"}
        self.games = [{"id": "already_seen", "title": "Example",
                       "categories": ["pc"], "content_type": "game"}]

    async def test_start_sends_only_to_requesting_chat_and_preserves_history(self):
        for command in ("/start", "/start@giochipcgratisbot", "/start referral"):
            with self.subTest(command=command):
                with patch.object(bot, "fetch_all_games", AsyncMock(return_value=self.games)), \
                     patch.object(bot, "send_game", AsyncMock()) as send, \
                     patch.object(bot, "broadcast_new_games", AsyncMock()) as broadcast:
                    reply = bot.handle_update({"message": {
                        "chat": {"id": 404, "type": "private"}, "text": command}})
                    await asyncio.sleep(0)
                    self.assertEqual(reply["chat_id"], 404)
                    send.assert_awaited_once_with(404, self.games[0])
                    broadcast.assert_not_called()
                    self.assertEqual(bot.state.sent, {"already_seen"})
                    self.assertEqual(bot.state.chats, {101, 202, -303, 404})

    async def test_platform_menu_does_not_send_games(self):
        with patch.object(bot, "_handle_giochi", AsyncMock()) as games:
            reply = bot.handle_update({"message": {
                "chat": {"id": 101}, "text": "/piattaforme"}})
            await asyncio.sleep(0)
            self.assertEqual(reply["chat_id"], 101)
            games.assert_not_called()

    async def test_scheduled_notifications_still_reach_subscribed_chats(self):
        game = dict(self.games[0], id="new_game")
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=[game])), \
             patch.object(bot, "send_game", AsyncMock()) as send:
            await bot.broadcast_new_games()
            self.assertEqual({c.args[0] for c in send.await_args_list}, {101, 202, -303})
            self.assertEqual(send.await_count, 3)
            self.assertEqual(bot.state.sent, {"already_seen", "new_game"})


if __name__ == "__main__":
    unittest.main()
