import itertools
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.server import (
    _build_relink_plan,
    _compare_timeline_snapshots,
    _conform_capabilities,
    _detect_gaps_overlaps_from_snapshot,
    _detect_missing_media_from_snapshot,
    _export_timeline_checked,
    _media_path_basename,
    _media_path_is_foreign,
    _media_path_volume_root,
    _missing_media_diagnosis,
    _sanitize_media_path,
    _source_ranges_from_snapshot,
    _timeline_conform_snapshot,
    _timeline_export_spec,
)


class MediaPoolItemStub:
    def __init__(self, name, item_id, file_path):
        self.name = name
        self.item_id = item_id
        self.file_path = file_path

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.item_id

    def GetClipProperty(self, key=""):
        return {
            "File Path": self.file_path,
            "Type": "Video + Audio",
            "Duration": "00:00:02:00",
            "Status": "Offline" if self.file_path and not Path(self.file_path).exists() else "Online",
        }


class TimelineItemStub:
    def __init__(self, name, item_id, start, end, source_start, track_type, track_index, media_pool_item):
        self.name = name
        self.item_id = item_id
        self.start = start
        self.end = end
        self.source_start = source_start
        self.track_type = track_type
        self.track_index = track_index
        self.media_pool_item = media_pool_item

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.item_id

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end

    def GetDuration(self):
        return self.end - self.start

    def GetSourceStartFrame(self):
        return self.source_start

    def GetLeftOffset(self):
        return self.source_start

    def GetTrackTypeAndIndex(self):
        return [self.track_type, self.track_index]

    def GetMediaPoolItem(self):
        return self.media_pool_item


class TimelineStub:
    def __init__(self, tracks):
        self.tracks = tracks

    def GetName(self):
        return "Conform Stub"

    def GetUniqueId(self):
        return "timeline-1"

    def GetStartFrame(self):
        return 86400

    def GetEndFrame(self):
        return 86500

    def GetStartTimecode(self):
        return "01:00:00:00"

    def GetTrackCount(self, track_type):
        return len(self.tracks.get(track_type, {}))

    def GetItemListInTrack(self, track_type, track_index):
        return self.tracks.get(track_type, {}).get(track_index, [])

    def GetMarkers(self):
        return {}

    def Export(self, path, export_type, export_subtype):
        export_path = Path(path)
        if str(path).endswith(".fcpxml"):
            export_path.mkdir(parents=True, exist_ok=True)
            (export_path / "Info.fcpxml").write_text("<fcpxml />", encoding="utf-8")
            return True
        export_path.write_text("export", encoding="utf-8")
        return True


class ResolveConstStub:
    EXPORT_FCPXML_1_10 = 1010
    EXPORT_NONE = 0


