import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import legacy
from app.services import storage_service


class MemoryUpload:
    def __init__(self, filename, content_type, data):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self._offset = 0

    async def read(self, size=-1):
        if self._offset >= len(self._data):
            return b""
        if size is None or size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


class CanvasMediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="syncanvas-media-"))
        self.originals = {
            "ASSETS_DIR": legacy.ASSETS_DIR,
            "OUTPUT_DIR": legacy.OUTPUT_DIR,
            "OUTPUT_INPUT_DIR": legacy.OUTPUT_INPUT_DIR,
            "OUTPUT_OUTPUT_DIR": legacy.OUTPUT_OUTPUT_DIR,
        }
        legacy.ASSETS_DIR = str(self.temp_root / "assets")
        legacy.OUTPUT_DIR = str(self.temp_root / "output")
        legacy.OUTPUT_INPUT_DIR = str(self.temp_root / "assets" / "input")
        legacy.OUTPUT_OUTPUT_DIR = str(self.temp_root / "assets" / "output")
        os.makedirs(legacy.OUTPUT_INPUT_DIR, exist_ok=True)
        os.makedirs(legacy.OUTPUT_OUTPUT_DIR, exist_ok=True)

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(legacy, name, value)
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_media_extension_detection(self):
        self.assertEqual(("audio", ".wav"), storage_service.canvas_media_kind_extension(MemoryUpload("voice.wav", "application/octet-stream", b"x")))
        self.assertEqual(("video", ".mp4"), storage_service.canvas_media_kind_extension(MemoryUpload("clip.mp4", "application/octet-stream", b"x")))
        self.assertEqual(("video", ".webm"), storage_service.canvas_media_kind_extension(MemoryUpload("clip", "video/webm", b"x")))
        self.assertEqual(("", ""), storage_service.canvas_media_kind_extension(MemoryUpload("notes.txt", "text/plain", b"x")))

    async def test_upload_video_and_audio(self):
        result = await storage_service.upload_canvas_media([
            MemoryUpload("clip.mp4", "video/mp4", b"video-data"),
            MemoryUpload("voice.ogg", "audio/ogg", b"audio-data"),
        ])
        self.assertEqual(["video", "audio"], [item["kind"] for item in result["files"]])
        self.assertTrue(result["files"][0]["url"].startswith("/assets/input/canvas_video_"))
        self.assertTrue(result["files"][1]["url"].startswith("/assets/input/canvas_audio_"))
        for item in result["files"]:
            path = storage_service.output_file_from_url(item["url"])
            self.assertTrue(path and os.path.isfile(path))

    async def test_empty_and_unsupported_uploads(self):
        empty = await storage_service.upload_canvas_media([MemoryUpload("empty.mov", "video/quicktime", b"")])
        self.assertEqual([], empty["files"])
        with self.assertRaises(HTTPException) as raised:
            await storage_service.upload_canvas_media([MemoryUpload("payload.exe", "application/octet-stream", b"bad")])
        self.assertEqual(400, raised.exception.status_code)

    async def test_upload_limit_removes_partial_file(self):
        target = self.temp_root / "limited.bin"
        with self.assertRaises(HTTPException) as raised:
            await storage_service.save_upload_limited(
                MemoryUpload("large.mp4", "video/mp4", b"12345"),
                str(target),
                max_bytes=4,
            )
        self.assertEqual(413, raised.exception.status_code)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
