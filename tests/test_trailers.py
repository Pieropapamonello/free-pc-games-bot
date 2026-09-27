import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import trailers
import test_start


class MetadataTests(unittest.TestCase):
    def test_duration_boundary_and_unknown_values(self):
        for value in (1, 179.9, 180, "180"):
            self.assertTrue(trailers.valid_duration(value))
        for value in (0, -1, 180.01, None, "invalid", float("nan"), float("inf")):
            self.assertFalse(trailers.valid_duration(value))

    def test_language_must_have_evidence(self):
        self.assertEqual(trailers.language_of({"language": "it-IT"}), "it")
        self.assertEqual(trailers.language_of({"name": "Trailer English"}), "en")
        self.assertIsNone(trailers.language_of({"name": "Launch trailer", "locale": "italian"}))
        self.assertIsNone(trailers.language_of({"language": "fr", "title": "English trailer"}))

    def test_official_keyword_alone_does_not_prove_ownership(self):
        info = {"title": "Example Official Trailer", "channel": "Publisher",
                "channel_is_verified": True, "duration": 120, "language": "en"}
        self.assertTrue(trailers.official_youtube(info, "Example", ["Publisher"]))
        for change in ({"channel": "Fan Channel"}, {"channel_is_verified": False},
                       {"title": "Another Official Trailer"}, {"title": "Example 2 Official Trailer"}, {"duration": 181},
                       {"language": None}, {"is_live": True}):
            self.assertFalse(trailers.official_youtube(dict(info, **change), "Example", ["Publisher"]))


class Response:
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.data


class UploadSession:
    def __init__(self, responses=None):
        self.uploads = []
        self.responses = responses or [{"ok": True, "result": {"video": {"file_id": "cached-video"}}}]

    def post(self, url, *, data, timeout):
        fields = {header["name"]: value for header, _, value in data._fields}
        self.uploads.append({**fields, "video": fields["video"].read()})
        return Response(self.responses.pop(0))


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_steam_streaming_trailers_are_discovered(self):
        service = trailers.TrailerService()
        candidates = await service.candidates("Example", {"official_match": True,
            "movies": [{"name": "Trailer Italiano", "hls_h264": "https://example.com/video.m3u8"}],
            "owners": []})
        self.assertEqual(candidates[0]["kind"], "steam_stream")
        self.assertEqual(candidates[0]["language"], "it")

    async def test_italian_preferred_and_file_uploaded_then_reused(self):
        service = trailers.TrailerService()
        session = UploadSession()
        api = AsyncMock(return_value={"ok": True})
        candidates = [{"language": "en"}, {"language": "it"}]

        async def prepare(candidate, session, directory, index):
            path = Path(directory) / f"{index}.mp4"
            path.write_bytes(candidate["language"].encode())
            return {"path": path, "language": candidate["language"], "duration": 120}

        with patch.object(service, "candidates", AsyncMock(return_value=candidates)), \
             patch.object(service, "prepare", prepare):
            self.assertTrue(await service.send(1, "Example", "Caption", {}, session, "api", api))
            self.assertEqual(session.uploads[0]["video"], b"it")
            self.assertEqual(session.uploads[0]["caption"], "Caption")
            self.assertTrue(await service.send(2, "Example", "Caption", {}, session, "api", api))
            self.assertEqual(len(session.uploads), 1)
            self.assertEqual(api.await_args.kwargs["video"], "cached-video")

    async def test_english_used_if_italian_cannot_be_prepared(self):
        service = trailers.TrailerService()
        session = UploadSession()

        async def prepare(candidate, session, directory, index):
            if candidate["language"] == "it":
                return None
            path = Path(directory) / "en.mp4"
            path.write_bytes(b"english")
            return {"path": path, "language": "en", "duration": 180}

        with patch.object(service, "candidates", AsyncMock(return_value=[{"language": "it"}, {"language": "en"}])), \
             patch.object(service, "prepare", prepare):
            self.assertTrue(await service.send(1, "Example", "Caption", {}, session, "api", AsyncMock()))
        self.assertEqual(session.uploads[0]["video"], b"english")

    async def test_no_eligible_video_sends_nothing(self):
        service = trailers.TrailerService()
        session = UploadSession()
        api = AsyncMock()
        with patch.object(service, "candidates", AsyncMock(return_value=[])):
            self.assertFalse(await service.send(1, "Example", "Caption", {}, session, "api", api))
        self.assertEqual(session.uploads, [])
        api.assert_not_awaited()


class RetryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_start.StartRoutingTests.asyncSetUp

    async def test_unavailable_trailer_is_not_marked_as_sent(self):
        bot = test_start.bot
        game = dict(self.games[0], id="pending")
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=[game])), \
             patch.object(bot, "send_game", AsyncMock(return_value=False)):
            await bot.broadcast_new_games()
        self.assertNotIn("pending", bot.state.sent)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
class RealMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_download_conversion_and_duration_validation(self):
        from aiohttp import ClientSession, web
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.mp4"
            await trailers.command("ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                                   "-i", "color=s=32x32:r=10:d=2", "-f", "lavfi",
                                   "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
                                   "-c:a", "aac", "-metadata:s:a:0", "language=eng", source)
            async def serve(request):
                return web.FileResponse(source)
            app = web.Application()
            app.router.add_get("/sample.mp4", serve)
            runner = web.AppRunner(app)
            await runner.setup()
            try:
                site = web.TCPSite(runner, "127.0.0.1", 0)
                await site.start()
                port = site._server.sockets[0].getsockname()[1]
                async with ClientSession() as session:
                    prepared = await trailers.TrailerService().prepare(
                        {"kind": "steam", "language": "en", "url": f"http://127.0.0.1:{port}/sample.mp4"},
                        session, directory, 1)
                self.assertIsNotNone(prepared)
                self.assertEqual(prepared["language"], "en")
                self.assertLessEqual(prepared["duration"], 180)
                self.assertLess(prepared["path"].stat().st_size, trailers.MAX_UPLOAD)
                info = await trailers.probe(prepared["path"])
                self.assertEqual(info["streams"][0]["codec_name"], "h264")
            finally:
                await runner.cleanup()

    async def test_actual_file_duration_is_checked_including_overlong_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            for length in (2, 181):
                path = Path(directory) / f"{length}.mp4"
                await trailers.command("ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                                       "-i", f"color=s=16x16:r=1:d={length}",
                                       "-c:v", "libx264", path)
                info = await trailers.probe(path)
                if length <= 180:
                    self.assertEqual(trailers.media_duration(info), length)
                else:
                    self.assertIsNone(trailers.media_duration(info))