class TimelineConformProbeTest(unittest.TestCase):
    def _timeline(self, missing_path="/tmp/missing_source.mov"):
        online = MediaPoolItemStub("A.mov", "mpi-a", __file__)
        missing = MediaPoolItemStub("B.mov", "mpi-b", missing_path)
        return TimelineStub(
            {
                "video": {
                    1: [
                        TimelineItemStub("A", "a1", 0, 10, 100, "video", 1, online),
                        TimelineItemStub("B", "b1", 15, 25, 200, "video", 1, missing),
                    ],
                    2: [
                        TimelineItemStub("C", "c1", 5, 18, 300, "video", 2, online),
                    ],
                },
                "audio": {
                    1: [
                        TimelineItemStub("A.wav", "a2", 0, 10, 100, "audio", 1, online),
                    ]
                },
            }
        )

    def test_capabilities_include_interchange_aliases(self):
        caps = _conform_capabilities()

        self.assertIn("fcpxml", caps["export_aliases"])
        self.assertIn("interchange", caps["supported"])

    def test_snapshot_and_gap_detection(self):
        snapshot = _timeline_conform_snapshot(self._timeline())
        gaps = _detect_gaps_overlaps_from_snapshot(snapshot)

        self.assertEqual(snapshot["item_count"], 4)
        self.assertEqual(gaps["gap_count"], 1)
        self.assertEqual(gaps["gaps"][0]["duration"], 5)

    def test_source_ranges_merge_by_source(self):
        snapshot = _timeline_conform_snapshot(self._timeline())
        report = _source_ranges_from_snapshot(snapshot, {"handles": 2})

        self.assertTrue(report["ranges"])
        first_key = next(iter(report["ranges"]))
        self.assertLessEqual(report["ranges"][first_key][0][0], 100)

    def test_compare_snapshots_reports_item_mismatch(self):
        left = _timeline_conform_snapshot(self._timeline())
        right = _timeline_conform_snapshot(
            TimelineStub({"video": {1: [TimelineItemStub("Changed", "x", 0, 9, 100, "video", 1, None)]}})
        )
        diff = _compare_timeline_snapshots(left, right)

        self.assertFalse(diff["match"])
        self.assertGreater(diff["difference_count"], 0)

    def test_export_spec_resolves_alias_constants(self):
        spec = _timeline_export_spec({"format": "fcpxml"}, ResolveConstStub())

        self.assertEqual(spec["export_type"], ResolveConstStub.EXPORT_FCPXML_1_10)
        self.assertEqual(spec["export_subtype"], ResolveConstStub.EXPORT_NONE)

    def test_export_checked_reports_directory_primary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "timeline.fcpxml")
            result = _export_timeline_checked(
                self._timeline(),
                {"format": "fcpxml", "path": path, "require_temp_path": False},
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["is_directory"])
        self.assertTrue(result["primary_file"].endswith("Info.fcpxml"))

    def test_missing_media_and_relink_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = str(Path(tmp) / "offline" / "lost.mov")
            replacement = Path(tmp) / "lost.mov"
            replacement.write_text("replacement", encoding="utf-8")
            timeline = self._timeline(missing_path=missing_path)
            snapshot = _timeline_conform_snapshot(timeline)
            missing = _detect_missing_media_from_snapshot(snapshot)
            plan = _build_relink_plan(timeline, {"search_roots": [tmp]})

        self.assertEqual(missing["missing_count"], 1)
        self.assertEqual(missing["diagnosis"]["unique_media_pool_item_count"], 1)
        self.assertEqual(missing["diagnosis"]["primary_cause"], "folder_not_found")
        self.assertTrue(plan["success"])
        self.assertEqual(plan["candidate_count"], 1)
        self.assertEqual(plan["unique_missing_basename_count"], 1)

    def test_items_with_no_media_pool_item_are_not_counted_as_present(self):
        """A timeline item with neither a path nor a pool item knows nothing, and
        `present` is a claim. Counting those as present made an entirely offline
        timeline report full coverage: an AAF imported with importSourceClips=false
        yields items like these, and the probe answered present_count 882 /
        missing_count 0 while every one returned None from GetMediaPoolItem()."""
        snapshot = {
            "tracks": {
                "video": {
                    "tracks": [
                        {
                            "track_index": 1,
                            "items": [
                                # no file_path, no media_pool_item_id -> unlinked
                                {"timeline_item_id": "a", "name": "A001C001.new.01"},
                                {"timeline_item_id": "b", "name": "A001C002.new.01"},
                                # genuinely linked, file exists
                                {
                                    "timeline_item_id": "c",
                                    "name": "real",
                                    "media_pool_item_id": "mp1",
                                    "file_path": __file__,
                                },
                            ],
                        }
                    ]
                }
            }
        }
        result = _detect_missing_media_from_snapshot(snapshot)

        self.assertEqual(result["unlinked_count"], 2)
        self.assertEqual(result["present_count"], 1, "unlinked items must not inflate present_count")
        self.assertEqual(result["missing_count"], 0, "no pool item means nothing to relink")
        self.assertEqual(result["diagnosis"]["primary_cause"], "no_media_pool_items")
        self.assertIn("media pool", result["diagnosis"]["recommended_next_step"])

    def test_relink_plan_skips_search_when_source_volume_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            replacement = Path(tmp) / "P1047043.MOV"
            replacement.write_text("replacement", encoding="utf-8")
            missing_path = "/Volumes/EOS_DIGITAL/DCIM/104_PANA/P1047043.MOV"
            timeline = self._timeline(missing_path=missing_path)
            # A macOS card on macOS: pinned so Windows CI sees the same-OS case
            # rather than a path another OS wrote.
            with mock.patch("src.server._host_media_path_form", return_value="posix"):
                plan = _build_relink_plan(timeline, {"search_roots": [tmp], "sanitized": True})

        self.assertTrue(plan["success"])
        self.assertTrue(plan["search_skipped"])
        self.assertEqual(plan["skip_reason"], "missing_source_volume_not_mounted")
        self.assertEqual(plan["candidate_count"], 0)
        self.assertEqual(plan["diagnosis"]["primary_cause"], "volume_not_mounted")
        self.assertEqual(plan["diagnosis"]["missing_volumes"][0]["volume_root_sanitized"], "/Volumes/EOS_DIGITAL/...")
        self.assertNotIn(tmp, str(plan))


