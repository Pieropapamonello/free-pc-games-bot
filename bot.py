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

try:
    import yt_dlp
    _YTDLP_OK = True
except Exception:
    _YTDLP_OK = False

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
RAWG_KEY = os.getenv("RAWG_KEY", "")

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


ROMAN_TO_ARAB = [
    ("xii", "12"), ("xi", "11"), ("ix", "9"), ("viii", "8"),
    ("vii", "7"), ("vi", "6"), ("iv", "4"), ("iii", "3"),
    ("ii", "2"), ("v", "5"), ("x", "10"), ("i", "1"),
]


def _roman_to_arabic(s: str) -> str:
    for rom, ara in ROMAN_TO_ARAB:
        s = re.sub(rf"\b{rom}\b", ara, s)
    return s


_NOISE_WORDS = {
    "game", "free", "giveaway", "gratis", "gratuito", "mobile",
    "pc", "steam", "epic", "epic games", "epicgames", "epic games mobile",
    "gog", "ubisoft", "drm-free", "drm", "indiegala", "itch", "itchio",
    "android", "ios", "switch", "ps4", "ps5", "xbox", "playstation",
    "store", "key", "claim", "now", "psa", "fgf", "online", "edition",
    "standalone", "deluxe", "complete", "premium", "ultimate",
}


def normalize_title(title: str) -> str:
    t = re.sub(r"\s*giveaway.*$", "", title, flags=re.IGNORECASE)
    t = re.sub(r"\s*\([^)]*\)\s*", " ", t)
    t = re.sub(r"\s*\[[^\]]*\]\s*", " ", t)
    t = t.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = _roman_to_arabic(t)
    parts = [w for w in t.split() if w not in _NOISE_WORDS]
    t = " ".join(parts) if parts else t
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
    s = str(worth).strip()
    # già in euro (es. da Steam con cc=IT: "19,99€") -> restituisco com'è
    if "€" in s or "eur" in s.lower():
        return s.replace("EUR", "€").strip()
    m = re.search(r"[\d.,]+", s)
    if not m:
        return s
    try:
        n = float(m.group(0).replace(",", ""))
        if n <= 0:
            return None
        return f"€{n * USD_TO_EUR:.2f}"
    except Exception:
        return s


def format_date_it(s) -> Optional[str]:
    if not s or str(s).upper() in ("N/D", "N/A", "NONE", ""):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M UTC", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(str(s), fmt)
            return f"{dt.day} {MESI_IT[dt.month - 1]} {dt.year}"
        except ValueError:
            continue
    return str(s)


def clean_title(t: str) -> str:
    t = re.sub(r"\s+giveaway\s*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+\(steam giveaway\)\s*$", "", t, flags=re.IGNORECASE)
    return t.strip()


def hashtag(t: str) -> str:
    s = re.sub(r"[^\w]+", "_", t).strip("_")
    return f"#{s}" if s else ""


USE_FIREBASE = bool(FIREBASE_URL and FIREBASE_SECRET)


DEFAULT_CONTENT = {"game"}  # default: solo giochi gratis pieni (no DLC, no abbonamenti)


class State:
    def __init__(self):
        self.chats: set[int] = set()
        self.sent: set[str] = set()
        self.prefs: dict[int, set[str]] = {}
        self.genres_prefs: dict[int, set[str]] = {}
        self.content_prefs: dict[int, set[str]] = {}
        self._load()

    def _load(self):
        if USE_FIREBASE:
            try:
                chats = firebase_get("chats") or {}
                sent = firebase_get("sent") or {}
                prefs = firebase_get("prefs") or {}
                self.chats = set()
                self.prefs = {}
                if isinstance(chats, dict):
                    for k, v in chats.items():
                        cid = int(k)
                        self.chats.add(cid)
                        if isinstance(v, dict) and v.get("categories"):
                            self.prefs[cid] = set(v["categories"])
                self.genres_prefs = {}
                self.content_prefs = {}
                if isinstance(prefs, dict):
                    for k, v in prefs.items():
                        cid = int(k)
                        cats = v.get("categories") if isinstance(v, dict) else v
                        if isinstance(cats, list):
                            self.prefs[cid] = set(cats)
                        elif isinstance(cats, dict):
                            self.prefs[cid] = {c for c, ok in cats.items() if ok}
                        gens = v.get("genres") if isinstance(v, dict) else None
                        if isinstance(gens, list):
                            self.genres_prefs[cid] = set(gens)
                        cont = v.get("content") if isinstance(v, dict) else None
                        if isinstance(cont, list):
                            self.content_prefs[cid] = set(cont)
                self.sent = set(sent.keys()) if isinstance(sent, dict) else set()
                log.info("Stato caricato da Firebase: %d chat, %d sent, %d prefs", len(self.chats), len(self.sent), len(self.prefs))
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

    def set_prefs(self, chat_id: int, categories: set[str]):
        self.prefs[chat_id] = set(categories)
        if USE_FIREBASE:
            try:
                firebase_put(f"prefs/{chat_id}/categories", list(categories))
                return
            except Exception as e:
                log.warning("Firebase prefs save fallita: %s", e)

    def get_prefs(self, chat_id: int) -> set[str]:
        return self.prefs.get(chat_id) or {"pc"}

    def set_genres(self, chat_id: int, genres: set[str]):
        self.genres_prefs = getattr(self, "genres_prefs", {})
        self.genres_prefs[chat_id] = set(genres)
        if USE_FIREBASE:
            try:
                firebase_put(f"prefs/{chat_id}/genres", list(genres))
                return
            except Exception as e:
                log.warning("Firebase genres save fallita: %s", e)

    def get_genres(self, chat_id: int) -> set[str]:
        self.genres_prefs = getattr(self, "genres_prefs", {})
        return self.genres_prefs.get(chat_id) or set()

    def set_content(self, chat_id: int, content: set[str]):
        self.content_prefs = getattr(self, "content_prefs", {})
        self.content_prefs[chat_id] = set(content)
        if USE_FIREBASE:
            try:
                firebase_put(f"prefs/{chat_id}/content", list(content))
                return
            except Exception as e:
                log.warning("Firebase content save fallita: %s", e)

    def get_content(self, chat_id: int) -> set[str]:
        self.content_prefs = getattr(self, "content_prefs", {})
        return self.content_prefs.get(chat_id) or set(DEFAULT_CONTENT)

    def acquire_broadcast_lock(self, ttl: int = 180) -> bool:
        """Evita che due istanze concorrenti (es. overlap deploy su Render)
        inviino lo stesso broadcast. Best-effort tramite Firebase."""
        if not USE_FIREBASE:
            return True
        import time
        my_ts = time.time()
        try:
            lock = firebase_get("broadcast_lock")
            if isinstance(lock, dict) and (my_ts - float(lock.get("ts", 0))) < ttl:
                log.info("Broadcast lock attivo da altra istanza, salto.")
                return False
            firebase_put("broadcast_lock", {"ts": my_ts, "owner": INSTANCE_ID})
            # rileggi per verificare di aver vinto la corsa
            time.sleep(0.5)
            check = firebase_get("broadcast_lock")
            if isinstance(check, dict) and check.get("owner") != INSTANCE_ID:
                log.info("Broadcast lock vinto da altra istanza, salto.")
                return False
            return True
        except Exception as e:
            log.warning("Lock check fallito (procedo): %s", e)
            return True


