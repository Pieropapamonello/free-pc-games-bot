"""Client for the authenticated /jobs protocol in Pieropapamonello/Nello."""
import asyncio
import os
import uuid
from urllib.parse import urlparse

import aiohttp


async def download_youtube(session, url, destination, max_bytes):
    base = os.getenv("DOWNLOADER_URL", "").rstrip("/")
    token = os.getenv("DOWNLOADER_TOKEN", "")
    if not base or not token:
        raise RuntimeError("Downloader Nello non configurato")
    if urlparse(base).scheme != "https":
        raise ValueError("Il downloader Nello richiede HTTPS")
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
