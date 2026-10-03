import unittest
from unittest.mock import AsyncMock, patch
import test_start

bot = test_start.bot


class ExtraSourceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_start.StartRoutingTests.asyncSetUp

    def test_itch_accepts_only_zero_price_100_percent_games_with_devices(self):
        def cell(name, discount, price):
            return f'''<div class="game_cell" data-game_id="42">
            <div class="game_title"><a class="title" href="https://author.itch.io/game">{name}</a>
            <a class="price_tag sale" href="/s/123/sale"><div class="price_value">{price}</div>
            <div class="sale_tag">{discount}</div></a></div><div class="game_text">An adventure.</div>
            <img data-lazy_src="https://img.itch.zone/banner.png"><span class="icon-windows8"></span></div>'''
        html = cell("Full Game", "-100%", "$0") + cell("Paid", "-50%", "$2") + cell("Demo", "-100%", "$0")
        games = bot.parse_itch_promotions(html)
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["title"], "Full Game")
        self.assertEqual(games[0]["categories"], ["pc"])
        self.assertEqual(games[0]["image"], "https://img.itch.zone/banner.png")

    async def test_indiegala_requires_normally_paid_exact_match(self):
        html = '''<div class="products-col-inner"><a class="fit-click" href="https://freebies.indiegala.com/example"></a>
        <div class="product-title">Example</div><img data-img-src="https://example.com/banner"></div>'''
        for info, expected in (({"official_match": True, "price": "0,00€"}, 0),
                               ({"official_match": False, "price": "9,99€"}, 0),
                               ({"official_match": True, "price": "9,99€"}, 1)):
            with self.subTest(info=info), patch.object(bot, "fetch_html", AsyncMock(side_effect=[html,
                    '<div class="developer-product-description">A puzzle game.</div> ADD TO LIBRARY'])), \
                 patch.object(bot, "steam_lookup", AsyncMock(return_value=info)):
                games = await bot.fetch_indiegala_freebies()
                self.assertEqual(len(games), expected)

    async def test_changed_source_does_not_repeat_completed_game(self):
        self.state = bot.state
        self.state.chats = {101}
        first = dict(self.games[0], id="original_source", title="Example (Steam)")
        first["dedupe_id"] = bot.game_dedupe_id(first)
        second = dict(first, id="other_source", title="EXAMPLE", source="IndieGala")
        self.assertEqual(bot.game_dedupe_id(first), bot.game_dedupe_id(second))
        with patch.object(bot, "fetch_all_games", AsyncMock(side_effect=[[first], [second]])), \
             patch.object(bot, "send_game", AsyncMock(return_value=True)) as send:
            await bot.broadcast_new_games()
            await bot.broadcast_new_games()
        send.assert_awaited_once()

    async def test_historical_gamerpower_notification_not_repeated_by_direct_source(self):
        game = dict(self.games[0], id="new_direct_source", title="Example (itch.io)")
        bot.state.sent.add("gp_game_example")
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=[game])), \
             patch.object(bot, "send_game", AsyncMock()) as send:
            await bot.broadcast_new_games()
        send.assert_not_awaited()

    async def test_partial_delivery_survives_source_change_without_duplicates(self):
        bot.state.chats = {101, 202}
        first = dict(self.games[0], id="first_source")
        first["dedupe_id"] = bot.game_dedupe_id(first)
        other = dict(first, id="second_source")
        async def first_send(chat, game):
            if chat == 202:
                raise RuntimeError("Temporary failure")
            return True
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=[first])), \
             patch.object(bot, "send_game", AsyncMock(side_effect=first_send)):
            await bot.broadcast_new_games()
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=[other])), \
             patch.object(bot, "send_game", AsyncMock(return_value=True)) as send:
            await bot.broadcast_new_games()
        send.assert_awaited_once_with(202, other)
