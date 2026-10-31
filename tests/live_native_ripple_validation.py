"""Opt-in native ripple validation, synthetic media and disposable project only.

Run: RESOLVE_VERIFY=1 python tests/live_native_ripple_validation.py
The user's project is saved and restored. Generated evidence stays in logs/.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if os.environ.get("RESOLVE_VERIFY") != "1":
        print("Set RESOLVE_VERIFY=1 to run against Resolve")
        return
    import src.server as s
    work = Path(s._resolve_safe_dir(str(ROOT / "logs" / "native-ripple-live"))) / uuid.uuid4().hex
    work.mkdir(parents=True)
    source = work / "synthetic.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True)
    resolve = s.get_resolve()
    pm = resolve.GetProjectManager()
    original = pm.GetCurrentProject()
    original_name = original.GetName() if original else None
    original_timeline = original.GetCurrentTimeline() if original else None
    original_timeline_id = original_timeline.GetUniqueId() if original_timeline else None
    if original and not pm.SaveProject():
        raise RuntimeError("Could not save outgoing project")
    project = None
    created_projects = []
    completed = False
    name = "_mcp_native_ripple_" + uuid.uuid4().hex[:8]
    try:
        project = pm.CreateProject(name)
        assert project, "Could not create disposable project"
        created_projects.append(name)
        assert project.SetSetting("timelineFrameRate", "24")
        assert resolve.OpenPage("edit")
        pool = project.GetMediaPool()
        clip = pool.ImportMedia([str(source)])[0]
        tl = pool.CreateEmptyTimeline("NativeRipple")
        assert project.SetCurrentTimeline(tl)
        start = int(tl.GetStartFrame())
        # Resolve's positioned append uses end-exclusive source frames on the
        # measured builds; fail here if the host disagrees.
        for offset in (0, 48, 96):
            items = pool.AppendToTimeline([dict(mediaPoolItem=clip, startFrame=0, endFrame=48,
                                               recordFrame=start + offset, trackIndex=1)])
            assert items
        video = tl.GetItemListInTrack("video", 1)
        assert [(i.GetStart(), i.GetDuration()) for i in video] == [(start+i*48, 48) for i in range(3)]
        tail = video[-1]
        assert tail.SetSpeed({"Percentage": 200.0, "RippleTimeline": False})
        print(f"Retimed tail: speed={tail.GetSpeed()}, duration={tail.GetDuration()}", flush=True)
        assert float(tail.GetSpeed()["Percentage"]) == 200.0
        assert tail.SetCDL({"NodeIndex": "1", "Slope": "1.1 0.9 0.8", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": "0.7"})
        assert tail.SetProperty("ZoomX", 0.7)
        assert tail.SetProperty("ZoomY", 0.7)
        assert tail.SetClipColor("Orange")
        assert tail.AddFlag("Blue")
        assert tail.AddMarker(4, "Green", "clip marker", "keep", 1, "clip-data")
        assert tl.AddMarker(100, "Blue", "timeline marker", "move", 1, "timeline-data")
        original_layout = [(i.GetStart(), i.GetDuration()) for i in tl.GetItemListInTrack("video", 1)]
        assert tl.SetCurrentTimecode("01:00:04:12")
        assert project.ExportCurrentFrameAsStill(str(work / "before.png"))
        params = {"record_frame": 48, "clip_infos": [
            {"clip_id": clip.GetUniqueId(), "start_frame": 0, "end_frame": 24, "track_index": 1, "media_type": 1},
            {"clip_id": clip.GetUniqueId(), "start_frame": 0, "end_frame": 24, "track_index": 1, "media_type": 2}]}
        plan = s.timeline("ripple_insert", params)
        assert plan.get("success"), plan
        result = s.timeline("ripple_insert", dict(params, dry_run=False))
        if result.get("status") == "confirmation_required":
            result = s.timeline("ripple_insert", dict(params, dry_run=False, confirm_token=result["confirm_token"]))
        (work / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True), flush=True)
        assert result.get("success"), result
        revision = project.GetCurrentTimeline()
        assert revision.GetUniqueId() != tl.GetUniqueId()
        assert [(i.GetStart(), i.GetDuration()) for i in tl.GetItemListInTrack("video", 1)] == original_layout
        assert revision.SetCurrentTimecode("01:00:05:12")
        assert project.ExportCurrentFrameAsStill(str(work / "after.png"))
        def pixels(path):
            return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        assert pixels(work / "before.png") == pixels(work / "after.png"), "Rendered frame changed after shift"
        assert revision.GetMarkers()[124]["customData"] == "timeline-data"
        # A 48 fps source window consumes half as many 24 fps timeline frames.
        fast_source = work / "synthetic48.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=48:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(fast_source)], check=True)
        fast = pool.ImportMedia([str(fast_source)])[0]
        params = {"record_frame": 0, "clip_infos": [{"clip_id": fast.GetUniqueId(), "start_frame": 0, "end_frame": 48, "track_index": 1, "media_type": 1}]}
        result = s.timeline("ripple_insert", dict(params, dry_run=False))
        if result.get("status") == "confirmation_required":
            result = s.timeline("ripple_insert", dict(params, dry_run=False, confirm_token=result["confirm_token"]))
        print(json.dumps(result, ensure_ascii=True), flush=True)
        assert result.get("success") and result["shift_frames"] == 24, result
        # Fractional-rate truncation must agree with native append, including
        # linked sound. Rounding this to 243 leaves a one-frame hole.
        assert pm.SaveProject()
        project = pm.CreateProject(name + "_fractional")
        assert project
        created_projects.append(name + "_fractional")
        assert project.SetSetting("timelineFrameRate", "29.97")
        pool = project.GetMediaPool()
        fractional = pool.CreateEmptyTimeline("FractionalRipple")
        assert project.SetCurrentTimeline(fractional)
        source30 = work / "synthetic30.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=10",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=10",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source30)], check=True)
        clip30 = pool.ImportMedia([str(source30)])[0]
        params = {"record_frame": 0, "clip_infos": [
            {"clip_id": clip30.GetUniqueId(), "start_frame": 0, "end_frame": 243, "track_index": 1, "media_type": mt}
            for mt in (1, 2)]}
        result = s.timeline("ripple_insert", dict(params, dry_run=False))
        if result.get("status") == "confirmation_required":
            result = s.timeline("ripple_insert", dict(params, dry_run=False, confirm_token=result["confirm_token"]))
        print(json.dumps(result, ensure_ascii=True), flush=True)
        assert result.get("success") and result["shift_frames"] == 242, result
        print("PASS: grade pixels, retime, linked A/V, markers, mixed/fractional-fps insertion; original retained", flush=True)
        completed = True
    finally:
        if project:
            pm.SaveProject()
            if completed:
                from src.utils.project_cleanup import delete_project_safely
                for created_name in created_projects:
                    cleanup = delete_project_safely(pm, created_name, switch_to=original_name)
                    if not cleanup["success"]:
                        print(f"Disposable project retained: {created_name}: {cleanup}", flush=True)
        if original_name:
            restored = pm.LoadProject(original_name)
            assert restored, "Could not restore original project"
            if original_timeline_id:
                matches = [restored.GetTimelineByIndex(i) for i in range(1, restored.GetTimelineCount()+1)]
                target = next(i for i in matches if i.GetUniqueId() == original_timeline_id)
                assert restored.SetCurrentTimeline(target)
        print(f"Evidence: {work}", flush=True)


if __name__ == "__main__":
    main()
