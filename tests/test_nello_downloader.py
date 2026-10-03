import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from nello_downloader import download_youtube


class NelloTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_downloads_media_and_deletes_job(self):
        session = MagicMock()
        posted = session.post.return_value.__aenter__.return_value
        posted.raise_for_status = MagicMock()
        status = MagicMock()
        status.json = AsyncMock(return_value={"state": "done", "result": {
            "success": True, "media": [{"index": 0, "suffix": ".mp4"}]}})
        media = MagicMock()
        async def chunks(size):
            yield b"video-content"
        media.content.iter_chunked = chunks
        contexts = []
        for response in (status, media):
            context = MagicMock()
            context.__aenter__ = AsyncMock(return_value=response)
            context.__aexit__ = AsyncMock()
            contexts.append(context)
        session.get.side_effect = contexts
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
                "DOWNLOADER_URL": "https://nello.example", "DOWNLOADER_TOKEN": "test-only"}):
            destination = Path(folder) / "video.mp4"
            await download_youtube(session, "https://www.youtube.com/watch?v=abcdefghijk", destination, 1000)
            self.assertEqual(destination.read_bytes(), b"video-content")
        self.assertEqual(session.post.call_args.kwargs["json"]["kind"], "video")
        self.assertEqual(session.post.call_args.kwargs["headers"]["Authorization"], "Bearer test-only")
        session.delete.assert_called_once()

    async def test_missing_configuration_does_not_send_network_requests(self):
        session = MagicMock()
        with patch.dict(os.environ, {"DOWNLOADER_URL": "", "DOWNLOADER_TOKEN": ""}):
            with self.assertRaises(RuntimeError):
                await download_youtube(session, "https://youtube.com/", Path("unused.mp4"), 1000)
        session.post.assert_not_called()
