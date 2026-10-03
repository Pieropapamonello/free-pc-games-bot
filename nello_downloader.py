"""Clients for the Nello YouTube Space and legacy Nello jobs service."""
import asyncio
import os
import uuid
from urllib.parse import urlparse

import aiohttp


class NelloExtractionError(RuntimeError):
    """Only fixed, non-sensitive service outcome codes are exposed in logs."""


def configured():
    return bool((os.getenv("NELLO_YOUTUBE_URL") and os.getenv("NELLO_YOUTUBE_TOKEN"))
                or (os.getenv("DOWNLOADER_URL") and os.getenv("DOWNLOADER_TOKEN")))


async def download_space(session, base, token, url, destination, max_bytes):
    headers = {"x-nello-token": token}
    ident = None
    try:
        async with asyncio.timeout(150):
            async with session.post(base + "/api/youtube", headers=headers,
                    json={"url": url, "kind": "video", "max_duration": 180},
                    timeout=aiohttp.ClientTimeout(total=120), allow_redirects=False) as response:
                response.raise_for_status()
                result = await response.json()
            if not result.get("success"):
                reason = result.get("auth_issue")
                if reason not in ("access_check", "download_failed"):
                    reason = "duration_limit" if result.get("skip_long") else "extraction_failed"
                raise NelloExtractionError(reason)
            ident = str(uuid.UUID(result["artifact"]))
            size = 0
            async with session.get(base + "/api/media/" + ident, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60), allow_redirects=False) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError("Trailer Nello troppo grande")
                        output.write(chunk)
            return result
    finally:
        if ident:
            try:
                async with session.delete(base + "/api/media/" + ident, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=5), allow_redirects=False):
                    pass
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass


async def download_youtube(session, url, destination, max_bytes):
    space = bool(os.getenv("NELLO_YOUTUBE_URL"))
    base = os.getenv("NELLO_YOUTUBE_URL" if space else "DOWNLOADER_URL", "").rstrip("/")
    token = os.getenv("NELLO_YOUTUBE_TOKEN" if space else "DOWNLOADER_TOKEN", "")
    if not base or not token:
        raise RuntimeError("Downloader Nello non configurato")
    if urlparse(base).scheme != "https":
        raise ValueError("Il downloader Nello richiede HTTPS")
    if space or (urlparse(base).hostname or "").endswith(".hf.space"):
        return await download_space(session, base, token, url, destination, max_bytes)
    ident = str(uuid.uuid4())
    headers = {"Authorization": "Bearer " + token}
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with asyncio.timeout(150):
            async with session.post(base + "/jobs", headers=headers, timeout=timeout,
                    json={"id": ident, "url": url, "kind": "video", "target": "",
                          "max_bytes": max_bytes}, allow_redirects=False) as response:
                response.raise_for_status()
            while True:
                async with session.get(base + "/jobs/" + ident, headers=headers,
                        timeout=timeout, allow_redirects=False) as response:
                    response.raise_for_status()
                    job = await response.json()
                if job.get("state") == "done":
                    break
                await asyncio.sleep(2)
            result = job.get("result") or {}
            media = result.get("media") or []
            if not result.get("success") or not media:
                raise RuntimeError("Nello non ha estratto il trailer")
            index = int(media[0]["index"])
            size = 0
            async with session.get(f"{base}/jobs/{ident}/files/{index}", headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60), allow_redirects=False) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError("Trailer Nello troppo grande")
                        output.write(chunk)
    finally:
        try:
            async with session.delete(base + "/jobs/" + ident, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5), allow_redirects=False):
                pass
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