INSTANCE_ID = secrets.token_hex(4)
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


GAMERPOWER_PC_PLATFORMS = ["pc", "epic-games-store", "steam", "gog", "ubisoft", "drm-free"]
GAMERPOWER_CONSOLE_PLATFORMS = ["ps4", "ps5", "xbox-one", "xbox-series-xs", "switch"]
GAMERPOWER_MOBILE_PLATFORMS = ["android", "ios"]


GENRE_KEYWORDS = {
    "azione": ["action", "fight", "combat", "beat 'em up", "hack and slash", "azione"],
    "avventura": ["adventure", "quest", "journey", "exploration", "avventura", "esplorazione"],
    "rpg": ["rpg", "role-playing", "role playing", "jrpg", "ruolo"],
    "strategia": ["strategy", "rts", "turn-based", "tactic", "4x", "strategia", "strategico"],
    "simulazione": ["simulator", "simulation", "tycoon", "management", "simulazione", "simulatore"],
    "sport": ["sport", "soccer", "football", "basketball", "tennis", "golf", "calcio"],
    "corse": ["racing", "race ", "racer", "driving", "corse", "corsa"],
    "puzzle": ["puzzle", "escape room", "brain", "rompicapo", "enigma"],
    "platform": ["platformer", "platform game", "2d platform", "platform"],
    "sparatutto": ["shooter", "fps", "third-person shooter", "sparatutto"],
    "survival": ["survival", "sopravvivenza"],
    "horror": ["horror", "scary", "psychological horror"],
    "roguelike": ["roguelike", "roguelite"],
    "indie": ["indie", "indipendente"],
    "casual": ["casual", "relaxing", "rilassante"],
}

GENRE_LABELS = {
    "azione": "🗡️ Azione",
    "avventura": "🧭 Avventura",
    "rpg": "⚔️ RPG",
    "strategia": "♟️ Strategia",
    "simulazione": "🏗️ Simulazione",
    "sport": "⚽ Sport",
    "corse": "🏎️ Corse",
    "puzzle": "🧩 Puzzle",
    "platform": "🦘 Platform",
    "sparatutto": "🔫 Sparatutto",
    "survival": "🏕️ Survival",
    "horror": "👻 Horror",
    "roguelike": "🎲 Roguelike",
    "indie": "🎨 Indie",
    "casual": "☕ Casual",
}


def detect_genres(title: str, description: str) -> list[str]:
    haystack = f"{title} {description}".lower()
    genres = []
    for g, kws in GENRE_KEYWORDS.items():
        if any(k in haystack for k in kws):
            genres.append(g)
    return genres


def _categorize(platforms_str: str) -> list[str]:
    s = (platforms_str or "").lower()
    cats = []
    if any(k in s for k in ["pc", "steam", "epic", "gog", "ubisoft", "drm-free", "itch", "battle.net", "origin"]):
        cats.append("pc")
    if any(k in s for k in ["ps4", "ps5", "playstation", "xbox", "switch", "nintendo"]):
        cats.append("console")
    if any(k in s for k in ["android", "ios", "mobile"]):
        cats.append("android")
    return cats or ["pc"]


async def _fetch_gamerpower_one(platform: str, gtype: str = "game") -> list[dict]:
    if platform:
        api_url = f"https://www.gamerpower.com/api/giveaways?platform={platform}&type={gtype}"
    else:
        api_url = f"https://www.gamerpower.com/api/giveaways?type={gtype}"
    try:
        data = await fetch_json(api_url)
    except Exception as e:
        log.warning("GamerPower fetch %s/%s fallita: %s", platform, gtype, e)
        return []
    if not isinstance(data, list):
        log.info("GamerPower %s/%s: nessun giveaway (%s)", platform, gtype, str(data)[:80])
        return []
    content_type = "dlc" if gtype == "loot" else "game"
    games = []
    for it in data:
        if not isinstance(it, dict):
            continue
        if it.get("status", "").lower() != "active":
            continue
        title = (it.get("title") or "").strip()
        if not title:
            continue
        platforms_str = it.get("platforms", "PC") or "PC"
        is_prime = "amazon" in platforms_str.lower() or "prime" in platforms_str.lower()
        if content_type == "dlc":
            source = "DLC / Contenuti"
        elif is_prime:
            source = "Amazon Prime Gaming"
        else:
            source = "GamerPower"
        gp_desc = (it.get("description") or "").strip()
        games.append({
            "id": f"gp_{content_type}_{normalize_title(title)}",
            "title": title,
            "description": gp_desc,
            "url": it.get("open_giveaway_url") or it.get("gamerpower_url") or "",
            "image": it.get("image") or it.get("thumbnail") or "",
            "platform": platforms_str,
            "end_date": it.get("end_date", "N/D"),
            "source": source,
            "worth": it.get("worth", "N/A"),
            "translate": True,
            "categories": _categorize(platforms_str),
            "genres": detect_genres(title, gp_desc),
            "content_type": content_type,
        })
    return games


async def fetch_gamerpower_all() -> list[dict]:
    platforms = GAMERPOWER_PC_PLATFORMS + GAMERPOWER_CONSOLE_PLATFORMS + GAMERPOWER_MOBILE_PLATFORMS
    results = await asyncio.gather(*[_fetch_gamerpower_one(p, "game") for p in platforms])
    out, seen = [], set()
    for batch in results:
        for g in batch:
            key = normalize_title(g["title"])
            if key in seen:
                continue
            seen.add(key)
            out.append(g)
    return out


async def fetch_gamerpower_loot() -> list[dict]:
    """DLC, loot, contenuti in-game gratuiti (PC + console). content_type=dlc."""
    batch = await _fetch_gamerpower_one("", "loot")
    out, seen = [], set()
    for g in batch:
        key = g["id"]
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
    return out