class MediaPathFormTests(unittest.TestCase):
    """Resolve stores a path the way the OS that wrote it gave it, and a project
    moves between machines, so the path's own form decides, not `os.sep`.
    Pure string handling: these run on every platform (#278)."""

    def test_volume_root_follows_the_paths_own_form(self):
        cases = {
            "/Volumes/EOS_DIGITAL/DCIM/104_PANA/P1047043.MOV": "/Volumes/EOS_DIGITAL",
            "/Users/editor/clip.mov": "/Users",
            "E:\\DCIM\\104_PANA\\P1047043.MOV": "E:\\",
            "e:/DCIM/P1047043.MOV": "e:\\",
            "\\\\nas\\share\\cards\\P1047043.MOV": "\\\\nas\\share",
            "clip.mov": None,
        }
        for path, root in cases.items():
            with self.subTest(path=path):
                self.assertEqual(_media_path_volume_root(path), root)

    def test_sanitizing_keeps_the_volume_and_hides_the_rest(self):
        cases = {
            "/Volumes/EOS_DIGITAL/DCIM/P1047043.MOV": "/Volumes/EOS_DIGITAL/.../P1047043.MOV",
            "/Users/editor/clip.mov": "/.../clip.mov",
            "E:\\DCIM\\104_PANA\\P1047043.MOV": "E:\\...\\P1047043.MOV",
            "\\\\nas\\share\\cards\\P1047043.MOV": "\\\\nas\\share\\...\\P1047043.MOV",
            "media\\clip.mov": ".../clip.mov",
        }
        for path, sanitized in cases.items():
            with self.subTest(path=path):
                self.assertEqual(_sanitize_media_path(path), sanitized)
        self.assertEqual(_sanitize_media_path("E:\\DCIM\\104_PANA", keep_filename=False), "E:\\...")
        self.assertEqual(_sanitize_media_path("\\\\nas\\share", keep_filename=False), "\\\\nas\\share\\...")

    def test_an_unplugged_windows_card_is_a_missing_volume(self):
        rows = [{"file_path": "E:\\DCIM\\104_PANA\\P1047043.MOV", "media_pool_item_id": "mp1", "name": "P1047043.MOV"}]
        with mock.patch("src.server._host_media_path_form", return_value="windows"), mock.patch(
            "os.path.isdir", return_value=False
        ):
            diagnosis = _missing_media_diagnosis(rows)
        self.assertEqual(diagnosis["primary_cause"], "volume_not_mounted")
        self.assertEqual(diagnosis["missing_volumes"][0]["volume_root_sanitized"], "E:\\...")
        self.assertFalse(diagnosis["missing_volumes"][0]["foreign_os_path"])
        self.assertIn("E:\\...", diagnosis["recommended_next_step"])

    def test_basename_follows_the_paths_own_form(self):
        cases = {
            "/Volumes/EOS_DIGITAL/DCIM/104_PANA/P1047043.MOV": "P1047043.MOV",
            "E:\\DCIM\\104_PANA\\P1047043.MOV": "P1047043.MOV",
            "e:/DCIM/P1047043.MOV": "P1047043.MOV",
            "\\\\nas\\share\\cards\\P1047043.MOV": "P1047043.MOV",
            "P1047043.MOV": "P1047043.MOV",
            "": "",
            None: "",
        }
        for path, basename in cases.items():
            with self.subTest(path=path):
                self.assertEqual(_media_path_basename(path), basename)

    def test_foreign_means_a_rooted_path_in_the_other_oss_form(self):
        cases = [
            ("posix", "E:\\DCIM\\P1047043.MOV", True),
            ("posix", "\\\\nas\\share\\P1047043.MOV", True),
            ("posix", "/Volumes/EOS_DIGITAL/P1047043.MOV", False),
            ("windows", "/Volumes/EOS_DIGITAL/P1047043.MOV", True),
            ("windows", "E:\\DCIM\\P1047043.MOV", False),
            ("windows", "\\\\nas\\share\\P1047043.MOV", False),
            ("posix", "media/P1047043.MOV", False),
            ("windows", "media\\P1047043.MOV", False),
        ]
        for host, path, foreign in cases:
            with self.subTest(host=host, path=path):
                with mock.patch("src.server._host_media_path_form", return_value=host):
                    self.assertEqual(_media_path_is_foreign(path), foreign)


