"""Native ramp geometry, source safety, byte preservation and failure recovery."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from src.utils import speed_ramp as r
from tests.test_native_ripple import clip, sequence


class RampGeometryTests(unittest.TestCase):
    def test_ripple_preserves_selected_source_window_with_frame_rounding(self):
        request = dict(mode="ripple", points=[dict(frame=0,speed=2),dict(frame=671,speed=5),
                       dict(frame=1567,speed=5),dict(frame=2238,speed=2)], durationFrames=2239,
                       sourceStartSec=5019/29.97, sourceSpanSec=2239/29.97,
                       sourceDurationSec=1000, fps=29.97, uniqueId="ripple")
        encoded = r.timemap(request)
        self.assertEqual(encoded["durationFrames"], 547)
        self.assertEqual(encoded["speeds"][0], 2)
        self.assertEqual(encoded["speeds"][-1], 2)
        self.assertEqual(max(encoded["speeds"]), 5)
        self.assertLess(abs(encoded["sourceEndErrorSec"]), 2/29.97)
        r.verify_map(encoded["hex"], encoded, 29.97, 5019/29.97)

    def request(self, **overrides):
        request = dict(points=[dict(frame=0, speed=1), dict(frame=20, speed=3),
                               dict(frame=39, speed=3), dict(frame=59, speed=1)],
                       durationFrames=60, sourceStartSec=1.25,
                       sourceDurationSec=20, fps=29.97, uniqueId="test")
        return dict(request, **overrides)

    def test_native_curve_has_requested_speeds_and_correct_seconds_domain(self):
        request = self.request()
        encoded = r.timemap(request)
        decoded = r.timemap(dict(mode="inspect", hex=encoded["hex"]))
        speeds = encoded["speeds"]
        self.assertEqual([speeds[i] for i in (0, 20, 39, 59)], [1, 3, 3, 1])
        self.assertEqual(speeds[:21], sorted(speeds[:21]))
        self.assertEqual(speeds[39:], sorted(speeds[39:], reverse=True))
        self.assertLess(speeds[1] - 1, speeds[10] - speeds[9])
        self.assertAlmostEqual(decoded["origin"]["sourceSec"], 1.25)
        self.assertAlmostEqual(decoded["keyframes"][-1]["recordSec"], 60 / 29.97)
        self.assertAlmostEqual(encoded["sourceEndSec"], 1.25 + sum(speeds) / 29.97)
        r.verify_map(encoded["hex"], encoded, 29.97, 1.25)

    def test_source_overrun_and_malformed_points_refuse(self):
        with self.assertRaisesRegex(ValueError, "available source"):
            r.timemap(self.request(sourceDurationSec=2))
        for points in ([], [dict(frame=0, speed=1)],
                       [dict(frame=0, speed=True), dict(frame=59, speed=1)],
                       [dict(frame=0, speed=1), dict(frame=59, speed=float("nan"))],
                       [dict(frame=0, speed=1), dict(frame=0, speed=2), dict(frame=59, speed=1)],
                       [dict(frame=1, speed=1), dict(frame=59, speed=1)],
                       [dict(frame=0, speed=1), dict(frame=60, speed=1)],
                       [dict(frame=0, speed=1), dict(frame=59, speed=-1)]):
            with self.subTest(points=points), self.assertRaises(ValueError):
                r.validate_points(points, 60)

    def test_readback_cannot_mistake_constant_speed_for_ramp(self):
        encoded = r.timemap(self.request())
        wrong = r.timemap(self.request(points=[dict(frame=0, speed=1), dict(frame=59, speed=1)]))
        with self.assertRaisesRegex(RuntimeError, "mapping failed"):
            r.verify_map(wrong["hex"], encoded, 29.97, 1.25)

    def test_live_211_compressed_export_preserves_every_output_frame(self):
        fixture = Path(__file__).resolve().parents[1] / "resolve-advanced/vendor/drp-format/__tests__/fixtures/retimed-timemap-smooth-211.hex"
        encoded = r.timemap(self.request(points=[dict(frame=0, speed=1), dict(frame=28, speed=3),
                                                dict(frame=67, speed=3), dict(frame=95, speed=1)],
                                         durationFrames=96, sourceStartSec=1, sourceDurationSec=12, fps=24))
        r.verify_map(fixture.read_text().strip(), encoded, 24, 1)


class NativePatchTests(unittest.TestCase):
    def test_ripple_archive_resizes_group_and_shifts_all_later_tracks(self):
        payload = b'<In>7</In><MediaTimemapBA>abcd</MediaTimemapBA><Opaque>keep</Opaque>'
        audio = b'<AudioTrackVec><Element><Sm2TiTrack><Items>' + clip(100,60,payload,b'Sm2TiAudioClip') + clip(160,20,payload,b'Sm2TiAudioClip') + b'</Items></Sm2TiTrack></Element></AudioTrackVec>'
        data = sequence(clip(100,60,payload)+clip(160,20,payload),audio)
        plan = dict(start=100,duration=60,new_duration=20,shift_frames=-40,ripple=True,
                    members=[dict(track_type=k,track_index=1,start=100,duration=60,new_duration=20) for k in ('video','audio')])
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/'original.drt', Path(directory)/'ripple.drt'
            with zipfile.ZipFile(source,'w') as z:
                z.writestr('project.xml', b'opaque project')
                z.writestr('SeqContainer/main.xml',data)
            r.patch_archive(source,output,plan,['aa','bb'])
            with zipfile.ZipFile(output) as z:
                result=z.read('SeqContainer/main.xml')
                self.assertEqual(z.read('project.xml'),b'opaque project')
            self.assertEqual(result.count(b'<Start>120</Start>'),2)
            self.assertEqual(result.count(b'<Duration>20</Duration>'),4)
            self.assertEqual(result.count(b'<Opaque>keep</Opaque>'),4)
            self.assertEqual(result.count(b'<MediaTimemapBA>abcd</MediaTimemapBA>'),2)

    def test_audio_target_is_patched_without_changing_picture_or_link_payloads(self):
        payload = b'<In>31</In><MediaTimemapBA>abcd</MediaTimemapBA><LinkedItemSync>opaque</LinkedItemSync>'
        video = clip(100, 60, payload)
        audio = (b'<AudioTrackVec><Element><Sm2TiTrack><Items>' +
                 clip(100, 60, payload, b'Sm2TiAudioClip') + b'</Items></Sm2TiTrack></Element></AudioTrackVec>')
        data = sequence(video, audio)
        patched = r.patch_sequence(data, 1, 100, 60, "aa", track_type="audio")
        self.assertEqual(patched, sequence(video, audio.replace(b'<In>31</In>', b'<In>0</In>').replace(b'abcd', b'aa')))

    def test_only_target_in_and_timemap_change(self):
        payload = (b'<In>31</In><MediaTimemapBA>abcd</MediaTimemapBA>'
                   b'<CompositionBA>1234</CompositionBA><Grade><In>99</In></Grade>'
                   b'<Name>\xce\xb2 &amp; shot</Name><FieldsBlob>beef</FieldsBlob>')
        data = sequence(clip(100, 60, payload) + clip(160, 60, payload))
        patched = r.patch_sequence(data, 1, 100, 60, "abcdef")
        expected = data.replace(b'<In>31</In>', b'<In>0</In>', 1).replace(b'<MediaTimemapBA>abcd</MediaTimemapBA>', b'<MediaTimemapBA>abcdef</MediaTimemapBA>', 1)
        self.assertEqual(patched, expected)
        self.assertEqual(r.patch_sequence(patched, 1, 100, 60), "abcdef")

    def test_self_closing_in_and_duplicate_target(self):
        data = sequence(clip(100, 60, b'<In/><MediaTimemapBA>abcd</MediaTimemapBA>'))
        self.assertIn(b'<In>0</In>', r.patch_sequence(data, 1, 100, 60, "aa"))
        with self.assertRaisesRegex(ValueError, "uniquely"):
            r.patch_sequence(sequence(clip(100, 60) * 2), 1, 100, 60, "aa")

    def test_archive_is_source_safe_and_other_members_are_byte_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.drt", Path(directory) / "output.drt"
            members = {"project.xml": b"opaque project", "MediaPool/folder.xml": b"opaque media",
                       "SeqContainer/main.xml": sequence(clip(100, 60, b'<In>7</In><MediaTimemapBA>abcd</MediaTimemapBA>'))}
            with zipfile.ZipFile(source, "w") as z:
                for name, value in members.items():
                    z.writestr(name, value)
            original = source.read_bytes()
            plan = dict(track_index=1, start=100, duration=60)
            r.patch_archive(source, output, plan, "aa")
            self.assertEqual(source.read_bytes(), original)
            with zipfile.ZipFile(output) as z:
                for name in ("project.xml", "MediaPool/folder.xml"):
                    self.assertEqual(z.read(name), members[name])
            with self.assertRaises(FileExistsError):
                r.patch_archive(source, source, plan, "aa")
            with zipfile.ZipFile(source, "a") as z:
                z.writestr("SeqContainer/nested.xml", b"nested")
            with self.assertRaisesRegex(ValueError, "nested"):
                r.patch_archive(source, Path(directory) / "nested.drt", plan, "aa")


class RecoveryTests(unittest.TestCase):
    def test_failed_import_readback_restores_original_and_removes_only_created_revision(self):
        project, timeline, revision, resolve = (mock.Mock() for _ in range(4))
        project.GetCurrentTimeline.return_value = timeline
        timeline.GetName.return_value = "Original"
        timeline.GetUniqueId.return_value = "original"
        timeline.Export.return_value = True
        timeline.GetMarkers.return_value = {}
        timeline.GetStartFrame.return_value = 0
        revision.GetName.return_value = "Revision"
        project.GetMediaPool.return_value.ImportTimelineFromFile.return_value = revision
        before = dict(tracks={}, items=[])
        after = dict(tracks={"video": 2}, items=[])
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(r, "inventory", side_effect=[before, after]), \
                mock.patch.object(r, "patch_archive"), \
                mock.patch.object(r, "set_current_timeline", return_value=(True, {})) as switch:
            result = r.execute(resolve, project, timeline,
                               dict(track_index=1, start=100, duration=60), dict(hex="aa"),
                               lambda path: directory, revision_name="Revision")
        self.assertFalse(result["success"])
        self.assertTrue(result["original_restored"])
        self.assertIn("other readable", result["error"]["message"])
        self.assertIs(switch.call_args.args[1], timeline)
        project.GetMediaPool.return_value.DeleteTimelines.assert_called_once_with([revision])

    def test_action_refuses_selection_fallback(self):
        import src.server as s
        project, timeline, item = mock.Mock(), mock.Mock(), mock.Mock()
        with mock.patch.object(s, "_get_selected_timeline_items", return_value=([item], ["used current video"])):
            result = s._timeline_set_speed_ramp_impl(project, timeline, dict(selected=True))
            self.assertIn("Select exactly one", result["error"]["message"])
        project.GetMediaPool.assert_not_called()


class LinkedPlannerTests(unittest.TestCase):
    def item(self, uid, kind, track, source_start):
        item = mock.Mock()
        item.GetUniqueId.return_value = uid
        item.GetStart.return_value = 100
        item.GetDuration.return_value = 60
        item.GetEnd.return_value = 160
        item.GetTrackTypeAndIndex.return_value = [kind, track]
        item.GetSourceStartFrame.return_value = source_start
        item.GetSourceEndFrame.return_value = source_start+60
        item.GetLinkedItems.return_value = []
        item.GetMediaPoolItem.return_value.GetClipProperty.return_value = {
            "Type": kind.capitalize(), "FPS": 24, "Frames": "480" if kind == "video" else "",
            "File Path": "/synthetic.wav"}
        return item

    def invoke(self, mode="follow", change=None, native_audio=True, ripple=False):
        import src.server as s
        from src.utils import media_analysis
        video = self.item("picture", "video", 1, 24)
        audio = self.item("sound", "audio", 2, 30)
        angle = self.item("angle", "video", 2, 48)
        video.GetLinkedItems.return_value = [audio]
        audio.GetLinkedItems.return_value = [video, angle]
        angle.GetLinkedItems.return_value = [audio]
        timeline, project = mock.Mock(), mock.Mock()
        timeline.GetIsTrackLocked.return_value = False
        timeline.GetTrackCount.return_value = 0
        if change:
            change(video, audio, angle, timeline)
        points = [dict(frame=0, speed=1), dict(frame=59, speed=3)]
        def encode(request):
            return dict(hex="aa", sourceEndSec=request["sourceStartSec"] + (2.5 if ripple else 4),
                        durationFrames=30, speedPoints=[dict(frame=0,speed=1),dict(frame=29,speed=3)],
                        speeds=[1]*request["durationFrames"], interpolation="smoothstep_speed_sampled_per_output_frame")
        with mock.patch.object(s, "_find_timeline_item_by_id", return_value=video), \
                mock.patch.object(s, "_timeline_fps", return_value=(24, None)), \
                mock.patch.object(s, "_has_method", side_effect=lambda item,name: name != "SetSpeed" or native_audio), \
                mock.patch.object(r, "timemap", side_effect=encode) as encoder, \
                mock.patch.object(media_analysis, "_ffprobe", return_value={"success": True,
                    "summary": {"audio": [{"duration_seconds": 12}]}}) as probe:
            result = s._timeline_set_speed_ramp_impl(project, timeline, dict(
                timeline_item_id="picture", speed_points=points, linked_audio=mode, ripple=ripple))
        project.GetMediaPool.assert_not_called()
        return result, encoder, probe

    def test_entire_link_group_uses_own_source_offsets_and_wav_extent(self):
        result, encoder, probe = self.invoke()
        self.assertTrue(result["success"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["plan"]["audio_retimed"])
        self.assertEqual([m["source_start_seconds"] for m in result["plan"]["members"]], [1, 1.25, 2])
        self.assertEqual(encoder.call_count, 3)
        probe.assert_called_once_with("/synthetic.wav")

    def test_preserve_audio_requires_explicit_mode_and_reports_sync_change(self):
        result, encoder, probe = self.invoke("preserve")
        self.assertTrue(result["success"])
        self.assertFalse(result["plan"]["audio_retimed"])
        self.assertEqual(encoder.call_count, 2)
        self.assertTrue(result["plan"]["warnings"])
        probe.assert_not_called()

    def test_locked_or_misaligned_linked_members_refuse(self):
        for change in (lambda v,a,b,t: setattr(a.GetDuration, "return_value", 59),
                       lambda v,a,b,t: setattr(a.GetStart, "return_value", 99),
                       lambda v,a,b,t: setattr(t.GetIsTrackLocked, "side_effect", lambda kind,index: kind == "audio")):
            result, _, _ = self.invoke(change=change)
            self.assertIn("error", result)

    def test_invalid_audio_mode_refuses(self):
        result, encoder, probe = self.invoke("maybe")
        self.assertIn("linked_audio", result["error"]["message"])
        encoder.assert_not_called()
        probe.assert_not_called()

    def test_ripple_plan_shortens_group_using_existing_source_range(self):
        result, _, _ = self.invoke(ripple=True)
        self.assertTrue(result["success"])
        self.assertFalse(result["plan"]["duration_preserved"])
        self.assertEqual(result["plan"]["shift_frames"],-30)
        self.assertEqual([m["new_duration"] for m in result["plan"]["members"]],[30,30,30])

    def test_ripple_refuses_audio_preserve_and_different_source_spans(self):
        result,_,_=self.invoke("preserve",ripple=True)
        self.assertIn("requires associated audio",result["error"]["message"])
        result,_,_=self.invoke(ripple=True,change=lambda v,a,b,t:setattr(a.GetSourceEndFrame,"return_value",300))
        self.assertIn("different source durations",result["error"]["message"])


    def test_older_audio_engine_cannot_silently_ignore_the_ramp(self):
        result, _, probe = self.invoke(native_audio=False)
        self.assertIn("21.1+", result["error"]["message"])
        probe.assert_not_called()


class RippleStateTests(unittest.TestCase):
    def test_link_topology_and_source_state_survive_record_geometry_changes(self):
        before=dict(tracks={'video':1,'audio':1},items=[
            dict(track_type='video',track_index=1,start=100,duration=60,links=[('audio',1,100,60)],GetSpeed=100),
            dict(track_type='audio',track_index=1,start=100,duration=60,links=[('video',1,100,60)]),
            dict(track_type='video',track_index=1,start=160,duration=20,links=[],source=55)])
        plan=dict(start=100,duration=60,shift_frames=-40,members=[
            dict(track_type=k,track_index=1,start=100,duration=60,new_duration=20) for k in ('video','audio')])
        after=r.ripple_inventory(before,plan)
        self.assertEqual(after['items'][0]['links'],[('audio',1,100,20)])
        self.assertEqual(after['items'][2]['start'],120)
        self.assertEqual(after['items'][2]['source'],55)
        self.assertEqual(before['items'][0]['duration'],60)

    def test_timeline_markers_ripple_after_cut_and_scale_inside_clip(self):
        marker=dict(duration=1,name='m',note='',color='Blue')
        plan=dict(start=200,duration=60,new_duration=20,shift_frames=-40,ripple=True)
        after=r.ripple_markers({50:marker,130:marker,170:marker},100,plan)
        self.assertEqual(set(after),{50,110,130})

    def test_audio_source_bound_uses_audio_duration_even_for_video_container(self):
        from src.utils import media_analysis
        with mock.patch.object(media_analysis, "_ffprobe", return_value={"success": True,
                "summary": {"audio": [{"duration_seconds": 12}]}}):
            self.assertEqual(r.source_duration_seconds({"Frames": "480", "File Path": "/synthetic.mov"}, 24, "audio"), 12)


if __name__ == "__main__":
    unittest.main()