async def fetch_epic_full_description(slug: str) -> str:
    if not slug:
        return ""
    url = f"https://store-content.ak.epicgames.com/api/it/content/products/{slug}"
    sess = await get_session()
    try:
        async with sess.get(url, headers={"User-Agent": "Mozilla/5.0"}) as r:
            text = await r.text()
        m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.){50,2000})"', text)
        if m:
            desc = m.group(1).encode("utf-8").decode("unicode_escape", errors="ignore")
            return desc.strip()
        m2 = re.search(r'"shortDescription"\s*:\s*"((?:[^"\\]|\\.){30,1500})"', text)
        if m2:
            return m2.group(1).encode("utf-8").decode("unicode_escape", errors="ignore").strip()
    except Exception as e:
        log.info("Epic content fetch fallita %s: %s", slug, e)
    return ""


async def fetch_epic_upcoming() -> list[dict]:
    """Giochi gratis Epic in arrivo (settimane prossime), da upcomingPromotionalOffers."""
    url = (
        "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
        "?locale=it-IT&country=IT&allowCountries=IT"
    )
    try:
        data = await fetch_json(url)
    except Exception as e:
        log.warning("Epic upcoming fetch fallita: %s", e)
        return []
    out = []
    elements = (
        data.get("data", {})
        .get("Catalog", {})
        .get("searchStore", {})
        .get("elements", [])
    )
    for el in elements:
        up = (el.get("promotions") or {}).get("upcomingPromotionalOffers") or []
        start_date = None
        for p in up:
            for o in p.get("promotionalOffers", []):
                start_date = o.get("startDate")
                break
            if start_date:
                break
        if not start_date:
            continue
        title = (el.get("title") or "").strip()
        if not title or "mystery game" in title.lower():
            title = "🎲 Gioco a sorpresa (Epic non lo ha ancora svelato)"
        image = ""
        for img in el.get("keyImages", []):
            if img.get("type") in ("OfferImageWide", "DieselStoreFrontWide", "Thumbnail"):
                image = img.get("url", "")
                break
        try:
            dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            when = f"{dt.day} {MESI_IT[dt.month - 1]} {dt.year}"
        except Exception:
            when = "prossimamente"
        out.append({"title": title, "image": image, "when": when})
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
        short_desc = (el.get("description") or "").strip()
        long_desc = await fetch_epic_full_description(slug) if slug else ""
        desc = long_desc or short_desc
        if desc.lower() == title.lower():
            desc = ""
        games.append({
            "id": f"epic_{normalize_title(title)}",
            "title": title,
            "description": desc,
            "url": url_game,
            "image": image,
            "platform": "PC (Epic Games)",
            "end_date": end_date,
            "source": "Epic Games",
            "worth": "N/A",
            "translate": False,
            "categories": ["pc"],
            "genres": detect_genres(title, desc),
        })
    return games


def _clean_reddit_title(title: str) -> str:
    t = title
    for _ in range(5):
        new = re.sub(r"^\s*\[[^\]]*\]\s*", "", t)
        if new == t:
            break
        t = new
    t = re.sub(r"^\s*\([A-Z][^)]*\)\s*", "", t)
    t = re.sub(r"^\s*\([A-Z][^)]*\)\s*", "", t)
    return t.strip()


async def fetch_reddit_all() -> list[dict]:
    url = "https://www.reddit.com/r/FreeGameFindings/new.json?limit=75&raw_json=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) free-pc-games-bot/1.0",
        "Accept": "application/json",
    }
    sess = await get_session()
    try:
        async with sess.get(url, headers=headers) as r:
            text = await r.text()
        if not text.strip().startswith("{"):
            log.warning("Reddit non-JSON (rate-limit?), salto: %s", text[:80])
            return []
        data = json.loads(text)
    except Exception as e:
        log.warning("Reddit fetch fallita: %s", e)
        return []
    posts = data.get("data", {}).get("children", [])
    games = []
    now = datetime.now(timezone.utc).timestamp()
    for p in posts:
        pd = p.get("data") or {}
        title = (pd.get("title") or "").strip()
        link_url = pd.get("url", "") or ""
        flair = (pd.get("link_flair_text", "") or "").lower()
        sel = (pd.get("selftext") or "").strip()
        created = pd.get("created_utc", 0)
        if not title or not link_url:
            continue
        if now - created > 7 * 86400:
            continue
        tl = title.lower()
        if any(k in tl for k in ("fgf mod", "mod post", "free game findings application",
                                 "mod announcement", "discussion", "weekly thread",
                                 "subreddit", "free game findings community")):
            continue
        if any(k in flair for k in ("mod", "meta", "discussion", "announcement")):
            continue
        link_l = link_url.lower()
        title_l = tl
        cats = []
        source = None
        if "luna.amazon" in link_l or "gaming.amazon" in link_l:
            cats, source = ["pc"], "Amazon Prime Gaming"
        elif "prime" in title_l and ("gaming" in title_l or "amazon" in title_l):
            cats, source = ["pc"], "Amazon Prime Gaming"
        elif any(k in link_l for k in ("store.playstation", "psn.com")) or \
             any(k in title_l for k in ("(ps5)", "(ps4)", "[ps5]", "[ps4]", "(playstation)", "[playstation]")):
            cats, source = ["console"], "PlayStation Store"
        elif "xbox.com" in link_l or any(k in title_l for k in ("(xbox)", "[xbox]", "xbox series", "xbox one")):
            cats, source = ["console"], "Xbox Store"
        elif any(k in link_l for k in ("nintendo.com", "ec.nintendo.com")) or \
             any(k in title_l for k in ("(switch)", "[switch]", "(nintendo)", "[nintendo]", "nintendo eshop")):
            cats, source = ["console"], "Nintendo eShop"
        elif "play.google.com" in link_l or "apps.apple.com" in link_l or \
             any(k in title_l for k in ("(android)", "[android]", "(ios)", "[ios]", "(mobile)", "[mobile]")):
            cats, source = ["android"], "Mobile Store"
        else:
            continue
        clean = _clean_reddit_title(title)
        desc_raw = sel[:350].strip() if sel else ""
        if desc_raw:
            desc = translate_it(desc_raw)
        else:
            desc = f"Gioco gratuito su {source}. Riscatta dal link sotto."
        games.append({
            "id": f"reddit_{pd.get('id','')}",
            "title": clean or title,
            "description": desc,
            "url": link_url,
            "image": pd.get("thumbnail") if (pd.get("thumbnail","") or "").startswith("http") else "",
            "platform": ", ".join(c.upper() for c in cats),
            "end_date": "N/A",
            "source": source,
            "worth": "N/A",
            "translate": False,
            "categories": cats,
            "genres": detect_genres(clean, desc),
        })
    return games


