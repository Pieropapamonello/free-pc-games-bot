import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import trailers
import test_start


class MetadataTests(unittest.TestCase):
    def test_selects_480p_stream_instead_of_first_1080p_stream(self):
        self.assertEqual(trailers.stream_video_map({"streams": [
            {"index": 0, "codec_type": "audio"},
            {"index": 1, "codec_type": "video", "height": 1080},
            {"index": 2, "codec_type": "video", "height": 720},
            {"index": 3, "codec_type": "video", "height": 480},
            {"index": 4, "codec_type": "video", "height": 360}]}), "0:3")

    def test_stream_copy_requires_compatible_codecs_and_bounded_size(self):
        info = {"streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}, {"codec_type": "audio", "codec_name": "aac"}]}
        self.assertTrue(trailers.telegram_copy_compatible(info, 12_000_000))
        self.assertFalse(trailers.telegram_copy_compatible(info, trailers.MAX_UPLOAD + 1))
        info["streams"][0]["codec_name"] = "av1"
        self.assertFalse(trailers.telegram_copy_compatible(info, 12_000_000))
    def test_reviewed_language_is_bound_to_exact_asset_and_duration(self):
        path = next(iter(trailers.REVIEWED_STEAM_ASSETS))
        url = "https://video.akamai.steamstatic.com" + path
        self.assertEqual(trailers.reviewed_steam_language(url + "?t=123", 79), "en")
        self.assertIsNone(trailers.reviewed_steam_language(url.replace("1750699784", "9999999999"), 79))
        self.assertIsNone(trailers.reviewed_steam_language(url.replace("video.akamai.steamstatic.com", "other.example"), 79))
        self.assertIsNone(trailers.reviewed_steam_language(url, 120))
    def test_visible_text_language_requires_substantial_language_evidence(self):
        self.assertEqual(trailers.text_language("Explore the city and discover a world of monsters. Play with your friends and fight to save the world."), "en")
        self.assertEqual(trailers.text_language("Esplora la città e scopri un mondo pieno di mostri. Gioca con i tuoi amici e combatti per salvare il mondo."), "it")
        self.assertIsNone(trailers.text_language("GigaBash PC Steam 2026"))
        self.assertIsNone(trailers.text_language("Explorez la ville et découvrez un monde rempli de monstres. Jouez avec vos amis pour sauver le monde."))

    def test_publisher_page_finds_direct_trailer_but_not_embedded_youtube(self):
        html = '<a href="/media/game-trailer-English.mp4">Official trailer</a><video><source src="/media/game-trailer-English.mp4"></video><a href="https://youtube.com/watch?v=abcdefghijk">Trailer</a><a href="/game-download.mp4">Download</a>'
        candidates = trailers.page_trailers(html, "https://publisher.example/game")
        self.assertEqual(candidates, [{"url": "https://publisher.example/media/game-trailer-English.mp4", "kind": "publisher", "language": "en"}])
    def test_game_trailer_accepts_platform_suffix_but_excludes_dlc(self):
        self.assertTrue(trailers.title_matches("GigaBash Official Launch Trailer | Nintendo Switch", "GigaBash"))
        self.assertTrue(trailers.title_matches("GigaBash - Official launch trailer (PC + PlayStation)", "GigaBash"))
        self.assertTrue(trailers.title_matches("GigaBash - Official Trailer | gamescom 2021", "GigaBash"))
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
    async def test_compatible_video_is_remuxed_without_cpu_encoding(self):
        details = {"format": {"duration": "79"}, "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}, {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}}]}
        calls = []
        async def remux(*args, **kwargs):
            calls.append(args)
            Path(args[-1]).write_bytes(b"video")
            return b""
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "source-0.mp4").write_bytes(b"video")
            with patch.object(trailers, "probe", AsyncMock(return_value=details)), patch.object(trailers, "command", remux):
                result = await trailers.TrailerService().prepare({"kind": "nello", "language": "en"}, None, folder, 0)
        self.assertEqual(result["duration"], 79)
        self.assertIn("copy", calls[0])
        self.assertNotIn("libx264", calls[0])
    async def test_reviewed_steam_asset_is_prepared_without_ocr(self):
        details = {"format": {"duration": "79"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio", "tags": {"language": "und"}}]}
        async def download_or_convert(*args, **kwargs):
            Path(args[-1]).write_bytes(b"video")
            return b""
        candidate = {"kind": "steam_stream", "language": None, "url": "https://video.akamai.steamstatic.com" + next(iter(trailers.REVIEWED_STEAM_ASSETS))}
        with tempfile.TemporaryDirectory() as folder, patch.object(trailers, "probe", AsyncMock(return_value=details)), patch.object(trailers, "command", download_or_convert), patch.object(trailers, "visual_language", AsyncMock()) as visual:
            result = await trailers.TrailerService().prepare(candidate, None, folder, 0)
        self.assertEqual(result["language"], "en")
        visual.assert_not_awaited()
    async def test_visual_language_samples_final_card_with_readable_small_text(self):
        calls = []
        async def recognize(*args, **kwargs):
            calls.append(args)
            if args[0] == "tesseract" and "language-0-4.png" in str(args[1]):
                return b"AVAILABLE NOW. All Rights Reserved. This game is a registered trademark of the publisher in Malaysia and other countries."
            return b""
        with tempfile.TemporaryDirectory() as folder, patch.object(trailers, "command", recognize):
            language = await trailers.visual_language(Path("source.mp4"), 79, folder, 0)
        self.assertEqual(language, "en")
        last_frame = [args for args in calls if args[0] == "ffmpeg"][0]
        self.assertEqual(last_frame[last_frame.index("-ss") + 1], "77")
        self.assertEqual(last_frame[last_frame.index("-vf") + 1], "scale=-2:1080")
        self.assertEqual(len([args for args in calls if args[0] == "tesseract"]), 1)
    async def test_publisher_video_urls_reject_private_addresses(self):
        with patch.object(trailers.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            self.assertFalse(await trailers.public_https("https://publisher.example/trailer.mp4"))
        self.assertFalse(await trailers.public_https("http://publisher.example/trailer.mp4"))

    async def test_usable_store_video_does_not_search_youtube(self):
        service = trailers.TrailerService()
        async def prepare(candidate, session, directory, index):
            path = Path(directory) / "store.mp4"
            path.write_bytes(b"video")
            return {"path": path, "language": "en", "duration": 90}
        with patch.object(service, "candidates", AsyncMock(return_value=[{"kind": "steam", "language": "en"}])) as candidates, patch.object(service, "prepare", prepare):
            self.assertTrue(await service.send(1, "Example", "Caption", {}, UploadSession(), "api", AsyncMock()))
        candidates.assert_awaited_once_with("Example", {}, youtube=False)

    async def test_youtube_searched_only_after_store_candidates_fail(self):
        service = trailers.TrailerService()
        async def prepare(candidate, session, directory, index):
            if candidate["kind"] == "steam":
                return None
            path = Path(directory) / "fallback.mp4"
            path.write_bytes(b"video")
            return {"path": path, "language": "en", "duration": 90}
        local = {"kind": "steam", "language": None}
        youtube = {"kind": "youtube", "language": "en"}
        with patch.object(service, "candidates", AsyncMock(side_effect=[[local], [local, youtube]])) as candidates, patch.object(service, "prepare", prepare):
            self.assertTrue(await service.send(1, "Example", "Caption", {}, UploadSession(), "api", AsyncMock()))
        self.assertEqual(candidates.await_count, 2)

    async def test_unknown_metadata_can_use_language_of_visible_video_text(self):
        service = trailers.TrailerService()
        details = {"format": {"duration": "90"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio", "tags": {"language": "und"}}]}
        async def convert(*args, **kwargs):
            Path(args[-1]).write_bytes(b"converted")
            return b""
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "source-0.mp4").write_bytes(b"downloaded")
            with patch.object(trailers, "probe", AsyncMock(return_value=details)), patch.object(trailers, "visual_language", AsyncMock(return_value="en")) as visual, patch.object(trailers, "command", convert):
                result = await service.prepare({"kind": "nello", "language": None}, None, folder, 0)
            self.assertEqual(result["language"], "en")
            visual.assert_awaited_once()

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
