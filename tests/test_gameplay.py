import json
import unittest
from unittest.mock import AsyncMock, patch

import trailers
from test_start import bot


class GameplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_italian_gameplay_preferred_over_english_and_other_games(self):
        entries = [{"id": "abcdefghijk", "title": "Example gameplay English"},
                   {"id": "12345678901", "title": "Other Game gameplay Italiano"},
                   {"id": "123456789ab", "title": "Example gameplay Italiano"}]
        service = trailers.TrailerService()
        with patch.object(trailers, "command", AsyncMock(return_value=json.dumps({"entries": entries}))) as command:
            result = await service.gameplay("Example")
            cached = await service.gameplay("Example")
        self.assertEqual(result["language"], "it")
        self.assertEqual(result["url"], "https://www.youtube.com/watch?v=123456789ab")
        self.assertEqual(cached, result)
        command.assert_awaited_once()

    async def test_any_language_fallback(self):
        with patch.object(trailers, "command", AsyncMock(return_value=json.dumps({"entries": [
                {"id": "abcdefghijk", "title": "Example gameplay", "language": "ja"}]}))):
            result = await trailers.TrailerService().gameplay("Example")
        self.assertEqual(result["url"], "https://www.youtube.com/watch?v=abcdefghijk")

    def test_card_links_title_to_whatsapp_and_gameplay_to_youtube(self):
        with patch.object(bot, "translate_it", return_value="Un gioco di avventura."):
            text = bot.format_game({"title": "Example", "description": "Adventure",
                "gameplay_url": "https://www.youtube.com/watch?v=abcdefghijk", "gameplay_language": "it"})
        self.assertIn("https://wa.me/?text=", text)
        self.assertIn(">EXAMPLE</a>", text)
        self.assertIn("Gameplay in italiano", text)
