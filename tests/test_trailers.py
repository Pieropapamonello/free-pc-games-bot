import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import trailers
import test_start


class MetadataTests(unittest.TestCase):
    def test_game_trailer_accepts_platform_suffix_but_excludes_dlc(self):
        self.assertTrue(trailers.title_matches("GigaBash Official Launch Trailer | Nintendo Switch", "GigaBash"))
        self.assertFalse(trailers.title_matches("GigaBash & Godzilla DLC - Launch Trailer | Nintendo Switch", "GigaBash"))
        self.assertFalse(trailers.title_matches("GigaBash - Final Ascension DLC Official Trailer", "GigaBash"))
    def test_title_words_are_not_removed_as_marketing_labels(self):
        info = {"title": "Cave Story Official Launch Trailer", "channel": "Publisher",
                "channel_is_verified": True, "duration": 120, "language": "en"}
        self.assertTrue(trailers.official_youtube(info, "Cave Story", ["Publisher"]))

    def test_known_stream_duration_without_container_duration(self):
        self.assertEqual(trailers.media_duration({"streams": [{"duration": "120"}]}), 120)
        self.assertIsNone(trailers.media_duration({"streams": [{"duration": "181"}]}))

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
        self.urls = []
        self.responses = responses or [{"ok": True, "result": {"video": {"file_id": "cached-video"}}}]

    def post(self, url, *, data, timeout):
        self.urls.append(url)
        fields = {header["name"]: value for header, _, value in data._fields}
        self.uploads.append({**fields, "video": fields["video"].read()})
        return Response(self.responses.pop(0))


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_publisher_linked_trailer_reaches_downloader_without_local_inspection(self):
        with patch.object(trailers, "nello_configured", return_value=True), patch.object(trailers, "command", AsyncMock(return_value=b'{"entries": []}')):
            candidates = await trailers.TrailerService().candidates("GigaBash", {"official_match": True, "owners": ["Passion Republic Games"]})
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["video_id"], "kJUeC8NqQqo")
        self.assertTrue(candidates[0]["metadata_pending"])

    async def test_blocked_local_metadata_routes_verified_search_to_nello(self):
        import json
        entry = {"id": "abcdefghijk", "title": "Example Official Trailer", "channel": "Publisher", "channel_is_verified": True}
        search = json.dumps({"entries": [entry]}).encode()
        with patch.object(trailers, "nello_configured", return_value=True), patch.object(trailers, "command", AsyncMock(side_effect=[search, RuntimeError("Sign in to confirm you're not a bot"), search])):
            candidates = await trailers.TrailerService().candidates("Example", {"official_match": True, "owners": ["Publisher"]})
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["metadata_pending"])
        self.assertEqual(candidates[0]["video_id"], entry["id"])

    async def test_nello_pending_metadata_rejects_wrong_video_identity(self):
        candidate = {"kind": "youtube", "url": "https://youtu.be/abcdefghijk", "metadata_pending": True, "video_id": "abcdefghijk", "game_title": "Example", "owners": ["Publisher"]}
        with tempfile.TemporaryDirectory() as folder, patch.object(trailers, "nello_configured", return_value=True), patch.object(trailers, "download_youtube", AsyncMock(return_value={"id": "wrong-video", "title": "Example Official Trailer", "uploader": "Publisher", "duration": 120, "source_info": {"language": "en"}})), patch.object(trailers, "probe", AsyncMock()) as probe:
            result = await trailers.TrailerService().prepare(candidate, None, folder, 0)
        self.assertIsNone(result)
        probe.assert_not_awaited()

    async def test_uploaded_video_replaces_existing_text_card(self):
        import json
        service = trailers.TrailerService()
        session = UploadSession()
        async def prepare(candidate, session, directory, index):
            path = Path(directory) / "en.mp4"
            path.write_bytes(b"video")
            return {"path": path, "language": "en", "duration": 120}
        with patch.object(service, "candidates", AsyncMock(return_value=[{"language": "en"}])), \
             patch.object(service, "prepare", prepare):
            self.assertTrue(await service.send(1, "Example", "Caption", {}, session, "api", AsyncMock(), message_id=37))
        self.assertEqual(session.urls, ["api/editMessageMedia"])
        self.assertEqual(session.uploads[0]["message_id"], "37")
        media = json.loads(session.uploads[0]["media"])
        self.assertEqual(media["media"], "attach://video")
        self.assertEqual(media["caption"], "Caption")

    async def test_cached_video_replaces_existing_text_card(self):
        service = trailers.TrailerService()
        service.cache["Example"] = {"file_id": "cached-video", "expires": time.monotonic() + 100}
        api = AsyncMock(return_value={"ok": True})
        self.assertTrue(await service.send(1, "Example", "Caption", {}, UploadSession(), "api", api, message_id=37))
        self.assertEqual(api.await_args.args[0], "editMessageMedia")
        self.assertEqual(api.await_args.kwargs["message_id"], 37)
        self.assertEqual(api.await_args.kwargs["media"]["media"], "cached-video")

    async def test_truncated_stream_is_rejected_before_conversion(self):
        service = trailers.TrailerService()
        with tempfile.TemporaryDirectory() as directory:
            async def download(*args, **kwargs):
                Path(directory, "source-0.mp4").write_bytes(b"truncated")
            with patch.object(trailers, "command", AsyncMock(side_effect=download)) as command, \
                 patch.object(trailers, "probe", AsyncMock(side_effect=[
                     {"format": {"duration": "180"}},
                     {"format": {"duration": "120"}, "streams": [{"codec_type": "video"}]}])):
                prepared = await service.prepare({"kind": "steam_stream", "language": "en",
                    "url": "https://example.com/trailer.m3u8"}, None, directory, 0)
            self.assertIsNone(prepared)
            self.assertEqual(command.await_count, 1)

    async def test_youtube_can_merge_video_and_audio_without_selecting_foreign_audio(self):
        import yt_dlp
        service = trailers.TrailerService()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(trailers, "command", AsyncMock()) as command:
                await service.prepare({"kind": "youtube", "language": "en", "url": "https://youtube.com/watch?v=abcdefghijk"},
                                      None, directory, 0)
        args = command.await_args.args
        specification = args[args.index("-f") + 1]
        with yt_dlp.YoutubeDL({"quiet": True}) as downloader:
            select = downloader.build_format_selector(specification)
            chosen = list(select({"formats": [
                {"format_id": "video", "ext": "mp4", "height": 480, "vcodec": "avc1", "acodec": "none", "url": "https://example.com/video"},
                {"format_id": "english", "ext": "m4a", "language": "en", "vcodec": "none", "acodec": "mp4a", "url": "https://example.com/en"},
                {"format_id": "french", "ext": "m4a", "language": "fr", "vcodec": "none", "acodec": "mp4a", "url": "https://example.com/fr"},
            ], "has_merged_format": False, "incomplete_formats": False}))
        self.assertEqual(chosen[0]["format_id"], "video+english")

    async def test_blocked_chat_does_not_invalidate_shared_file_id(self):
        service = trailers.TrailerService()
        service.cache["Example"] = {"file_id": "cached-video", "expires": time.monotonic() + 100}
        api = AsyncMock(return_value={"ok": False, "error_code": 403, "description": "Forbidden"})
        with self.assertRaises(RuntimeError):
            await service.send(1, "Example", "Caption", {}, UploadSession(), "api", api)
        self.assertEqual(service.cache["Example"]["file_id"], "cached-video")

    async def test_rate_limit_does_not_cache_missing_trailer(self):
        service = trailers.TrailerService()
        session = UploadSession([{"ok": False, "error_code": 429, "description": "Too Many Requests"}])
        async def prepare(candidate, session, directory, index):
            path = Path(directory) / "en.mp4"
            path.write_bytes(b"video")
            return {"path": path, "language": "en", "duration": 120}
        with patch.object(service, "candidates", AsyncMock(return_value=[{"language": "en"}])), \
             patch.object(service, "prepare", prepare):
            with self.assertRaises(RuntimeError):
                await service.send(1, "Example", "Caption", {}, session, "api", AsyncMock())
        self.assertNotIn("Example", service.cache)

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

    async def test_partial_delivery_retries_only_failed_chat_after_restart(self):
        bot = test_start.bot
        bot.state.chats = {101, 202}
        bot.state._save_chats()
        game = dict(self.games[0], id="pending")
        async def first_attempt(chat_id, game):
            if chat_id == 202:
                raise RuntimeError("Temporary Telegram failure")
            return True
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=[game])), \
             patch.object(bot, "send_game", AsyncMock(side_effect=first_attempt)):
            await bot.broadcast_new_games()
        self.assertNotIn("pending", bot.state.sent)
        bot.state = bot.State()
        with patch.object(bot, "fetch_all_games", AsyncMock(return_value=[game])), \
             patch.object(bot, "send_game", AsyncMock(return_value=True)) as send:
            await bot.broadcast_new_games()
        send.assert_awaited_once_with(202, game)
        self.assertIn("pending", bot.state.sent)

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
