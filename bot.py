import os
import json
import logging
import asyncio
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from deep_translator import GoogleTranslator

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
POLL_MINUTES = int(os.getenv("POLL_MINUTES", "60"))
HTTP_PORT = int(os.getenv("PORT", "7860"))
PING_SECRET = os.getenv("PING_SECRET", "")
USD_TO_EUR = float(os.getenv("USD_TO_EUR", "0.92"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") or secrets.token_urlsafe(24)
FIREBASE_URL = os.getenv("FIREBASE_URL", "").rstrip("/")
FIREBASE_SECRET = os.getenv("FIREBASE_SECRET", "")

DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).parent / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHATS_FILE = DATA_DIR / "chats.json"
SENT_FILE = DATA_DIR / "sent_games.json"

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

MESI_IT = [
    "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("freegamesbot")


def load_json_set(path: Path) -> set:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_json_set(path: Path, data: set) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(list(data), f, ensure_ascii=False, indent=2)


def _firebase_url(path: str) -> str:
    return f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"


def firebase_get(path: str) -> dict:
    import urllib.request
    req = urllib.request.Request(_firebase_url(path), method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        data = r.read()
        return json.loads(data) if data else {}


def firebase_put(path: str, value) -> None:
    import urllib.request
    body = json.dumps(value).encode("utf-8")
    req = urllib.request.Request(
        _firebase_url(path),
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def firebase_patch(path: str, value: dict) -> None:
    import urllib.request
    body = json.dumps(value).encode("utf-8")
    req = urllib.request.Request(
        _firebase_url(path),
        data=body,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def firebase_delete(path: str) -> None:
    import urllib.request
    req = urllib.request.Request(_firebase_url(path), method="DELETE")
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def normalize_title(title: str) -> str:
    t = re.sub(r"\s*giveaway.*$", "", title, flags=re.IGNORECASE)
    t = re.sub(r"\s*\([^)]*\)\s*", "", t)
    t = re.sub(r"[^\w\s]", "", t.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def translate_it(text: str) -> str:
    if not text:
        return text
    t = text.strip()
    if len(t) < 5:
        return t
    try:
        out = GoogleTranslator(source="auto", target="it").translate(t)
        return (out or t).strip()
    except Exception as e:
        log.warning("Traduzione fallita: %s", e)
        return t


def format_price_eur(worth) -> Optional[str]:
    if not worth or worth in ("N/A", "", None):
        return None
    m = re.search(r"[\d.,]+", str(worth))
    if not m:
        return str(worth)
    try:
        n = float(m.group(0).replace(",", ""))
        if n <= 0:
            return None
        return f"€{n * USD_TO_EUR:.2f}"
    except Exception:
        return str(worth)


def format_date_it(s) -> Optional[str]:
    if not s or s == "N/D":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M UTC", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return f"{dt.day} {MESI_IT[dt.month - 1]} {dt.year}"
        except ValueError:
            continue
    return s


USE_FIREBASE = bool(FIREBASE_URL and FIREBASE_SECRET)


class State:
    def __init__(self):
        self.chats: set[int] = set()
        self.sent: set[str] = set()
        self._load()

    def _load(self):
        if USE_FIREBASE:
            try:
                chats = firebase_get("chats") or {}
                sent = firebase_get("sent") or {}
                self.chats = {int(k) for k in chats.keys()} if isinstance(chats, dict) else set()
                self.sent = set(sent.keys()) if isinstance(sent, dict) else set()
                log.info("Stato caricato da Firebase: %d chat, %d giochi inviati", len(self.chats), len(self.sent))
                return
            except Exception as e:
                log.error("Firebase load fallita: %s — fallback a file", e)
        self.chats = {int(x) for x in load_json_set(CHATS_FILE)}
        self.sent = load_json_set(SENT_FILE)

    def _save_chats(self):
        if USE_FIREBASE:
            try:
                firebase_put("chats", {str(c): True for c in self.chats})
                return
            except Exception as e:
                log.warning("Firebase save chats fallita: %s", e)
        save_json_set(CHATS_FILE, self.chats)

    def _save_sent(self):
        if USE_FIREBASE:
            try:
                firebase_put("sent", {str(s): True for s in self.sent})
                return
            except Exception as e:
                log.warning("Firebase save sent fallita: %s", e)
        save_json_set(SENT_FILE, self.sent)

    def save(self):
        self._save_chats()
        self._save_sent()

    def subscribe(self, chat_id: int) -> bool:
        if chat_id in self.chats:
            return False
        self.chats.add(chat_id)
        if USE_FIREBASE:
            try:
                firebase_patch("chats", {str(chat_id): True})
                return True
            except Exception as e:
                log.warning("Firebase patch chat fallita: %s", e)
        self._save_chats()
        return True

    def unsubscribe(self, chat_id: int) -> bool:
        if chat_id not in self.chats:
            return False
        self.chats.discard(chat_id)
        if USE_FIREBASE:
            try:
                firebase_delete(f"chats/{chat_id}")
                return True
            except Exception as e:
                log.warning("Firebase delete chat fallita: %s", e)
        self._save_chats()
        return True

    def mark_sent(self, game_ids: list[str]):
        if not game_ids:
            return
        for gid in game_ids:
            self.sent.add(gid)
        if USE_FIREBASE:
            try:
                firebase_patch("sent", {str(g): True for g in game_ids})
                return
            except Exception as e:
                log.warning("Firebase patch sent fallita: %s", e)
        self._save_sent()


state = State()
_session: Optional[aiohttp.ClientSession] = None


def _connector() -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(
        limit=20,
        force_close=True,
        enable_cleanup_closed=True,
        ttl_dns_cache=60,
    )


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=30, connect=15)
        _session = aiohttp.ClientSession(timeout=timeout, connector=_connector())
    return _session


async def tg_api(method: str, **params) -> dict:
    sess = await get_session()
    url = f"{API}/{method}"
    for attempt in range(1, 4):
        try:
            async with sess.post(url, json=params) as r:
                data = await r.json()
                if not data.get("ok"):
                    log.warning("API %s failed: %s", method, data)
                return data
        except Exception as e:
            log.warning("tg_api %s tentativo %d fallito: %s", method, attempt, e)
            if attempt == 3:
                raise
            await asyncio.sleep(2 * attempt)
    return {"ok": False}


async def fetch_json(url: str) -> Any:
    sess = await get_session()
    async with sess.get(url, headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        return await r.json(content_type=None)


GAMERPOWER_PLATFORMS = ["pc", "epic-games-store", "steam", "gog", "ubisoft", "drm-free", "amazon"]


async def _fetch_gamerpower_one(platform: str) -> list[dict]:
    try:
        data = await fetch_json(
            f"https://www.gamerpower.com/api/giveaways?platform={platform}&type=game"
        )
    except Exception as e:
        log.warning("GamerPower fetch %s fallita: %s", platform, e)
        return []
    games = []
    for it in data:
        if it.get("status", "").lower() != "active":
            continue
        title = (it.get("title") or "").strip()
        if not title:
            continue
        platforms_str = it.get("platforms", "PC") or "PC"
        is_prime = "amazon" in platforms_str.lower() or "prime" in platforms_str.lower()
        source = "Amazon Prime Gaming" if is_prime else "GamerPower"
        games.append({
            "id": f"gp_{normalize_title(title)}",
            "title": title,
            "description": (it.get("description") or "").strip(),
            "url": it.get("open_giveaway_url") or it.get("gamerpower_url") or "",
            "image": it.get("image") or it.get("thumbnail") or "",
            "platform": platforms_str,
            "end_date": it.get("end_date", "N/D"),
            "source": source,
            "worth": it.get("worth", "N/A"),
            "translate": True,
        })
    return games


async def fetch_gamerpower_pc() -> list[dict]:
    results = await asyncio.gather(*[_fetch_gamerpower_one(p) for p in GAMERPOWER_PLATFORMS])
    out, seen = [], set()
    for batch in results:
        for g in batch:
            key = normalize_title(g["title"])
            if key in seen:
                continue
            seen.add(key)
            out.append(g)
    return out


async def fetch_epic_free() -> list[dict]:
    url = (
        "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
        "?locale=it-IT&country=IT&allowCountries=IT"
    )
    try:
        data = await fetch_json(url)
    except Exception as e:
        log.warning("Epic fetch fallita: %s", e)
        return []
    games = []
    elements = (
        data.get("data", {})
        .get("Catalog", {})
        .get("searchStore", {})
        .get("elements", [])
    )
    now = datetime.now(timezone.utc)
    for el in elements:
        promos = (el.get("promotions") or {}).get("promotionalOffers") or []
        active = False
        end_date = "N/D"
        for p in promos:
            for off in p.get("promotionalOffers", []):
                try:
                    start = datetime.fromisoformat(off["startDate"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(off["endDate"].replace("Z", "+00:00"))
                except Exception:
                    continue
                disc = (off.get("discountSetting") or {}).get("discountPercentage", 100)
                if start <= now <= end and disc == 0:
                    active = True
                    end_date = end.strftime("%d/%m/%Y %H:%M UTC")
        if not active:
            continue
        title = (el.get("title") or "").strip()
        if not title:
            continue
        slug = ""
        mappings = el.get("catalogNs", {}).get("mappings") or []
        if mappings:
            slug = mappings[0].get("pageSlug", "")
        if not slug:
            slug = el.get("productSlug") or el.get("urlSlug") or ""
        slug = slug.split("/")[0] if slug else ""
        url_game = f"https://store.epicgames.com/it/p/{slug}" if slug else "https://store.epicgames.com/it/free-games"
        image = ""
        for img in el.get("keyImages", []):
            if img.get("type") in ("OfferImageWide", "DieselStoreFrontWide", "Thumbnail"):
                image = img.get("url", "")
                break
        games.append({
            "id": f"epic_{normalize_title(title)}",
            "title": title,
            "description": (el.get("description") or "").strip(),
            "url": url_game,
            "image": image,
            "platform": "PC (Epic Games)",
            "end_date": end_date,
            "source": "Epic Games",
            "worth": "N/A",
            "translate": False,
        })
    return games


async def fetch_all_games() -> list[dict]:
    epic, gp = await asyncio.gather(fetch_epic_free(), fetch_gamerpower_pc())
    seen, unique = set(), []
    for g in epic + gp:
        key = normalize_title(g["title"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(g)
    return unique


def md_escape(text: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", text)


def format_game(g: dict) -> str:
    title = g["title"]
    desc = g.get("description", "") or ""
    if g.get("translate", True):
        desc = translate_it(desc)
    if len(desc) > 400:
        desc = desc[:397] + "..."
    parts = [f"🎮 *{title}*"]
    if desc:
        parts.append(f"_{desc}_")
    parts.append(f"🏢 {g.get('source','?')} • 💻 {g.get('platform','PC')}")
    price = format_price_eur(g.get("worth"))
    if price:
        parts.append(f"💰 Valore: {price}")
    date = format_date_it(g.get("end_date"))
    if date:
        parts.append(f"⏰ Riscatta entro: {date}")
    if g.get("url"):
        parts.append(f"▶️ [Scarica Gratis]({g['url']})")
    return "\n".join(parts)


async def send_game(chat_id: int, g: dict):
    caption = format_game(g)
    if g.get("image"):
        try:
            await tg_api("sendPhoto", chat_id=chat_id, photo=g["image"], caption=caption, parse_mode="Markdown")
            return
        except Exception as e:
            log.warning("sendPhoto fallita, fallback testo: %s", e)
    await tg_api("sendMessage", chat_id=chat_id, text=caption, parse_mode="Markdown", disable_web_page_preview=False)


WELCOME_NEW = (
    "✅ *Iscrizione attivata!*\n\n"
    "Riceverai qui ogni nuovo gioco PC gratuito appena disponibile "
    "(Epic Games, GamerPower e altri).\n\n"
    "Comandi:\n"
    "• /giochi – mostra giochi gratis ora\n"
    "• /status – stato iscrizione\n"
    "• /stop – disiscriviti"
)
WELCOME_ALREADY = "ℹ️ Questa chat è già iscritta. Usa /stop per disattivare o /giochi per vedere quelli attuali."


def handle_update(update: dict) -> Optional[dict]:
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if msg:
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        text = (msg.get("text") or "").strip()
        new_members = msg.get("new_chat_members") or []
        if new_members:
            for m in new_members:
                if m.get("username", "").lower() == "giochipcgratisbot":
                    state.subscribe(chat_id)
                    return {
                        "method": "sendMessage",
                        "chat_id": chat_id,
                        "text": "👋 Ciao! Riceverete qui notifiche sui giochi PC gratuiti. Usa /giochi o /stop.",
                    }
            return None
        if not chat_id or not text:
            return None
        cmd = text.split()[0].lower().split("@")[0]
        if cmd == "/start":
            added = state.subscribe(chat_id)
            return {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": WELCOME_NEW if added else WELCOME_ALREADY,
                "parse_mode": "Markdown",
            }
        if cmd == "/stop":
            ok = state.unsubscribe(chat_id)
            return {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": "🛑 Disiscritto. Non riceverai più notifiche. /start per riattivare." if ok else "Questa chat non era iscritta.",
            }
        if cmd == "/status":
            sub = chat_id in state.chats
            return {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": (
                    "📊 Stato\n"
                    f"• Questa chat: {'iscritta ✅' if sub else 'non iscritta ❌'}\n"
                    f"• Chat totali iscritte: {len(state.chats)}\n"
                    f"• Intervallo controllo: ogni {POLL_MINUTES} min"
                ),
            }
        if cmd == "/giochi":
            asyncio.create_task(_handle_giochi(chat_id))
            return {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": "🔎 Cerco giochi gratuiti…",
            }
        return None
    cmu = update.get("my_chat_member")
    if cmu:
        chat = cmu.get("chat") or {}
        chat_id = chat.get("id")
        new_status = (cmu.get("new_chat_member") or {}).get("status")
        if new_status in ("member", "administrator"):
            added = state.subscribe(chat_id)
            if added:
                return {
                    "method": "sendMessage",
                    "chat_id": chat_id,
                    "text": (
                        "👋 Ciao! Da ora questa chat riceverà notifiche sui giochi PC gratuiti "
                        "(Epic Games, GamerPower e altri).\n\n"
                        "Usa /giochi per vederli ora, /stop per disattivare."
                    ),
                }
        elif new_status in ("left", "kicked"):
            state.unsubscribe(chat_id)
    return None


async def _handle_giochi(chat_id: int):
    try:
        games = await fetch_all_games()
        if not games:
            await tg_api("sendMessage", chat_id=chat_id, text="Nessun gioco gratuito trovato al momento.")
            return
        for g in games[:10]:
            try:
                await send_game(chat_id, g)
            except Exception as e:
                log.warning("send_game fallito: %s", e)
    except Exception as e:
        log.exception("Errore /giochi: %s", e)


async def broadcast_new_games():
    try:
        log.info("Controllo giochi gratuiti…")
        games = await fetch_all_games()
        new = [g for g in games if g["id"] not in state.sent]
        if not new:
            log.info("Nessun nuovo gioco.")
            return
        log.info("Trovati %d nuovi giochi, invio a %d chat", len(new), len(state.chats))
        dead = []
        for chat_id in list(state.chats):
            for g in new:
                try:
                    await send_game(chat_id, g)
                    await asyncio.sleep(0.3)
                except Exception as e:
                    msg = str(e).lower()
                    if any(k in msg for k in ("forbidden", "kicked", "blocked", "deactivated", "chat not found")):
                        dead.append(chat_id)
                        break
                    log.warning("Errore invio chat %s: %s", chat_id, e)
        for cid in dead:
            state.unsubscribe(cid)
        state.mark_sent([g["id"] for g in new])
    except Exception as e:
        log.exception("Errore broadcast: %s", e)


async def periodic_broadcaster():
    await asyncio.sleep(15)
    while True:
        await broadcast_new_games()
        await asyncio.sleep(POLL_MINUTES * 60)


async def webhook_handler(request: web.Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != WEBHOOK_SECRET:
        return web.Response(status=401, text="unauthorized")
    try:
        update = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")
    try:
        reply = handle_update(update)
    except Exception as e:
        log.exception("handle_update errore: %s", e)
        reply = None
    if reply:
        return web.json_response(reply)
    return web.Response(text="ok")


async def health_handler(request: web.Request):
    return web.json_response({
        "status": "ok",
        "chats": len(state.chats),
        "sent": len(state.sent),
        "poll_minutes": POLL_MINUTES,
        "webhook_set": bool(PUBLIC_BASE_URL),
    })


async def ping_handler(request: web.Request):
    if PING_SECRET and request.query.get("secret", "") != PING_SECRET:
        return web.Response(status=401, text="unauthorized")
    asyncio.create_task(broadcast_new_games())
    return web.Response(text="triggered")


async def setup_webhook(app: web.Application):
    if not PUBLIC_BASE_URL:
        log.warning("PUBLIC_BASE_URL non impostato — il webhook NON sarà registrato automaticamente. Imposta la variabile a https://USER-SPACE.hf.space")
        return
    url = f"{PUBLIC_BASE_URL.rstrip('/')}/webhook"
    try:
        resp = await tg_api(
            "setWebhook",
            url=url,
            secret_token=WEBHOOK_SECRET,
            allowed_updates=["message", "edited_message", "channel_post", "my_chat_member"],
            drop_pending_updates=False,
        )
        log.info("setWebhook: %s -> %s", url, resp)
    except Exception as e:
        log.error("setWebhook fallita: %s", e)


async def on_startup(app: web.Application):
    app["broadcaster"] = asyncio.create_task(periodic_broadcaster())
    asyncio.create_task(setup_webhook(app))
    log.info("Bot avviato. Chat iscritte: %d", len(state.chats))


async def on_cleanup(app: web.Application):
    t = app.get("broadcaster")
    if t:
        t.cancel()
    if _session and not _session.closed:
        await _session.close()


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/ping", ping_handler)
    app.router.add_post("/webhook", webhook_handler)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN mancante. Imposta variabile d'ambiente BOT_TOKEN.")
    web.run_app(build_app(), host="0.0.0.0", port=HTTP_PORT, access_log=None)


if __name__ == "__main__":
    main()