class ForeignOsMediaPathTests(unittest.TestCase):
    """A project moved between macOS and Windows keeps the paths the other OS
    wrote. Their volumes can never be mounted here, so the diagnosis says so
    and the relink plan searches approved roots instead of waiting for a mount.
    The host OS is pinned, so every platform runs both directions (#278)."""

    CASES = [
        # (running OS, path the other OS wrote, its sanitized volume root)
        ("posix", "E:\\DCIM\\104_PANA\\P1047043.MOV", "E:\\..."),
        ("posix", "\\\\nas\\share\\cards\\P1047043.MOV", "\\\\nas\\share\\..."),
        ("windows", "/Volumes/EOS_DIGITAL/DCIM/104_PANA/P1047043.MOV", "/Volumes/EOS_DIGITAL/..."),
    ]

    @staticmethod
    def _timeline(*missing_paths):
        items = [
            TimelineItemStub(
                f"clip{index}",
                f"t{index}",
                index * 10,
                index * 10 + 5,
                0,
                "video",
                1,
                MediaPoolItemStub(f"clip{index}", f"mpi-{index}", path),
            )
            for index, path in enumerate(missing_paths)
        ]
        return TimelineStub({"video": {1: items}})

    def test_diagnosis_reports_another_oss_volume_instead_of_asking_to_mount_it(self):
        for host, path, root in self.CASES:
            with self.subTest(host=host, path=path):
                rows = [{"file_path": path, "media_pool_item_id": "mp1", "name": "P1047043.MOV"}]
                with mock.patch("src.server._host_media_path_form", return_value=host), mock.patch(
                    "os.path.isdir", return_value=False
                ) as isdir:
                    diagnosis = _missing_media_diagnosis(rows)

                self.assertEqual(diagnosis["primary_cause"], "foreign_os_path")
                volume = diagnosis["missing_volumes"][0]
                self.assertEqual(volume["volume_root_sanitized"], root)
                self.assertTrue(volume["foreign_os_path"])
                self.assertFalse(volume["mounted"])
                self.assertEqual(volume["sample_basenames"], ["P1047043.MOV"])
                item = diagnosis["unique_media_pool_items"][0]
                self.assertEqual(item["wanted_basename"], "P1047043.MOV")
                self.assertTrue(item["foreign_os_path"])
                self.assertTrue(all(folder["foreign_os_path"] for folder in diagnosis["missing_folders"]))
                self.assertIn(root, diagnosis["recommended_next_step"])
                self.assertIn("build_relink_plan", diagnosis["recommended_next_step"])
                self.assertNotIn("Mount", diagnosis["recommended_next_step"])
                # The other OS's names mean something else here; never probe them.
                isdir.assert_not_called()

    def test_relink_plan_searches_approved_roots_for_another_oss_paths(self):
        # Clip names differ from the filename, so a match proves the path's own
        # basename was searched for, sanitized report or not.
        for (host, path, _root), sanitized in itertools.product(self.CASES, (True, False)):
            with self.subTest(host=host, path=path, sanitized=sanitized):
                with tempfile.TemporaryDirectory() as tmp:
                    (Path(tmp) / "P1047043.MOV").write_text("replacement", encoding="utf-8")
                    with mock.patch("src.server._host_media_path_form", return_value=host):
                        plan = _build_relink_plan(
                            self._timeline(path), {"search_roots": [tmp], "sanitized": sanitized}
                        )

                self.assertTrue(plan["success"])
                self.assertNotIn("search_skipped", plan)
                self.assertEqual(plan["diagnosis"]["primary_cause"], "foreign_os_path")
                self.assertEqual(plan["candidate_count"], 1)
                candidate = plan["candidates"][0]
                self.assertEqual(candidate["wanted_basename"], "P1047043.MOV")
                self.assertTrue(candidate["foreign_os_path"])
                self.assertEqual(candidate["candidate_count"], 1)
                if sanitized:
                    self.assertNotIn(tmp, str(plan))

    def test_a_same_os_unmounted_volume_still_holds_the_search(self):
        same_os = "/Volumes/EOS_DIGITAL/DCIM/104_PANA/P1047043.MOV"
        foreign = "E:\\DCIM\\104_PANA\\P1047044.MOV"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "P1047044.MOV").write_text("replacement", encoding="utf-8")
            with mock.patch("src.server._host_media_path_form", return_value="posix"):
                plan = _build_relink_plan(self._timeline(same_os, foreign), {"search_roots": [tmp], "sanitized": True})

        self.assertTrue(plan["search_skipped"])
        self.assertEqual(plan["skip_reason"], "missing_source_volume_not_mounted")
        diagnosis = plan["diagnosis"]
        self.assertEqual(diagnosis["primary_cause"], "volume_not_mounted")
        self.assertIn("Mount the missing volume(s): /Volumes/EOS_DIGITAL/...", diagnosis["recommended_next_step"])
        self.assertIn("another operating system (E:\\...)", diagnosis["recommended_next_step"])
        flags = {row["volume_root_sanitized"]: row["foreign_os_path"] for row in diagnosis["missing_volumes"]}
        self.assertEqual(flags, {"/Volumes/EOS_DIGITAL/...": False, "E:\\...": True})


if __name__ == "__main__":
    unittest.main()
