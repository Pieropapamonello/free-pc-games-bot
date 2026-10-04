"""Private Telegram admin sessions; password verifier only, no plaintext secret."""
import hashlib
import hmac
import os
import threading
import time

DEFAULT_VERIFIER = "4ced12f7dd9b91e51b686d35d576edcb:626ad5e6021b91e702ac695c356f5c9e93fd2bad8b4424c99cd8c06a0c017de7"


class AdminAuth:
    def __init__(self):
        self.pending = {}
        self.sessions = {}
        self.attempts = {}
        self.global_attempts = []
        self.lock = threading.Lock()

    def begin(self, user):
        now = time.monotonic()
        with self.lock:
            self.pending = {key: expiry for key, expiry in self.pending.items() if expiry > now}
            if len(self.pending) >= 1024:
                self.pending.pop(next(iter(self.pending)))
            self.pending[user] = now + 300

    def waiting(self, user):
        return self.pending.get(user, 0) > time.monotonic()

    def active(self, user):
        return self.sessions.get(user, 0) > time.monotonic()

    def logout(self, user):
        with self.lock:
            self.sessions.pop(user, None)
            self.pending.pop(user, None)

    def verify(self, user, password):
        now = time.monotonic()
        with self.lock:
            if not self.waiting(user):
                return "expired"
            self.attempts = {key: [stamp for stamp in stamps if now - stamp < 900]
                             for key, stamps in self.attempts.items() if stamps and now - stamps[-1] < 900}
            self.global_attempts = [stamp for stamp in self.global_attempts if now - stamp < 60]
            stamps = self.attempts.setdefault(user, [])
            if len(stamps) >= 5 or len(self.global_attempts) >= 20:
                return "limited"
            stamps.append(now)
            self.global_attempts.append(now)
        try:
            salt, expected = os.getenv("ADMIN_PASSWORD_HASH", DEFAULT_VERIFIER).split(":")
            computed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200000).hex()
            accepted = len(password) <= 256 and hmac.compare_digest(computed, expected)
        except (ValueError, TypeError):
            accepted = False
        with self.lock:
            if accepted and self.waiting(user):
                self.pending.pop(user, None)
                self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
                self.sessions[user] = now + 3600
                return "ok"
        return "wrong"
