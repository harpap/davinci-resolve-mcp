"""Opt-in native ramp readback AND rendered-frame verification, synthetic only.

RESOLVE_VERIFY=1 python tests/live_speed_ramp_validation.py
Add --linked to verify independent offset WAV audio on A2 against timed beeps.
Add --ripple to preserve source range, resize the group, and shift later edits.
Saves/restores the outgoing project; artifacts stay under logs/speed-ramp-live.
"""
import hashlib
import array
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if os.environ.get("RESOLVE_VERIFY") != "1":
        raise SystemExit("Set RESOLVE_VERIFY=1 to run the synthetic live test")
    import src.server as s
    from src.utils import speed_ramp
    from src.utils.project_cleanup import delete_project_safely
    resolve = s.get_resolve()
    pm = resolve.GetProjectManager()
    outgoing = pm.GetCurrentProject()
    outgoing_name = outgoing.GetName() if outgoing else None
    outgoing_timeline = outgoing.GetCurrentTimeline() if outgoing else None
    outgoing_id = outgoing_timeline.GetUniqueId() if outgoing_timeline else None
    outgoing_tc = outgoing_timeline.GetCurrentTimecode() if outgoing_timeline else None
    outgoing_page = resolve.GetCurrentPage()
    if outgoing and not pm.SaveProject():
        raise RuntimeError("Could not save outgoing project")
    work = Path(s._resolve_safe_dir(str(ROOT / "logs" / "speed-ramp-live"))) / uuid.uuid4().hex
    work.mkdir(parents=True)
    source = work / "synthetic.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=12",
                    "-c:v", "prores_ks", "-profile:v", "0", "-pix_fmt", "yuv422p10le",
                    "-timecode", "01:00:00:00", str(source)], check=True)
    name = "_mcp_speed_ramp_" + uuid.uuid4().hex[:8]
    project = None
    receipt = {"version": resolve.GetVersionString(), "samples": [], "work": str(work)}
    linked = "--linked" in sys.argv
    ripple = "--ripple" in sys.argv
    try:
        project = pm.CreateProject(name)
        assert project, "Could not create disposable project"
        assert project.SetSetting("timelineFrameRate", "24")
        assert project.SetSetting("timelineResolutionWidth", "640")
        assert project.SetSetting("timelineResolutionHeight", "360")
        assert resolve.OpenPage("edit")
        pool = project.GetMediaPool()
        media = pool.ImportMedia([str(source)])[0]
        reference = pool.CreateEmptyTimeline("Reference")
        assert project.SetCurrentTimeline(reference)
        ref = pool.AppendToTimeline([dict(mediaPoolItem=media, startFrame=0, endFrame=288, mediaType=1)])[0]
        assert ref.SetProperty("FlipX", True)
        timeline = pool.CreateEmptyTimeline("RampTarget")
        assert project.SetCurrentTimeline(timeline)
        target = pool.AppendToTimeline([dict(mediaPoolItem=media, startFrame=24, endFrame=120,
                                              mediaType=1, trackIndex=1, recordFrame=int(timeline.GetStartFrame()))])[0]
        print({"initial_duration": target.GetDuration(), "source_start": target.GetSourceStartFrame(),
               "source_end": target.GetSourceEndFrame()}, flush=True)
        assert target.SetProperty("FlipX", True)
        assert target.SetClipColor("Orange")
        assert timeline.AddMarker(20, "Blue", "preserve", "timeline marker", 1, "ramp-test")
        assert target.GetDuration() == 96, target.GetDuration()
        if linked:
            # Independent WAV, intentionally offset by six source frames from
            # picture. Resolve does not provide a Frames property for this.
            audio_path = work / "synthetic-beeps.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            r"aevalsrc=if(lt(mod(t\,0.5)\,0.02)\,0.7*sin(2*PI*1000*t)\,0):s=48000:d=12",
                            "-c:a", "pcm_s16le", str(audio_path)], check=True)
            audio_media = pool.ImportMedia([str(audio_path)])[0]
            # Exercise a non-default audio track and preserve a linked group.
            assert timeline.AddTrack("audio", "mono")
            audio = pool.AppendToTimeline([dict(mediaPoolItem=audio_media, startFrame=30, endFrame=126,
                                                mediaType=2, trackIndex=2, recordFrame=int(target.GetStart()))])[0]
            assert timeline.SetClipsLinked([target, audio], True)
            assert int(audio.GetSourceStartFrame()) == 30
        if ripple:
            tail_start = int(target.GetEnd())
            tail = pool.AppendToTimeline([dict(mediaPoolItem=media, startFrame=0, endFrame=24,
                mediaType=1, trackIndex=1, recordFrame=tail_start)])[0]
            assert tail.SetProperty("FlipX", True)
            if linked:
                tail_audio = pool.AppendToTimeline([dict(mediaPoolItem=audio_media, startFrame=0, endFrame=24,
                    mediaType=2, trackIndex=2, recordFrame=tail_start)])[0]
                assert timeline.SetClipsLinked([tail, tail_audio], True)
            assert timeline.AddMarker(108, "Green", "tail", "ripple marker", 1, "tail-test")
        params = {"timeline_item_id": target.GetUniqueId(), "ripple": ripple, "speed_points": [
            {"frame": 0, "speed": 1}, {"frame": 28, "speed": 3},
            {"frame": 67, "speed": 3}, {"frame": 95, "speed": 1}]}
        plan = s.timeline("set_speed_ramp", params)
        assert plan.get("success") and plan.get("dry_run"), plan
        assert project.GetCurrentTimeline().GetUniqueId() == timeline.GetUniqueId()
        result = s.timeline("set_speed_ramp", dict(params, dry_run=False))
        if result.get("status") == "confirmation_required":
            result = s.timeline("set_speed_ramp", dict(params, dry_run=False, confirm_token=result["confirm_token"]))
        receipt["result"] = result
        print(json.dumps(result), flush=True)
        assert result.get("success"), result
        revision = project.GetCurrentTimeline()
        duration = result["plan"]["new_duration"]
        encoded = speed_ramp.timemap({"points": result["plan"]["speed_points"], "durationFrames": duration,
                                     "sourceStartSec": 1, "sourceDurationSec": 12,
                                     "fps": 24, "uniqueId": str(uuid.uuid4())})

        def still(tl, frame, label):
            assert project.SetCurrentTimeline(tl)
            absolute = int(tl.GetStartFrame()) + frame
            tc = f"{absolute // 86400:02}:{absolute // 1440 % 60:02}:{absolute // 24 % 60:02}:{absolute % 24:02}"
            assert tl.SetCurrentTimecode(tc)
            path = work / (label + ".png")
            assert project.ExportCurrentFrameAsStill(str(path))
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
            return hashlib.sha256(raw).hexdigest()

        for frame in sorted(set(round((duration-1)*p) for p in (0,.125,.25,.5,.75,1))):
            expected = 24 + sum(encoded["speeds"][:frame])
            actual = still(revision, frame, f"ramp-{frame}")
            candidates = range(max(0, math.floor(expected) - 1), math.ceil(expected) + 2)
            matches = [f for f in candidates if still(reference, f, f"source-{frame}-{f}") == actual]
            receipt["samples"].append({"output_frame": frame, "expected_source_frame": expected, "matching_source_frames": matches})
            assert matches, receipt["samples"][-1]
        if ripple:
            delta = duration-96
            assert duration < 96
            assert int(tail.GetStart()) == tail_start
            assert still(timeline, 108, "tail-before") == still(revision, 108+delta, "tail-after")
            for kind, track in (("video",1),("audio",2)) if linked else (("video",1),):
                items = revision.GetItemListInTrack(kind,track)
                assert sorted(int(i.GetStart()) for i in items) == [int(target.GetStart()),tail_start+delta]
                assert sorted(int(i.GetDuration()) for i in items) == sorted([duration,24])
            assert (revision.GetMarkers() or {})[108+delta]["customData"] == "tail-test"
            receipt["ripple_verified"] = dict(original_duration=96,new_duration=duration,shift_frames=delta,
                                             tail_pixels_match=True,all_tracks_shifted=True)
        receipt["render_verified"] = True
        if linked:
            assert result["plan"]["audio_retimed"]
            assert len(result["plan"]["members"]) == 2
            # Unlike a map readback, this proves that the audio engine follows
            # the source timing. Retain the normal-speed render as a control.
            def render_beeps(tl, label):
                assert project.SetCurrentTimeline(tl)
                assert project.SetCurrentRenderMode(1)
                assert project.SetCurrentRenderFormatAndCodec("mov", "ProRes422LT")
                assert project.SetRenderSettings(dict(TargetDir=str(work), CustomName=label,
                    SelectAllFrames=False, MarkIn=int(tl.GetStartFrame()),
                    MarkOut=int(tl.GetStartFrame())+(96 if tl.GetUniqueId()==timeline.GetUniqueId() else duration)-1,
                    ExportVideo=True, ExportAudio=True, AudioCodec="lpcm",
                    AudioBitDepth=24, AudioSampleRate=48000))
                job = project.AddRenderJob()
                assert job and project.StartRendering([job], False)
                deadline = time.monotonic() + 90
                while project.IsRenderingInProgress() and time.monotonic() < deadline:
                    time.sleep(.5)
                assert project.GetRenderJobStatus(job)["JobStatus"] == "Complete"
                path = work / (label + ".mov")
                raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000",
                                      "-f", "s16le", "-"], capture_output=True, check=True).stdout
                samples = array.array("h", raw)
                active = [max(map(abs, samples[i:i+48])) > 1000 for i in range(0, len(samples), 48)]
                starts, was_active = [], False
                for i, on in enumerate(active):
                    if on and not was_active:
                        starts.append(i / 1000)
                    was_active = on
                assert project.DeleteRenderJob(job)
                return starts
            baseline_events = render_beeps(timeline, "audio-control")
            actual_events = render_beeps(revision, "audio-ramp")
            # The first beep after source 1.25s is at 1.5s. Interpolate the
            # inverse source-position curve at every subsequent half-second.
            source_positions = [1.25]
            for speed in encoded["speeds"]:
                source_positions.append(source_positions[-1] + speed / 24)
            expected_events = []
            source_time = 1.5
            while source_time < source_positions[-1]:
                index = next(i for i in range(duration) if source_positions[i] <= source_time < source_positions[i+1])
                expected_events.append((index + (source_time-source_positions[index]) /
                                        (source_positions[index+1]-source_positions[index])) / 24)
                source_time += .5
            receipt["audio"] = dict(baseline_events=baseline_events, actual_events=actual_events,
                                    expected_events=expected_events)
            assert len(actual_events) == len(expected_events), receipt["audio"]
            assert all(abs(a-b) < .04 for a,b in zip(actual_events, expected_events)), receipt["audio"]
            if ripple:
                assert len(actual_events) == len(baseline_events)
                assert actual_events[-1] < baseline_events[-1]
            else:
                assert len(actual_events) > len(baseline_events)
            receipt["audio_render_verified"] = True
        print("PASS: 1x -> 3x -> 1x mapping and six rendered samples, nonzero source in/TC, transform and timeline marker preserved", flush=True)
    finally:
        (work / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        if project:
            pm.SaveProject()
            print(delete_project_safely(pm, name, switch_to=outgoing_name), flush=True)
        if outgoing_name:
            restored = pm.LoadProject(outgoing_name)
            assert restored
            if outgoing_id:
                original = next(restored.GetTimelineByIndex(i) for i in range(1, restored.GetTimelineCount() + 1)
                                if restored.GetTimelineByIndex(i).GetUniqueId() == outgoing_id)
                assert restored.SetCurrentTimeline(original)
                assert original.SetCurrentTimecode(outgoing_tc)
        if outgoing_page:
            assert resolve.OpenPage(outgoing_page)
        print(f"Evidence: {work}", flush=True)


if __name__ == "__main__":
    main()
