import unittest
from unittest.mock import AsyncMock, patch

import test_start

bot = test_start.bot


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_freetogame_maps_browser_and_skips_future_releases(self):
        rows = [{"id": 1, "title": "Example", "game_url": "https://example.com",
                 "platform": "Web Browser", "release_date": "2020-01-01"},
                {"id": 2, "title": "Future", "game_url": "https://example.com",
                 "release_date": "2999-01-01"}]
        with patch.object(bot, "fetch_json", AsyncMock(return_value=rows)):
            games = await bot.fetch_freetogame()
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["access_model"], "free_to_play")
        self.assertEqual(games[0]["catalog"], "freetogame")

    async def test_cheapshark_only_accepts_zero_price_discounts(self):
        base = {"gameID": 1, "title": "Example", "dealID": "a%2Bb%3D",
                "salePrice": "0.00", "normalPrice": "9.99", "isOnSale": "1"}
        rows = [base, dict(base, salePrice="0.99"), dict(base, normalPrice="0"),
                dict(base, salePrice="invalid"), dict(base, isOnSale="0")]
        with patch.object(bot, "fetch_json", AsyncMock(return_value=rows)):
            games = await bot.fetch_cheapshark()
        self.assertEqual(len(games), 1)
        self.assertTrue(games[0]["url"].endswith("a%2Bb%3D"))

    async def test_mmobomb_excludes_exhausted_keys_and_beta(self):
        base = {"id": 1, "title": "Example Gift Pack", "keys_left": "41%",
                "giveaway_url": "https://www.mmobomb.com/giveaway/example"}
        rows = [base, dict(base, keys_left="0%"), dict(base, title="Example Beta Key"),
                dict(base, title="Unclear Giveaway")]
        with patch.object(bot, "fetch_json", AsyncMock(return_value=rows)):
            games = await bot.fetch_mmobomb_giveaways()
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["content_type"], "dlc")

    async def test_all_platforms_use_one_gamerpower_request(self):
        with patch.object(bot, "_fetch_gamerpower_one", AsyncMock(return_value=[])) as fetch:
            await bot.fetch_gamerpower_all()
        fetch.assert_awaited_once_with("", "game")

    async def test_failure_isolated_and_cross_source_titles_deduplicated(self):
        names = ["fetch_epic_free", "fetch_gamerpower_all", "fetch_reddit_all",
                 "fetch_prime_gaming", "fetch_gamerpower_loot", "fetch_cheapshark",
                 "fetch_mmobomb_giveaways", "fetch_freetogame"]
        mocks = {}
        for name in names:
            replacement = AsyncMock(return_value=[])
            patcher = patch.object(bot, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
            mocks[name] = replacement
        game = {"id": "epic_example", "title": "Example", "content_type": "game"}
        mocks[names[0]].return_value = [game]
        mocks[names[1]].side_effect = RuntimeError("source down")
        mocks[names[-1]].return_value = [dict(game, id="ftg_1"),
                                        dict(game, id="ftg_2", title="Another")]
        games = await bot.fetch_all_games()
        self.assertEqual([g["id"] for g in games], ["epic_example", "ftg_2"])


class CatalogNotificationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_start.StartRoutingTests.asyncSetUp
    async def test_catalog_baseline_is_silent_but_next_new_title_is_sent(self):
        old = dict(self.games[0], id="ftg_1", catalog="freetogame")
        new = dict(old, id="ftg_2", title="New title")
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=[old])) as fetch, \
             patch.object(bot, "send_game", AsyncMock()) as send:
            await bot.broadcast_new_games()
            send.assert_not_awaited()
            fetch.return_value = [old, new]
            await bot.broadcast_new_games()
            self.assertEqual(send.await_count, 3)
            self.assertTrue(all(c.args[1]["id"] == "ftg_2" for c in send.await_args_list))


class CardTests(unittest.TestCase):
    def test_compact_card_has_three_description_lines_and_no_hashtags(self):
        with patch.object(bot, "translate_it", return_value="Un gioco di avventura e azione. " * 20):
            description = bot.compact_description("text")
            card = bot.format_game({"title": "Example", "description": "text",
                                    "source": "Steam", "url": "https://store.steampowered.com/"})
        self.assertLessEqual(len(description.splitlines()), 3)
        self.assertTrue(all(len(line) <= 42 for line in description.splitlines()))
        self.assertIn("Piattaforma: PC", card)
        self.assertIn("Descrizione: ", card)
        self.assertIn('Scarica da: <a href=', card)
        self.assertNotIn("#", card)
        self.assertNotIn("Valore", card)

    def test_subscription_requirement_is_retained(self):
        card = bot.format_game({"title": "Example", "source": "Amazon Prime Gaming"})
        self.assertIn("Richiede un abbonamento attivo Amazon Prime", card)

    def test_links_escape_html_and_reject_non_web_urls(self):
        self.assertIn("&quot;", bot.text_link('https://example.com/?q="x"', "Example"))
        self.assertNotIn("<a", bot.text_link("javascript:alert(1)", "Example"))
