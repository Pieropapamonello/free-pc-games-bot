import unittest
from unittest.mock import AsyncMock, patch

import test_start

bot = test_start.bot


class SourceTests(unittest.IsolatedAsyncioTestCase):
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
                 "fetch_mmobomb_giveaways", "fetch_itch_promotions", "fetch_indiegala_freebies"]
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
        mocks["fetch_cheapshark"].return_value = [dict(game, id="cheap_1"),
                                        dict(game, id="cheap_2", title="Another")]
        games = await bot.fetch_all_games()
        self.assertEqual([g["id"] for g in games], ["epic_example", "cheap_2"])


class CatalogNotificationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_start.StartRoutingTests.asyncSetUp
    async def test_free_to_play_catalog_does_not_generate_notifications(self):
        old = dict(self.games[0], id="ftg_1", catalog="freetogame", access_model="free_to_play")
        new = dict(old, id="ftg_2", title="New title")
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=[old, new])), \
             patch.object(bot, "send_game", AsyncMock()) as send:
            await bot.broadcast_new_games()
            send.assert_not_awaited()

    async def test_manual_lists_exclude_f2p_but_keep_paid_game_giveaways(self):
        free = dict(self.games[0], id="permanent", access_model="free_to_play")
        promo = dict(self.games[0], id="promotion")
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=[free, promo])), \
             patch.object(bot, "send_game", AsyncMock(return_value=True)) as send:
            await bot._handle_giochi(101)
        send.assert_awaited_once_with(101, promo)

    def test_f2p_dlc_rewards_remain_available_when_enabled(self):
        items = [{"title": "Example (Free-to-play)", "content_type": "game"},
                 {"title": "Example F2P Pack", "content_type": "dlc"},
                 {"title": "Paid game giveaway", "content_type": "game"}]
        result = bot.filter_by_content(items, {"game", "dlc"})
        self.assertEqual(result, items[1:])


class CardTests(unittest.TestCase):
    def test_expired_dates_removed_but_unknown_deadlines_kept(self):
        games = [{"end_date": value} for value in ("2000-01-01", "01/01/2000 12:00 UTC", "2999-01-01", "N/A", "")]
        self.assertEqual(bot.filter_by_content(games, {"game"}), games[2:])

    def test_prime_slug_title_capitalized_without_store_suffix(self):
        self.assertEqual(bot._title_from_slug("five-nights-at-freddys-epic"), "Five Nights At Freddys")

    def test_compact_card_has_short_description_icons_and_no_hashtags(self):
        with patch.object(bot, "translate_it", return_value="Un gioco di avventura e azione. " * 20):
            description = bot.compact_description("text")
            card = bot.format_game({"title": "Example", "description": "text",
                                    "source": "Steam", "url": "https://store.steampowered.com/"})
        self.assertLessEqual(len(description), 280)
        self.assertNotIn("\n", description)
        self.assertIn("🎁 GRATIS SU PC", card)
        self.assertIn(">EXAMPLE</a></b>\n\n", card)
        self.assertIn("https://wa.me/?text=", card)
        self.assertIn("Cerca gameplay", card)
        self.assertIn('Scarica da:</b> <a href=', card)
        self.assertNotIn("#", card)
        self.assertNotIn("Valore", card)

    def test_subscription_requirement_is_retained(self):
        card = bot.format_game({"title": "Example", "source": "Amazon Prime Gaming"})
        self.assertIn("Richiede un abbonamento attivo Amazon Prime", card)

    def test_links_escape_html_and_reject_non_web_urls(self):
        self.assertIn("&quot;", bot.text_link('https://example.com/?q="x"', "Example"))
        self.assertNotIn("<a", bot.text_link("javascript:alert(1)", "Example"))
