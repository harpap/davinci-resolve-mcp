"""Insta360 extraction contract, exercised only with synthetic media in temp dirs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("extract_insta360_audio", REPO / "scripts/extract_insta360_audio.py")
extractor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extractor)


class DiscoveryTests(unittest.TestCase):
    def test_originals_win_over_exports_and_lrv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "take.INSV"
            for path in (original, root / "export.mp4", root / "proxy.lrv"):
                path.touch()
            self.assertEqual(extractor.discover(root), [original])

    def test_one_stream_is_not_misread_as_two_microphones(self):
        with self.assertRaisesRegex(ValueError, "found 1"):
            extractor.audio_streams({"streams": [{"codec_type": "audio", "channels": 2}]})


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe required")
class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "originals"
        self.source.mkdir()
        self.recording = self.source / "take & $source.insv"
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")
        # Video makes the audio's absolute stream indices differ from ordinals.
        self.command([
            self.ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=size=16x16:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
            "-f", "lavfi", "-i", "sine=frequency=2000:sample_rate=44100",
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0", "-t", "0.3",
            "-c:v", "mpeg4", "-c:a", "aac", "-ac:a:0", "2", "-ac:a:1", "1",
            "-f", "mp4", str(self.recording),
        ])

    def command(self, args):
        return subprocess.run(args, capture_output=True, text=True, check=True).stdout

    def test_verified_pairs_preserve_mapping_format_source_and_nested_names(self):
        nested = self.source / "second camera"
        nested.mkdir()
        shutil.copyfile(self.recording, nested / self.recording.name)
        before = hashlib.sha256(self.recording.read_bytes()).hexdigest()
        output = self.root / "audio"
        result = extractor.extract(self.source, output)
        self.assertEqual(result["summary"], {"recordings": 2, "verified_pairs": 2,
                                            "failed_pairs": 0, "pending_pairs": 0})
        self.assertEqual(len(list(output.rglob("*.wav"))), 4)
        self.assertEqual(len(list(output.rglob("*.partial.wav"))), 0)
        self.assertEqual(before, hashlib.sha256(self.recording.read_bytes()).hexdigest())
        for clip in result["clips"]:
            self.assertTrue(clip["source_stat_unchanged"])
            self.assertTrue(clip["decoded_streams_differ"])
            for i, audio in enumerate(clip["audio"]):
                expected = self.command([
                    self.ffmpeg, "-v", "error", "-i", str(self.recording),
                    "-map", f"0:a:{i}", "-c:a", "pcm_s24le", "-f", "hash", "-hash", "sha256", "-",
                ]).strip().split("=", 1)[1]
                self.assertEqual(audio["pcm_sha256"], expected)
                self.assertEqual(audio["sample_rate"], (48000, 44100)[i])
                self.assertEqual(audio["channels"], (2, 1)[i])
                self.assertEqual(audio["source_stream_index"], i + 1)
        saved = json.loads((output / "extraction_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["summary"], result["summary"])

    def test_existing_destination_and_source_tree_are_refused(self):
        with self.assertRaisesRegex(ValueError, "separate"):
            extractor.extract(self.source, self.source / "audio")
        output = self.root / "existing"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            extractor.extract(self.source, output)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_missing_track_is_reported_without_partial_success_claim(self):
        single = self.source / "single.mp4"
        self.command([self.ffmpeg, "-v", "error", "-i", str(self.recording),
                      "-map", "0:a:0", "-c", "copy", str(single)])
        result = extractor.extract(single, self.root / "single output")
        self.assertEqual(result["summary"]["failed_pairs"], 1)
        self.assertEqual(result["summary"]["verified_pairs"], 0)
        self.assertIn("found 1", result["clips"][0]["error"])
        self.assertEqual(list(Path(result["output_directory"]).rglob("*.wav")), [])

    def test_failed_validation_keeps_only_partial_outputs(self):
        output = self.root / "failed output"
        with patch.object(extractor, "validate_wav", side_effect=ValueError("bad WAV")):
            result = extractor.extract(self.recording, output)
        self.assertEqual(result["summary"]["failed_pairs"], 1)
        self.assertEqual(len(list(output.rglob("*.partial.wav"))), 2)
        self.assertTrue(all(p.name.endswith(".partial.wav") for p in output.rglob("*.wav")))

    def test_stream_offsets_are_recorded_without_silently_shifting_audio(self):
        offset = self.source / "offset.mp4"
        self.command([
            self.ffmpeg, "-v", "error", "-i", str(self.recording), "-itsoffset", "0.2",
            "-i", str(self.recording), "-map", "0:a:0", "-map", "1:a:1", "-c", "copy", str(offset),
        ])
        result = extractor.extract(offset, self.root / "offset output")
        self.assertEqual(result["summary"]["verified_pairs"], 1)
        starts = [float(a["source_start_seconds"]) for a in result["clips"][0]["audio"]]
        self.assertGreater(starts[1] - starts[0], 0.1)


if __name__ == "__main__":
    unittest.main()
