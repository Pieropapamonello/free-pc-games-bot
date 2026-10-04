import unittest
from unittest.mock import patch

import test_start
from trailers import TrailerService

bot = test_start.bot


class DiagnosticsTests(unittest.TestCase):
    def message(self, sender=101, chat=101, kind="private", **extra):
        return {"message": {"from": {"id": sender}, "chat": {"id": chat, "type": kind}, "text": "/diagnostica", **extra}}

    def test_only_allowlisted_account_in_private_chat_can_read_events(self):
        service = TrailerService()
        service.report("Example game", "Preparazione video fallita", TimeoutError("SECRET_URL_TOKEN"))
        with patch.object(bot, "ADMIN_USER_IDS", {101}), patch.object(bot, "trailer_service", service):
            result = bot.handle_update(self.message())
            self.assertIn("TimeoutError", result["text"])
            self.assertNotIn("SECRET_URL_TOKEN", result["text"])
            for message in (self.message(202, 202), self.message(202, 101), self.message(101, -100, "supergroup"), self.message(sender_chat={"id": 101}), {"message": {"chat": {"id": 101, "type": "private"}, "text": "/diagnostica"}}):
                result = bot.handle_update(message)
                self.assertNotIn("Example game", result["text"])
                self.assertIn("riservato", result["text"])

    def test_empty_admin_configuration_denies_access(self):
        with patch.object(bot, "ADMIN_USER_IDS", set()):
            self.assertIn("riservato", bot.handle_update(self.message())["text"])

    def test_events_are_bounded(self):
        service = TrailerService()
        for index in range(100):
            service.report(str(index), "In coda")
        self.assertEqual(len(service.diagnostics), 40)
