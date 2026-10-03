import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from nello_downloader import download_youtube, NelloExtractionError


class NelloTests(unittest.IsolatedAsyncioTestCase):
    async def test_space_forwards_optional_netscape_cookies(self):
        session = MagicMock()
        response = session.post.return_value.__aenter__.return_value
        response.json = AsyncMock(return_value={"success": False, "auth_issue": "access_check"})
        fake = "# Netscape HTTP Cookie File\r\n# test-only\r\n"
        with patch.dict(os.environ, {"NELLO_YOUTUBE_URL": "https://example.hf.space", "NELLO_YOUTUBE_TOKEN": "test-only", "NELLO_YOUTUBE_COOKIES": fake}):
            with self.assertRaises(NelloExtractionError):
                await download_youtube(session, "https://youtu.be/abcdefghijk", Path("unused.mp4"), 1000)
        self.assertEqual(session.post.call_args.kwargs["json"]["cookies"], fake.replace("\r\n", "\n"))
    async def test_space_failure_exposes_fixed_reason_without_raw_error(self):
        session = MagicMock()
        response = session.post.return_value.__aenter__.return_value
        response.json = AsyncMock(return_value={"success": False, "auth_issue": "access_check", "error": "private server details"})
        with patch.dict(os.environ, {"NELLO_YOUTUBE_URL": "https://example.hf.space", "NELLO_YOUTUBE_TOKEN": "test-only"}):
            with self.assertRaisesRegex(NelloExtractionError, "^access_check$"):
                await download_youtube(session, "https://youtu.be/abcdefghijk", Path("unused.mp4"), 1000)
        session.get.assert_not_called()
    async def test_space_download_and_cleanup_even_when_too_large(self):
        for limit in (1000, 2):
            session = MagicMock()
            ident = "12345678-1234-1234-1234-123456789abc"
            posted = session.post.return_value.__aenter__.return_value
            posted.json = AsyncMock(return_value={"success": True, "artifact": ident})
            media = session.get.return_value.__aenter__.return_value
            async def chunks(size):
                yield b"video-content"
            media.content.iter_chunked = chunks
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
                    "NELLO_YOUTUBE_URL": "https://bicimonello-nello-youtube.hf.space",
                    "NELLO_YOUTUBE_TOKEN": "test-only"}):
                destination = Path(folder) / "video.mp4"
                if limit == 2:
                    with self.assertRaises(ValueError):
                        await download_youtube(session, "https://youtu.be/abcdefghijk", destination, limit)
                else:
                    await download_youtube(session, "https://youtu.be/abcdefghijk", destination, limit)
                    self.assertEqual(destination.read_bytes(), b"video-content")
            self.assertTrue(session.post.call_args.args[0].endswith("/api/youtube"))
            self.assertEqual(session.post.call_args.kwargs["headers"], {"x-nello-token": "test-only"})
            self.assertEqual(session.post.call_args.kwargs["json"]["max_duration"], 180)
            self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
            self.assertTrue(session.delete.call_args.args[0].endswith("/api/media/" + ident))

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
