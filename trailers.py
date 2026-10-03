"""Fail-closed official trailer selection and actual Telegram file uploads."""
import asyncio
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
import weakref
from pathlib import Path

import aiohttp
from nello_downloader import download_youtube, configured as nello_configured

log = logging.getLogger(__name__)
MAX_SECONDS = 180
MAX_DOWNLOAD = 100_000_000
MAX_UPLOAD = 45_000_000
# Publisher's launch announcement links this exact base-game trailer:
# https://www.gamespress.com/fr/GigaBash-Kaijus-vs-Heroes-Arena-Brawler-is-out-now-on-PC-PlayStation
PUBLISHER_TRAILERS = {"gigabash": ("kJUeC8NqQqo", "Passion Republic Games")}


def language_of(metadata):
    language = str(metadata.get("language") or "").lower().split("-")[0]
    if language:
        return {"ita": "it", "eng": "en", "it": "it", "en": "en"}.get(language)
    name = str(metadata.get("title") or metadata.get("name") or "").lower()
    # The page locale and the search query are NOT evidence of video language.
    if re.search(r"\b(italiano|italian|ita)\b", name):
        return "it"
    if re.search(r"\b(english|inglese|eng)\b", name):
        return "en"
    return None


def valid_duration(value):
    try:
        seconds = float(value)
        return math.isfinite(seconds) and 0 < seconds <= MAX_SECONDS
    except (TypeError, ValueError):
        return False