MONTHS_EN = ["january", "february", "march", "april", "may", "june",
             "july", "august", "september", "october", "november", "december"]
GGDEALS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _parse_ggdeals_games(html: str) -> list[str]:
    links = re.findall(r'<a[^>]+href="(/game/[^"]+)"[^>]*>(.*?)</a>', html, re.S)
    titles, seen = [], set()
    for _href, txt in links:
        t = re.sub(r"<[^>]+>", "", txt).strip()
        t = t.replace("&amp;", "&").replace("&#039;", "'").replace("&quot;", '"')
        if not t or "Discount" in t or "Cheapest price" in t:
            continue
        key = normalize_title(t)
        if not key or key in seen:
            continue
        seen.add(key)
        titles.append(t)
    return titles


async def fetch_prime_gaming() -> list[dict]:
    sess = await get_session()
    now = datetime.now(timezone.utc)
    html = None
    direct = (
        f"https://gg.deals/subscription-news/prime-gaming-amazon-luna-"
        f"{MONTHS_EN[now.month - 1]}-{now.year}-full-list-of-free-games-for-the-month/"
    )
    try:
        async with sess.get(direct, headers={"User-Agent": GGDEALS_UA}) as r:
            if r.status == 200:
                html = await r.text()
    except Exception as e:
        log.info("gg.deals URL diretto fallito: %s", e)
    if not html:
        try:
            async with sess.get(
                "https://gg.deals/news/prime-gaming-free-games/",
                headers={"User-Agent": GGDEALS_UA},
            ) as r:
                idx = await r.text()
            m = re.search(
                r'href="(/subscription-news/prime-gaming-amazon-luna-[^"]*full-list[^"]*)"',
                idx,
            )
            if m:
                async with sess.get("https://gg.deals" + m.group(1), headers={"User-Agent": GGDEALS_UA}) as r:
                    if r.status == 200:
                        html = await r.text()
        except Exception as e:
            log.warning("gg.deals fallback fallito: %s", e)
    if not html:
        log.warning("Prime Gaming: nessuna lista recuperata da gg.deals")
        return []
    titles = _parse_ggdeals_games(html)[:25]
    log.info("Prime Gaming: %d giochi trovati su gg.deals", len(titles))
    steam_infos = await asyncio.gather(*[steam_lookup(t) for t in titles], return_exceptions=True)
    # per i titoli dove Steam non ha dato descrizione, prova RAWG (se configurato)
    need_rawg = [
        t for t, info in zip(titles, steam_infos)
        if not (isinstance(info, dict) and info.get("description"))
    ]
    rawg_results = {}
    if RAWG_KEY and need_rawg:
        rl = await asyncio.gather(*[rawg_lookup(t) for t in need_rawg], return_exceptions=True)
        for t, info in zip(need_rawg, rl):
            rawg_results[t] = info if isinstance(info, dict) else None
    generic = "Gioco gratuito incluso con l'abbonamento Amazon Prime. Riscattalo dall'app Prime Gaming / Amazon Luna."
    games = []
    for t, info in zip(titles, steam_infos):
        if isinstance(info, Exception):
            info = None
        rawg = rawg_results.get(t) or {}
        desc = (info or {}).get("description") or rawg.get("description") or generic
        image = (info or {}).get("image") or rawg.get("image") or ""
        price = (info or {}).get("price") or "N/A"
        games.append({
            "id": f"prime_{normalize_title(t)}",
            "title": t,
            "description": desc,
            "url": "https://gaming.amazon.com/home",
            "image": image,
            "platform": "PC (Amazon Prime)",
            "end_date": "N/A",
            "source": "Amazon Prime Gaming",
            "worth": price,
            "translate": True,
            "categories": ["pc"],
            "genres": detect_genres(t, desc),
        })
    return games


