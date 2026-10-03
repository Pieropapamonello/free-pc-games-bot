import hashlib
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_start

bot = test_start.bot


class TranslationConcurrencyTests(unittest.TestCase):
    def test_slow_provider_does_not_block_cached_card(self):
        entered, release = threading.Event(), threading.Event()
        key = hashlib.sha256(b"Cached description").hexdigest()
        def slow(text):
            entered.set()
            release.wait(3)
            return "Descrizione tradotta."
        with patch.object(bot, "_translation_cache", {key: "Descrizione salvata."}), patch.object(bot, "_language", return_value="en"), patch.object(bot, "_valid_translation", return_value=True), patch.object(bot, "_google_translation", slow), patch.object(bot, "_persist_translation"), ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(bot.translate_it, "Uncached description")
            try:
                self.assertTrue(entered.wait(1))
                cached = workers.submit(bot.translate_it, "Cached description")
                self.assertEqual(cached.result(timeout=1), "Descrizione salvata.")
            finally:
                release.set()
            self.assertEqual(first.result(timeout=1), "Descrizione tradotta.")
