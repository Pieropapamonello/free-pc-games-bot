import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_start import bot


def response(data):
    result = AsyncMock()
    result.__aenter__.return_value = result
    result.json.return_value = data
    return result


class SteamLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_title_wins_over_earlier_fuzzy_match(self):
        session = Mock()
        session.get.return_value = response({"items": [
            {"id": 2, "name": "Example 2"}, {"id": 1, "name": "Example"}]})
        self.assertEqual(await bot._steam_search_one(session, "Example & Friends", "example"), 1)
        self.assertEqual(session.get.call_args.kwargs["params"]["term"], "Example & Friends")

    async def test_expired_failed_lookup_retries_and_recovers(self):
        session = Mock()
        session.get.return_value = response({"1": {"data": {"name": "Example", "movies": []}}})
        with patch.object(bot, "_steam_cache", {"example": (10, None)}), \
             patch.object(bot.time, "monotonic", return_value=11), \
             patch.object(bot, "get_session", AsyncMock(return_value=session)), \
             patch.object(bot, "_steam_search_one", AsyncMock(return_value=1)):
            info = await bot.steam_lookup("Example")
        self.assertTrue(info["official_match"])

    async def test_recent_failure_is_cached_briefly(self):
        with patch.object(bot, "_steam_cache", {"example": (10, None)}), \
             patch.object(bot.time, "monotonic", return_value=9), \
             patch.object(bot, "get_session", AsyncMock()) as session:
            self.assertIsNone(await bot.steam_lookup("Example"))
        session.assert_not_awaited()