async def fetch_all_games() -> list[dict]:
    epic, gp, reddit, prime, loot = await asyncio.gather(
        fetch_epic_free(),
        fetch_gamerpower_all(),
        fetch_reddit_all(),
        fetch_prime_gaming(),
        fetch_gamerpower_loot(),
    )
    seen, unique = set(), []
    for g in epic + gp + reddit + prime + loot:
        # la chiave include il tipo: un gioco e un suo DLC non si annullano a vicenda
        key = (g.get("content_type", "game"), normalize_title(g["title"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(g)
    return unique


def filter_by_categories(games: list[dict], wanted: set[str]) -> list[dict]:
    if not wanted or "all" in wanted:
        return games
    out = []
    for g in games:
        cats = set(g.get("categories") or ["pc"])
        if cats & wanted:
            out.append(g)
    return out


def filter_by_genres(games: list[dict], wanted: set[str]) -> list[dict]:
    if not wanted or "all" in wanted:
        return games
    out = []
    for g in games:
        gens = set(g.get("genres") or [])
        if gens & wanted:
            out.append(g)
    return out


def filter_by_content(games: list[dict], wanted: set[str]) -> list[dict]:
    """Filtra per tipo di contenuto. Giochi senza 'content_type' sono trattati
    come 'game'. 'wanted' di default è {'game'} (no DLC, no abbonamenti)."""
    if not wanted:
        wanted = set(DEFAULT_CONTENT)
    return [g for g in games if g.get("content_type", "game") in wanted]


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_game(g: dict) -> str:
    title = clean_title(g["title"])
    desc = g.get("description", "") or ""
    if g.get("translate", True):
        desc = translate_it(desc)
    if len(desc) > 380:
        desc = desc[:377] + "..."
    source = g.get("source", "?")
    cats = set(g.get("categories") or ["pc"])
    ctype = g.get("content_type", "game")
    is_prime = "prime" in source.lower() or "amazon" in source.lower()
    plat_label = "PC" if "pc" in cats else ("CONSOLE" if "console" in cats else "ANDROID")
    if ctype == "dlc":
        header = f"🎁 DLC / CONTENUTO GRATIS ({plat_label})"
    elif ctype == "subscription":
        header = f"💳 GRATIS CON ABBONAMENTO ({plat_label})"
    elif is_prime:
        header = "🎁 GRATIS SU PRIME GAMING"
    elif cats == {"console"}:
        header = "🎁 GRATIS SU CONSOLE"
    elif cats == {"android"}:
        header = "🎁 GRATIS SU ANDROID"
    elif "console" in cats and "pc" not in cats:
        header = "🎁 GRATIS SU CONSOLE"
    else:
        header = "🎁 GRATIS SU PC"
    parts = [f"<b>{html_escape(header)}</b>", ""]
    parts.append(f"<b>{html_escape(title.upper())}</b>")
    if desc:
        parts.append("")
        parts.append(html_escape(desc))
    parts.append("")
    plat_tag = "#PC" if "pc" in cats else ("#Console" if "console" in cats else "#Android")
    tags = [plat_tag, hashtag(source)]
    line = " ".join(t for t in tags if t)
    price = format_price_eur(g.get("worth"))
    if price:
        line = f"{line}  ·  Valore {price}"
    parts.append(html_escape(line))
    date = format_date_it(g.get("end_date"))
    if date:
        parts.append(html_escape(f"Scade il {date}"))
    elif is_prime:
        parts.append("Riscattabile fino a fine periodo Prime (vedi su Amazon)")
    # link del gioco in chiaro (copiabile + cliccabile)
    url = g.get("url")
    if url:
        label = "🎮 Riscatta qui:" if is_prime else "🔗 Scarica qui:"
        parts.append("")
        parts.append(f"{label}\n<code>{html_escape(url)}</code>")
    return "\n".join(parts)


_rawg_cache: dict[str, Optional[dict]] = {}


async def rawg_lookup(title: str) -> Optional[dict]:
    """Fallback descrizione/immagine da RAWG (se RAWG_KEY impostata). Cache per titolo."""
    if not RAWG_KEY:
        return None
    clean = clean_title(title)
    key = normalize_title(clean)
    if key in _rawg_cache:
        return _rawg_cache[key]
    sess = await get_session()
    result = None
    try:
        from urllib.parse import quote_plus
        async with sess.get(
            f"https://api.rawg.io/api/games?key={RAWG_KEY}&search={quote_plus(clean)}&page_size=5",
            headers={"User-Agent": "free-pc-games-bot/1.0"},
        ) as r:
            data = await r.json(content_type=None)
        for item in (data.get("results") or [])[:5]:
            name = normalize_title(item.get("name", ""))
            wanted = set(key.split())
            found = set(name.split())
            if name == key or (wanted and len(wanted & found) / len(wanted) >= 0.6):
                slug = item.get("slug")
                image = item.get("background_image") or ""
                desc = ""
                if slug:
                    async with sess.get(
                        f"https://api.rawg.io/api/games/{slug}?key={RAWG_KEY}",
                        headers={"User-Agent": "free-pc-games-bot/1.0"},
                    ) as r2:
                        det = await r2.json(content_type=None)
                    desc = (det.get("description_raw") or "").strip()
                    if len(desc) > 400:
                        desc = desc[:397] + "..."
                result = {"description": desc, "image": image}
                break
    except Exception as e:
        log.info("RAWG lookup fallito per '%s': %s", title, e)
        result = None
    _rawg_cache[key] = result
    return result


_steam_cache: dict[str, Optional[dict]] = {}


def _title_variants(clean: str) -> list[str]:
    """Genera varianti del titolo da provare su Steam, dalla più specifica alla più generica."""
    variants = [clean]
    # togli sottotitolo dopo ':' o ' - '
    base = re.split(r"\s*[:\-–]\s+", clean)[0].strip()
    if base and base != clean and len(base) >= 3:
        variants.append(base)
    # togli suffissi tipo "Definitive Edition", "Remastered", numeri romani finali
    stripped = re.sub(r"\s+(definitive edition|remastered|complete edition|goty.*|game of the year.*)$", "", clean, flags=re.IGNORECASE).strip()
    if stripped and stripped not in variants and len(stripped) >= 3:
        variants.append(stripped)
    return variants


async def _steam_search_one(sess, term: str, key: str) -> Optional[int]:
    async with sess.get(
        f"https://store.steampowered.com/api/storesearch/?term={term}&cc=IT&l=italian",
        headers={"User-Agent": "Mozilla/5.0"},
    ) as r:
        sr = await r.json(content_type=None)
    for item in (sr.get("items") or [])[:5]:
        appid = item.get("id")
        if not appid:
            continue
        steam_title = normalize_title(item.get("name", ""))
        if steam_title == key:
            return appid
        wanted = set(key.split())
        found = set(steam_title.split())
        if wanted and len(wanted & found) / len(wanted) >= 0.6:
            return appid
    return None


async def steam_lookup(title: str) -> Optional[dict]:
    """Cerca il gioco su Steam (match titolo, più varianti) e restituisce
    {appid, description, image, trailer, price} oppure None. Risultato in cache."""
    clean = clean_title(title)
    key = normalize_title(clean)
    if key in _steam_cache:
        return _steam_cache[key]
    sess = await get_session()
    result = None
    try:
        appid = None
        for variant in _title_variants(clean):
            appid = await _steam_search_one(sess, variant, normalize_title(variant) if variant != clean else key)
            if appid:
                break
        if appid:
            async with sess.get(
                f"https://store.steampowered.com/api/appdetails?appids={appid}&l=italian&cc=IT",
                headers={"User-Agent": "Mozilla/5.0"},
            ) as r:
                ad = await r.json(content_type=None)
            det = (ad.get(str(appid)) or {}).get("data") or {}
            desc = (det.get("short_description") or "").strip()
            image = det.get("header_image") or ""
            price = ""
            po = det.get("price_overview") or {}
            if po.get("final_formatted"):
                price = po["final_formatted"]
            trailer = None
            movies = det.get("movies") or []
            if movies:
                m = next((x for x in movies if x.get("highlight")), movies[0])
                mp4 = m.get("mp4") or {}
                trailer = mp4.get("480") or mp4.get("max")
                if not trailer and m.get("id"):
                    trailer = f"https://cdn.akamai.steamstatic.com/steam/apps/{m['id']}/movie480.mp4"
                if trailer and trailer.startswith("//"):
                    trailer = "https:" + trailer
            result = {"appid": appid, "description": desc, "image": image, "trailer": trailer, "price": price}
    except Exception as e:
        log.info("Steam lookup fallito per '%s': %s", title, e)
        result = None
    _steam_cache[key] = result
    return result


async def search_steam_trailer(title: str) -> Optional[str]:
    info = await steam_lookup(title)
    return info.get("trailer") if info else None


_YT_BLOCKED = False  # diventa True se YouTube blocca l'IP (datacenter) per evitare retry inutili


def _ytdlp_extract_mp4_sync(youtube_url: str) -> Optional[str]:
    global _YT_BLOCKED
    if not _YTDLP_OK or _YT_BLOCKED:
        return None
    opts = {
        "format": "best[ext=mp4][height<=480][filesize<19M]/best[ext=mp4][height<=360]/best[height<=480][ext=mp4]",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "socket_timeout": 12,
        # client mobile: a volte aggira il "Sign in to confirm you're not a bot" sui datacenter
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            return info.get("url")
    except Exception as e:
        emsg = str(e)
        if "Sign in to confirm" in emsg or "not a bot" in emsg:
            _YT_BLOCKED = True
            log.warning("YouTube blocca l'IP per estrazione video: disabilito yt-dlp (uso thumbnail+link)")
        else:
            log.info("yt-dlp extract fallita per %s: %s", youtube_url, emsg[:120])
        return None


async def extract_youtube_mp4(youtube_url: str) -> Optional[str]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_ytdlp_extract_mp4_sync, youtube_url),
            timeout=15,
        )
    except asyncio.TimeoutError:
        log.info("yt-dlp timeout per %s", youtube_url)
        return None
    except Exception as e:
        log.info("yt-dlp errore: %s", e)
        return None


async def search_youtube_video(query: str) -> Optional[tuple[str, str]]:
    """Restituisce (video_url, thumbnail_url) oppure None."""
    sess = await get_session()
    from urllib.parse import quote_plus
    q = quote_plus(f"{query} gameplay trailer")
    url = f"https://www.youtube.com/results?search_query={q}&hl=it&gl=IT"
    try:
        async with sess.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "it,en;q=0.7",
        }) as r:
            html = await r.text()
    except Exception as e:
        log.info("YouTube fetch fallita: %s", e)
        return None
    m = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
    if not m:
        return None
    vid = m.group(1)
    return (
        f"https://www.youtube.com/watch?v={vid}",
        f"https://img.youtube.com/vi/{vid}/hqdefault.jpg",
    )