def identity(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def title_matches(name, title):
    words = re.findall(r"\w+", name.casefold())
    wanted = re.findall(r"\w+", title.casefold())
    marketing = set("official trailer launch reveal announcement gameplay cinematic story teaser "
                    "english inglese eng italian italiano ita hd 4k 1080p 60fps "
                    "pc ps4 ps5 xbox one series xs nintendo switch steam epic games store".split())
    if not wanted:
        return False
    for index in range(len(words) - len(wanted) + 1):
        if words[index:index + len(wanted)] == wanted:
            remaining = words[:index] + words[index + len(wanted):]
            if remaining and all(word in marketing for word in remaining):
                return True
    return False


def official_youtube(info, title, owners):
    name = str(info.get("title") or "")
    channel = identity(info.get("channel"))
    return bool(
        info.get("channel_is_verified") is True
        and channel and channel in {identity(owner) for owner in owners if owner}
        and title_matches(name, title)
        and re.search(r"\btrailer\b", name, re.I)
        and not re.search(r"fan.?made|reaction|concept|walkthrough", name, re.I)
        and not info.get("is_live")
        and valid_duration(info.get("duration"))
        and language_of(info) in ("it", "en")
    )


async def command(*args, timeout=90):
    process = await asyncio.create_subprocess_exec(
        *map(str, args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[-400:])
    return stdout


async def probe(path):
    return json.loads(await command("ffprobe", "-v", "error", "-show_format",
                                    "-show_streams", "-of", "json", path, timeout=20))


def media_duration(info):
    values = [info.get("format", {}).get("duration")]
    values += [s["duration"] for s in info.get("streams", []) if s.get("duration")]
    values = [value for value in values if value not in (None, "", "N/A")]
    if not values or any(not valid_duration(value) for value in values):
        return None
    return max(float(value) for value in values)


class TrailerService:
    def __init__(self):
        self.locks = weakref.WeakValueDictionary()
        self.slots = asyncio.Semaphore(2)
        self.cache = {}  # file_id or negative result; bounded, expires after 10 min/6 h
        self.gameplay_cache = {}

    async def gameplay(self, title):
        async with self.slots:
            return await self._gameplay(title)

    async def _gameplay(self, title):
        cached = self.gameplay_cache.get(title)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        fallback = None
        wanted = re.findall(r"\w+", title.casefold())
        for query in (f"{title} gameplay italiano", f"{title} gameplay"):
            try:
                results = json.loads(await command(sys.executable, "-m", "yt_dlp",
                    "--ignore-config", "--no-warnings", "--js-runtimes", "node",
                    "--flat-playlist", "--dump-single-json", "--socket-timeout", "10",
                    f"ytsearch3:{query}", timeout=35))
                for entry in results.get("entries") or []:
                    video_id = entry.get("id", "")
                    name = entry.get("title") or ""
                    words = re.findall(r"\w+", name.casefold())
                    matches = any(words[i:i + len(wanted)] == wanted for i in range(len(words)))
                    if not wanted or not matches or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                        continue
                    if not re.search(r"\b(gameplay|walkthrough|lets play|let.s play)\b", name, re.I):
                        continue
                    if entry.get("is_live"):
                        continue
                    result = {"url": f"https://www.youtube.com/watch?v={video_id}", "language": language_of(entry)}
                    fallback = fallback or result
                    if result["language"] == "it":
                        fallback = result
                        break
                if fallback and fallback.get("language") == "it":
                    break
            except Exception as exc:
                log.info("Ricerca gameplay %s: %s", title, type(exc).__name__)
        if len(self.gameplay_cache) >= 128:
            self.gameplay_cache.pop(next(iter(self.gameplay_cache)))
        self.gameplay_cache[title] = (time.monotonic() + (21600 if fallback else 600), fallback)
        return fallback

    async def candidates(self, title, steam):
        candidates = []
        # Exact Steam identity only: its movies are uploaded by the publisher.
        if steam and steam.get("official_match"):
            for movie in steam.get("movies", []):
                if not re.search(r"\btrailer\b", movie.get("name", ""), re.I):
                    continue
                url = (movie.get("mp4") or {}).get("480") or (movie.get("mp4") or {}).get("max")
                kind = "steam"
                if not url:
                    url = movie.get("hls_h264") or movie.get("dash_h264")
                    kind = "steam_stream"
                if url:
                    candidates.append({"url": "https:" + url if url.startswith("//") else url,
                                       "language": language_of(movie), "kind": kind})
        owners = (steam or {}).get("owners", []) if (steam or {}).get("official_match") else []
        linked = PUBLISHER_TRAILERS.get(identity(title))
        if linked and nello_configured() and identity(linked[1]) in {identity(owner) for owner in owners}:
            candidates.append({"url": "https://www.youtube.com/watch?v=" + linked[0],
                               "language": None, "kind": "youtube", "metadata_pending": True,
                               "video_id": linked[0], "game_title": title, "owners": [linked[1]]})
        if owners:
            # Inspect a bounded number of results; no arbitrary first-result fallback.
            seen = {linked[0]} if linked else set()
            for language in ("italiano", ""):
                try:
                    results = json.loads(await command(
                        sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-warnings",
                        "--js-runtimes", "node", "--flat-playlist", "--dump-single-json", "--socket-timeout", "10",
                        f'ytsearch8:"{title}" "official launch trailer" {language}', timeout=45))
                    for entry in results.get("entries") or []:
                        video_id = entry.get("id", "")
                        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or video_id in seen:
                            continue
                        seen.add(video_id)
                        if not title_matches(entry.get("title", ""), title):
                            log.info("Trailer YouTube scartato per %s: titolo=%s", title, entry.get("title", ""))
                            continue
                        # Skip channels already known not to belong to the game owners.
                        if identity(entry.get("channel")) not in {identity(o) for o in owners}:
                            continue
                        url = f"https://www.youtube.com/watch?v={video_id}"
                        try:
                            info = json.loads(await command(
                                sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-warnings",
                                "--js-runtimes", "node", "--skip-download", "--dump-single-json", "--socket-timeout", "10",
                                "--no-playlist", url, timeout=45))
                        except Exception as exc:
                            log.info("Metadati YouTube locali non disponibili per %s: %s", title, type(exc).__name__)
                            # The trusted channel/title come from the search;
                            # duration, media identity and language must still
                            # be checked against the downloader response.
                            if nello_configured() and entry.get("channel_is_verified") is True and title_matches(entry.get("title", ""), title) and re.search(r"\btrailer\b", entry.get("title", ""), re.I):
                                candidates.append({"url": url, "language": language_of(entry),
                                                   "kind": "youtube", "metadata_pending": True,
                                                   "video_id": video_id, "game_title": title, "owners": owners})
                            continue
                        if "channel_is_verified" not in info:
                            info["channel_is_verified"] = entry.get("channel_is_verified")
                        if official_youtube(info, title, owners):
                            candidates.append({"url": url, "language": language_of(info),
                                               "kind": "youtube", "duration": info["duration"]})
                except Exception as exc:
                    log.info("Ricerca trailer ufficiale %s: %s", title, exc)
        return candidates

    async def prepare(self, candidate, session, directory, index):
        source = Path(directory) / f"source-{index}.mp4"
        expected_duration = candidate.get("duration")
        if candidate["kind"] == "youtube":
            if nello_configured():
                try:
                    metadata = await download_youtube(session, candidate["url"], source, MAX_DOWNLOAD)
                    if candidate.get("metadata_pending"):
                        metadata = metadata or {}
                        checked = {"title": metadata.get("title"), "channel": metadata.get("uploader"),
                                   "channel_is_verified": True, "duration": metadata.get("duration"),
                                   "language": (metadata.get("source_info") or {}).get("language")}
                        if metadata.get("id") != candidate["video_id"] or not official_youtube(checked, candidate["game_title"], candidate["owners"]):
                            raise ValueError("Metadati Nello non confermano il trailer ufficiale")
                        candidate = dict(candidate, language=language_of(checked), duration=metadata["duration"])
                        expected_duration = metadata["duration"]
                    candidate = dict(candidate, kind="nello")
                except Exception as exc:
                    source.unlink(missing_ok=True)
                    log.info("Downloader Nello non disponibile per %s: errore=%s, HTTP=%s",
                             candidate.get("game_title", "trailer"), type(exc).__name__, getattr(exc, "status", None))
                    if candidate.get("metadata_pending"):
                        return None
        if candidate["kind"] == "youtube":
            lang = candidate["language"]
            await command(
                sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-warnings",
                "--js-runtimes", "node", "--no-playlist", "--socket-timeout", "15", "--retries", "1",
                "--max-filesize", str(MAX_DOWNLOAD),
                "--match-filters", "duration <= 180 & !is_live",
                "-f", (f"best[ext=mp4][height<=720][language^=?{lang}]/"
                       f"bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a][language^=?{lang}]"),
                "--merge-output-format", "mp4",
                "-o", source, candidate["url"], timeout=120)
        elif candidate["kind"] == "steam_stream":
            # Modern Steam store trailers use HLS/DASH instead of MP4 URLs.
            # Validate the entire manifest before downloading, never trim it.
            remote = await probe(candidate["url"])
            expected_duration = media_duration(remote)
            if expected_duration is None:
                return None
            await command("ffmpeg", "-v", "error", "-y", "-rw_timeout", "20000000",
                          "-i", candidate["url"], "-map", "0:v:0", "-map", "0:a:0?",
                          "-c", "copy", "-fs", str(MAX_DOWNLOAD + 1), source, timeout=120)
        elif candidate["kind"] != "nello":
            async with session.get(candidate["url"], timeout=aiohttp.ClientTimeout(total=90)) as response:
                response.raise_for_status()
                if response.content_length and response.content_length > MAX_DOWNLOAD:
                    return None
                size = 0
                with source.open("wb") as output:
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > MAX_DOWNLOAD:
                            return None
                        output.write(chunk)
        if not source.exists() or source.stat().st_size > MAX_DOWNLOAD:
            return None
        details = await probe(source)
        duration = media_duration(details)
        if duration is None or not any(s.get("codec_type") == "video" for s in details.get("streams", [])):
            log.info("Trailer scartato: durata oltre 180 secondi, sconosciuta o video assente (%s)", candidate["kind"])
            return None
        if expected_duration is not None and abs(duration - float(expected_duration)) > 0.5:
            return None
        audio = [s for s in details.get("streams", []) if s.get("codec_type") == "audio"]
        raw_languages = [str(s.get("tags", {}).get("language") or "").lower() for s in audio]
        if any(raw not in ("", "und") and language_of({"language": raw}) is None for raw in raw_languages):
            return None
        tags = [language_of({"language": s.get("tags", {}).get("language")}) for s in audio]
        language = candidate.get("language")
        if language and any(tag and tag != language for tag in tags):
            return None
        language = language or (tags[0] if tags and len(set(tags)) == 1 else None)
        if language not in ("it", "en"):
            log.info("Trailer scartato: lingua italiana/inglese non verificabile (%s)", candidate["kind"])
            return None
        target = Path(directory) / f"trailer-{index}.mp4"
        # Full trailer, not a 3-minute cut of a longer video. Make a bounded MP4.
        await command("ffmpeg", "-v", "error", "-y", "-i", source,
                      "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=-2:480",
                      "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-b:v", "1200k",
                      "-maxrate", "1400k", "-bufsize", "2800k", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", target,
                      timeout=180)
        checked = media_duration(await probe(target))
        if checked is None or abs(checked - duration) > 0.5 or target.stat().st_size > MAX_UPLOAD:
            return None
        source.unlink(missing_ok=True)
        return {"path": target, "language": language, "duration": checked}

    async def send(self, chat_id, title, caption, steam, session, api, tg_api, *, message_id=None):
        lock = self.locks.setdefault(title, asyncio.Lock())
        async with lock, self.slots:
            cached = self.cache.get(title)
            if cached and cached["expires"] > time.monotonic():
                if not cached.get("file_id"):
                    return False
                if message_id is None:
                    result = await tg_api("sendVideo", chat_id=chat_id, video=cached["file_id"],
                                          caption=caption, parse_mode="HTML", supports_streaming=True)
                else:
                    result = await tg_api("editMessageMedia", chat_id=chat_id, message_id=message_id,
                                          media={"type": "video", "media": cached["file_id"],
                                                 "caption": caption, "parse_mode": "HTML",
                                                 "supports_streaming": True})
                if not result.get("ok"):
                    description = result.get("description", "Invio trailer fallito")
                    if result.get("error_code") == 400 and any(word in description.lower() for word in ("file_id", "file identifier", "file reference")):
                        self.cache.pop(title, None)
                    else:
                        # A chat-specific error or rate limit says nothing about
                        # the validity of the shared Telegram video.
                        raise RuntimeError(description)
                else:
                    return True
            if len(self.cache) >= 128:
                self.cache.pop(next(iter(self.cache)))
            candidates = await self.candidates(title, steam)
            log.info("Trailer %s: candidati=%s, identita_store=%s, downloader_configurato=%s",
                     title, len(candidates), bool(steam and steam.get("official_match")), nello_configured())
            # Prepare all unlabelled Steam videos before choosing English: tags
            # inside the file may identify an Italian trailer.
            with tempfile.TemporaryDirectory(prefix="official-trailer-") as directory:
                prepared = []
                for index, candidate in enumerate(sorted(candidates, key=lambda c: (c.get("language") != "it", c.get("kind") != "youtube"))[:8]):
                    try:
                        trailer = await self.prepare(candidate, session, directory, index)
                        if trailer:
                            prepared.append(trailer)
                    except Exception as exc:
                        log.info("Trailer scartato per %s: %s", title, exc)
                delivery_error = None
                for trailer in sorted(prepared, key=lambda t: t["language"] != "it"):
                    form = aiohttp.FormData()
                    fields = {"chat_id": str(chat_id), "caption": caption,
                              "parse_mode": "HTML", "supports_streaming": "true",
                              "duration": str(math.ceil(trailer["duration"]))}
                    method = "sendVideo"
                    if message_id is not None:
                        method = "editMessageMedia"
                        fields = {"chat_id": str(chat_id), "message_id": str(message_id),
                                  "media": json.dumps({"type": "video", "media": "attach://video",
                                                       "caption": caption, "parse_mode": "HTML",
                                                       "supports_streaming": True,
                                                       "duration": math.ceil(trailer["duration"])})}
                    for key, value in fields.items():
                        form.add_field(key, value)
                    with trailer["path"].open("rb") as video:
                        form.add_field("video", video, filename="trailer.mp4", content_type="video/mp4")
                        async with session.post(f"{api}/{method}", data=form,
                                                timeout=aiohttp.ClientTimeout(total=150)) as response:
                            result = await response.json()
                    if result.get("ok"):
                        file_id = (result.get("result", {}).get("video") or {}).get("file_id")
                        if file_id:
                            self.cache[title] = {"file_id": file_id, "expires": time.monotonic() + 21600}
                        return True
                    delivery_error = result.get("description", "Invio trailer fallito")
                    if result.get("error_code") in (403, 429) or result.get("error_code", 0) >= 500:
                        raise RuntimeError(delivery_error)
                    log.info("Telegram rifiuta il trailer di %s: %s", title, result.get("description"))
                if delivery_error:
                    raise RuntimeError(delivery_error)
            self.cache[title] = {"expires": time.monotonic() + 600}
            return False
