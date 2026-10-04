import hashlib
import time
import unittest
from unittest.mock import AsyncMock, patch

from admin_auth import AdminAuth
import test_start

bot = test_start.bot


class AdminAuthTests(unittest.TestCase):
    def setUp(self):
        self.auth = AdminAuth()
        salt = '00' * 16
        verifier = salt + ':' + hashlib.pbkdf2_hmac('sha256', b'test-password', bytes.fromhex(salt), 200000).hex()
        setting = patch.dict('os.environ', {'ADMIN_PASSWORD_HASH': verifier})
        setting.start()
        self.addCleanup(setting.stop)

    def test_password_session_is_scoped_expires_and_logs_out(self):
        self.assertEqual(self.auth.verify(101, 'test-password'), 'expired')
        self.auth.begin(101)
        self.assertEqual(self.auth.verify(101, 'test-password'), 'ok')
        self.assertTrue(self.auth.active(101))
        self.assertFalse(self.auth.active(202))
        with patch('admin_auth.time.monotonic', return_value=time.monotonic() + 3601):
            self.assertFalse(self.auth.active(101))
        self.auth.logout(101)
        self.assertFalse(self.auth.active(101))

    def test_rate_limit_cannot_be_reset_by_reopening_login(self):
        self.auth.begin(101)
        for _ in range(5):
            self.assertEqual(self.auth.verify(101, 'wrong'), 'wrong')
        self.auth.begin(101)
        self.assertEqual(self.auth.verify(101, 'test-password'), 'limited')
        self.assertFalse(self.auth.active(101))

    def test_callbacks_recheck_session_and_chat(self):
        self.auth.sessions[101] = time.monotonic() + 60
        callback = {'callback_query': {'id': 'cb', 'from': {'id': 101}, 'data': 'admin:diagnostics',
                    'message': {'message_id': 1, 'chat': {'id': 101, 'type': 'private'}}}}
        with patch.object(bot, 'admin_auth', self.auth), patch.object(bot, 'ADMIN_USER_IDS', set()):
            self.assertIn('Diagnostica', bot.handle_update(callback)['text'])
            callback['callback_query']['message']['chat'] = {'id': -1, 'type': 'group'}
            self.assertEqual(bot.handle_update(callback)['method'], 'answerCallbackQuery')
            callback['callback_query']['message']['chat'] = {'id': 101, 'type': 'private'}
            self.auth.logout(101)
            self.assertEqual(bot.handle_update(callback)['method'], 'answerCallbackQuery')


class LoginDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_password_message_is_deleted_and_never_echoed(self):
        with patch.object(bot, 'tg_api', AsyncMock(return_value={'ok': True})) as api, patch.object(bot.admin_auth, 'verify', return_value='ok'):
            await bot.admin_login({'chat': {'id': 101}, 'message_id': 55}, 'test-password')
        self.assertEqual(api.await_args_list[0].args[0], 'deleteMessage')
        self.assertNotIn('test-password', str(api.await_args_list))