def _trailer_button(yt_url: Optional[str]) -> Optional[dict]:
    if not yt_url:
        return None
    return {"inline_keyboard": [[{"text": "▶️ Guarda trailer", "url": yt_url}]]}


async def send_game(chat_id: int, g: dict):
    yt = await search_youtube_video(clean_title(g["title"]))
    yt_url = yt[0] if yt else None
    yt_thumb = yt[1] if yt else None
    caption = format_game(g)
    button = _trailer_button(yt_url)
    mp4 = None
    if yt_url:
        mp4 = await extract_youtube_mp4(yt_url)
    if not mp4:
        mp4 = await search_steam_trailer(g["title"])
    if mp4:
        try:
            payload = dict(
                chat_id=chat_id,
                video=mp4,
                caption=caption,
                parse_mode="HTML",
                supports_streaming=True,
            )
            if button:
                payload["reply_markup"] = button
            r = await tg_api("sendVideo", **payload)
            if r.get("ok"):
                return
            log.info("sendVideo non OK, fallback foto: %s", r.get("description"))
        except Exception as e:
            log.warning("sendVideo fallita, fallback foto: %s", e)
    photo = yt_thumb or g.get("image")
    if photo:
        try:
            payload = dict(chat_id=chat_id, photo=photo, caption=caption, parse_mode="HTML")
            if button:
                payload["reply_markup"] = button
            r = await tg_api("sendPhoto", **payload)
            if r.get("ok"):
                return
            log.info("sendPhoto non OK, fallback testo: %s", r.get("description"))
        except Exception as e:
            log.warning("sendPhoto fallita, fallback testo: %s", e)
    payload = dict(
        chat_id=chat_id, text=caption, parse_mode="HTML", disable_web_page_preview=False
    )
    if button:
        payload["reply_markup"] = button
    await tg_api("sendMessage", **payload)


WELCOME_NEW = (
    "✅ <b>Iscrizione attivata!</b>\n\n"
    "Scegli per quali piattaforme vuoi ricevere notifiche di giochi gratis:"
)
WELCOME_ALREADY = (
    "ℹ️ Questa chat è già iscritta.\n\n"
    "Puoi cambiare le piattaforme qui sotto, oppure usare /stop o /giochi."
)


def platforms_keyboard(current: set[str]) -> dict:
    def btn(label: str, code: str) -> dict:
        mark = "✅ " if code in current else ""
        return {"text": f"{mark}{label}", "callback_data": f"pref:{code}"}
    return {
        "inline_keyboard": [
            [btn("🖥️ PC", "pc"), btn("🎮 Console", "console")],
            [btn("📱 Android", "android"), btn("🌍 Tutti", "all")],
            [{"text": "✔️ Conferma", "callback_data": "pref:done"}],
        ]
    }


def genres_keyboard(current: set[str]) -> dict:
    def btn(code: str) -> dict:
        mark = "✅ " if code in current else ""
        return {"text": f"{mark}{GENRE_LABELS[code]}", "callback_data": f"gen:{code}"}
    codes = list(GENRE_LABELS.keys())
    rows = [[btn(codes[i]), btn(codes[i + 1])] for i in range(0, len(codes) - 1, 2)]
    if len(codes) % 2 == 1:
        rows.append([btn(codes[-1])])
    rows.append([{"text": "🌍 Tutti i generi", "callback_data": "gen:all"}])
    rows.append([{"text": "✔️ Conferma", "callback_data": "gen:done"}])
    return {"inline_keyboard": rows}


CONTENT_LABELS = {
    "game": "🎮 Giochi gratis",
    "dlc": "🎁 DLC e contenuti",
    "subscription": "💳 Abbonamenti (PS+/Game Pass)",
}
# tipi mostrati nel menu /contenuti (subscription nascosto finché non c'è fonte affidabile)
CONTENT_MENU = ["game", "dlc"]


def content_keyboard(current: set[str]) -> dict:
    def btn(code: str) -> dict:
        mark = "✅ " if code in current else ""
        return {"text": f"{mark}{CONTENT_LABELS[code]}", "callback_data": f"cont:{code}"}
    rows = [[btn(c)] for c in CONTENT_MENU]
    rows.append([{"text": "✔️ Conferma", "callback_data": "cont:done"}])
    return {"inline_keyboard": rows}


def handle_update(update: dict) -> Optional[dict]:
    cb = update.get("callback_query")
    if cb:
        data = cb.get("data", "") or ""
        chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        msg_id = (cb.get("message") or {}).get("message_id")
        cb_id = cb.get("id")
        if not chat_id:
            return {"method": "answerCallbackQuery", "callback_query_id": cb_id}
        if data.startswith("pref:"):
            code = data.split(":", 1)[1]
            current = state.get_prefs(chat_id) or set()
            if code == "all":
                current = {"pc", "console", "android"}
            elif code == "done":
                if not current:
                    current = {"pc"}
                state.set_prefs(chat_id, current)
                labels = {"pc": "PC", "console": "Console", "android": "Android"}
                chosen = ", ".join(labels[c] for c in ["pc", "console", "android"] if c in current) or "Nessuna"
                asyncio.create_task(_finish_setup(chat_id, msg_id, chosen, cb_id))
                return None
            elif code in ("pc", "console", "android"):
                if code in current:
                    current.discard(code)
                else:
                    current.add(code)
            else:
                return {"method": "answerCallbackQuery", "callback_query_id": cb_id}
            state.set_prefs(chat_id, current)
            asyncio.create_task(_update_keyboard(chat_id, msg_id, current, cb_id))
            return None
        if data.startswith("gen:"):
            code = data.split(":", 1)[1]
            current = state.get_genres(chat_id) or set()
            if code == "all":
                current = set()
            elif code == "done":
                state.set_genres(chat_id, current)
                chosen = "Tutti" if not current else ", ".join(GENRE_LABELS[c].split(" ", 1)[-1] for c in current)
                asyncio.create_task(_finish_genres(chat_id, msg_id, chosen, cb_id))
                return None
            elif code in GENRE_LABELS:
                if code in current:
                    current.discard(code)
                else:
                    current.add(code)
            else:
                return {"method": "answerCallbackQuery", "callback_query_id": cb_id}
            state.set_genres(chat_id, current)
            asyncio.create_task(_update_genres_keyboard(chat_id, msg_id, current, cb_id))
            return None
        if data.startswith("cont:"):
            code = data.split(":", 1)[1]
            current = state.get_content(chat_id) or set(DEFAULT_CONTENT)
            if code == "done":
                if not current:
                    current = set(DEFAULT_CONTENT)
                state.set_content(chat_id, current)
                chosen = ", ".join(CONTENT_LABELS[c].split(" ", 1)[-1] for c in ["game", "dlc", "subscription"] if c in current) or "Nessuno"
                asyncio.create_task(_finish_content(chat_id, msg_id, chosen, cb_id))
                return None
            elif code in CONTENT_LABELS:
                if code in current:
                    current.discard(code)
                else:
                    current.add(code)
            else:
                return {"method": "answerCallbackQuery", "callback_query_id": cb_id}
            state.set_content(chat_id, current)
            asyncio.create_task(_update_content_keyboard(chat_id, msg_id, current, cb_id))
            return None
        return {"method": "answerCallbackQuery", "callback_query_id": cb_id}
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
        if cmd == "/start" or cmd == "/piattaforme":
            added = state.subscribe(chat_id)
            current = state.get_prefs(chat_id)
            return {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": WELCOME_NEW if added else WELCOME_ALREADY,
                "parse_mode": "HTML",
                "reply_markup": platforms_keyboard(current),
            }
        if cmd == "/generi":
            current = state.get_genres(chat_id)
            return {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": (
                    "🎯 <b>Filtra per generi</b>\n\n"
                    "Seleziona i generi che ti interessano (o 'Tutti' per non filtrare). "
                    "Verranno mostrati solo giochi che corrispondono ad almeno uno dei generi scelti."
                ),
                "parse_mode": "HTML",
                "reply_markup": genres_keyboard(current),
            }
        if cmd == "/contenuti":
            current = state.get_content(chat_id)
            return {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": (
                    "🗂️ <b>Tipi di contenuto</b>\n\n"
                    "Scegli cosa vuoi ricevere. Di default solo i <b>giochi gratis</b> completi.\n\n"
                    "• 🎮 <b>Giochi gratis</b> – giochi interi gratuiti\n"
                    "• 🎁 <b>DLC e contenuti</b> – espansioni, skin, pacchetti, codici (PC e console)\n\n"
                    "Attiva solo ciò che ti interessa per evitare notifiche di troppo."
                ),
                "parse_mode": "HTML",
                "reply_markup": content_keyboard(current),
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
        if cmd == "/cerca":
            query = text.split(None, 1)
            if len(query) < 2 or len(query[1].strip()) < 2:
                return {
                    "method": "sendMessage",
                    "chat_id": chat_id,
                    "text": "Scrivi cosa cercare dopo il comando, es:\n<code>/cerca tomb raider</code>",
                    "parse_mode": "HTML",
                }
            asyncio.create_task(_handle_cerca(chat_id, query[1].strip()))
            return {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": f"🔎 Cerco «{query[1].strip()}» tra i giochi gratis…",
            }
        if cmd == "/prossimi":
            asyncio.create_task(_handle_prossimi(chat_id))
            return {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": "🔮 Controllo i prossimi giochi gratis Epic…",
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
        games = filter_by_content(games, state.get_content(chat_id))
        games = filter_by_categories(games, state.get_prefs(chat_id))
        games = filter_by_genres(games, state.get_genres(chat_id))
        if not games:
            await tg_api("sendMessage", chat_id=chat_id, text="Nessun gioco trovato per i tuoi filtri. Cambia con /piattaforme, /generi o /contenuti.")
            return
        for g in games[:12]:
            try:
                await send_game(chat_id, g)
            except Exception as e:
                log.warning("send_game fallito: %s", e)
    except Exception as e:
        log.exception("Errore /giochi: %s", e)


async def _handle_prossimi(chat_id: int):
    try:
        upcoming = await fetch_epic_upcoming()
        if not upcoming:
            await tg_api("sendMessage", chat_id=chat_id, text="Nessun gioco gratis Epic in arrivo al momento.")
            return
        lines = ["🔮 <b>PROSSIMI GIOCHI GRATIS — EPIC GAMES</b>", ""]
        for g in upcoming:
            lines.append(f"• <b>{html_escape(g['title'])}</b>")
            lines.append(f"  dal {html_escape(g['when'])}")
        lines.append("")
        lines.append("Te li segnalo appena diventano riscattabili. 🎁")
        await tg_api("sendMessage", chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
    except Exception as e:
        log.exception("Errore /prossimi: %s", e)
        await tg_api("sendMessage", chat_id=chat_id, text="Errore nel recupero dei prossimi giochi. Riprova più tardi.")


async def _handle_cerca(chat_id: int, query: str):
    try:
        q = normalize_title(query)
        qwords = set(q.split())
        games = await fetch_all_games()
        matches = []
        for g in games:
            nt = normalize_title(g["title"])
            if q and (q in nt or (qwords and qwords <= set(nt.split()))):
                matches.append(g)
        if not matches:
            await tg_api(
                "sendMessage",
                chat_id=chat_id,
                text=f"Nessun gioco gratis trovato per «{query}». Prova con un nome diverso o /giochi per vedere tutti.",
            )
            return
        for g in matches[:8]:
            try:
                await send_game(chat_id, g)
            except Exception as e:
                log.warning("send_game (cerca) fallito: %s", e)
    except Exception as e:
        log.exception("Errore /cerca: %s", e)


async def _update_keyboard(chat_id: int, msg_id: int, current: set[str], cb_id: str):
    try:
        await tg_api(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=msg_id,
            reply_markup=platforms_keyboard(current),
        )
    except Exception as e:
        log.warning("editMessageReplyMarkup fallita: %s", e)
    try:
        await tg_api("answerCallbackQuery", callback_query_id=cb_id)
    except Exception:
        pass


async def _finish_setup(chat_id: int, msg_id: int, chosen: str, cb_id: str):
    try:
        await tg_api(
            "editMessageText",
            chat_id=chat_id,
            message_id=msg_id,
            text=(
                f"✅ <b>Piattaforme salvate</b>\n\nRiceverai giochi gratis per: <b>{html_escape(chosen)}</b>\n\n"
                "Comandi:\n"
                "• /giochi – giochi disponibili ora\n"
                "• /piattaforme – cambia piattaforme\n"
                "• /generi – filtra per genere\n"
                "• /status – stato iscrizione\n"
                "• /stop – disiscriviti"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        log.warning("editMessageText fallita: %s", e)
    try:
        await tg_api("answerCallbackQuery", callback_query_id=cb_id, text="Salvato ✓")
    except Exception:
        pass


async def _update_genres_keyboard(chat_id: int, msg_id: int, current: set[str], cb_id: str):
    try:
        await tg_api(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=msg_id,
            reply_markup=genres_keyboard(current),
        )
    except Exception as e:
        log.warning("editMessageReplyMarkup gen fallita: %s", e)
    try:
        await tg_api("answerCallbackQuery", callback_query_id=cb_id)
    except Exception:
        pass


async def _finish_genres(chat_id: int, msg_id: int, chosen: str, cb_id: str):
    try:
        await tg_api(
            "editMessageText",
            chat_id=chat_id,
            message_id=msg_id,
            text=f"✅ <b>Generi salvati</b>\n\nFiltro generi: <b>{html_escape(chosen)}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        log.warning("editMessageText gen fallita: %s", e)
    try:
        await tg_api("answerCallbackQuery", callback_query_id=cb_id, text="Generi salvati ✓")
    except Exception:
        pass


async def _update_content_keyboard(chat_id: int, msg_id: int, current: set[str], cb_id: str):
    try:
        await tg_api(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=msg_id,
            reply_markup=content_keyboard(current),
        )
    except Exception as e:
        log.warning("editMessageReplyMarkup cont fallita: %s", e)
    try:
        await tg_api("answerCallbackQuery", callback_query_id=cb_id)
    except Exception:
        pass


async def _finish_content(chat_id: int, msg_id: int, chosen: str, cb_id: str):
    try:
        await tg_api(
            "editMessageText",
            chat_id=chat_id,
            message_id=msg_id,
            text=f"✅ <b>Contenuti salvati</b>\n\nRiceverai: <b>{html_escape(chosen)}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        log.warning("editMessageText cont fallita: %s", e)
    try:
        await tg_api("answerCallbackQuery", callback_query_id=cb_id, text="Contenuti salvati ✓")
    except Exception:
        pass


async def broadcast_new_games(seed_only: bool = False):
    try:
        log.info("Controllo giochi gratuiti…")
        games = await fetch_all_games()
        new = [g for g in games if g["id"] not in state.sent]
        if not new:
            log.info("Nessun nuovo gioco.")
            return
        if seed_only:
            # Primo avvio con lista 'sent' vuota: marca i giochi già disponibili
            # come 'visti' SENZA inviarli, per non spammare le chat ad ogni deploy.
            state.mark_sent([g["id"] for g in new])
            log.info("Seed iniziale: marcati %d giochi come già visti (nessun invio)", len(new))
            return
        # Lock anti-duplicati: se un'altra istanza sta già inviando, salta.
        if not state.acquire_broadcast_lock():
            return
        # Marca SUBITO come inviati (prima dell'invio): se un'altra istanza parte
        # adesso, vedrà questi giochi come già visti e non li rimanderà.
        state.mark_sent([g["id"] for g in new])
        log.info("Trovati %d nuovi giochi, controllo invio a %d chat", len(new), len(state.chats))
        dead = []
        for chat_id in list(state.chats):
            prefs = state.get_prefs(chat_id)
            chat_games = filter_by_content(new, state.get_content(chat_id))
            chat_games = filter_by_categories(chat_games, prefs)
            chat_games = filter_by_genres(chat_games, state.get_genres(chat_id))
            if not chat_games:
                continue
            for g in chat_games:
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
    except Exception as e:
        log.exception("Errore broadcast: %s", e)


async def periodic_broadcaster():
    await asyncio.sleep(15)
    # Al primo giro dopo l'avvio: se 'sent' è vuoto (es. perso al deploy o errore
    # Firebase), facciamo un seed silenzioso invece di rimandare tutti i giochi.
    first_run_seed = len(state.sent) == 0
    if first_run_seed:
        log.info("Avvio con lista 'sent' vuota: primo giro sarà seed silenzioso (no spam)")
    while True:
        await broadcast_new_games(seed_only=first_run_seed)
        first_run_seed = False
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
            allowed_updates=["message", "edited_message", "channel_post", "my_chat_member", "callback_query"],
            drop_pending_updates=False,
        )
        log.info("setWebhook: %s -> %s", url, resp)
    except Exception as e:
        log.error("setWebhook fallita: %s", e)


async def setup_commands():
    try:
        await tg_api("setMyCommands", commands=[
            {"command": "giochi", "description": "Mostra i giochi/contenuti gratis ora"},
            {"command": "cerca", "description": "Cerca un gioco gratis per nome"},
            {"command": "prossimi", "description": "Giochi gratis Epic in arrivo"},
            {"command": "piattaforme", "description": "Scegli PC / Console / Android"},
            {"command": "contenuti", "description": "Giochi, DLC, Abbonamenti"},
            {"command": "generi", "description": "Filtra per genere"},
            {"command": "status", "description": "Stato iscrizione e filtri"},
            {"command": "stop", "description": "Disiscrivi questa chat"},
            {"command": "start", "description": "Iscrivi e configura"},
        ])
    except Exception as e:
        log.warning("setMyCommands fallita: %s", e)


async def on_startup(app: web.Application):
    app["broadcaster"] = asyncio.create_task(periodic_broadcaster())
    asyncio.create_task(setup_webhook(app))
    asyncio.create_task(setup_commands())
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
