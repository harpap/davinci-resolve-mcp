"""Native speed ramps for video and aligned linked media in a DRT revision.

No source media is written. Retimed members receive a native map and duration;
ripple shifts following record positions. The original timeline is retained.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import uuid
import zipfile
from xml.parsers import expat

from src.utils.native_ripple import inventory, shift_sequence
from src.utils.resolve_writes import set_current_timeline

ROOT = Path(__file__).resolve().parents[2]
TIMEMAP_MODULE = ROOT / "resolve-advanced" / "vendor" / "drp-format" / "media-timemap.js"
NODE_BRIDGE = """
const m = require(process.argv[1]);
let input = '';
process.stdin.on('data', c => input += c);
process.stdin.on('end', () => {
  try {
    const p = JSON.parse(input);
    const result = p.mode === 'inspect' ? m.decodeTimemap(p.hex)
      : p.mode === 'ripple' ? m.buildRippleRampTimemapKeyed(p) : m.buildSmoothRampTimemapKeyed(p);
    process.stdout.write(JSON.stringify(result));
  } catch (e) { process.stderr.write(e.message); process.exitCode = 1; }
});
"""


def timemap(request):
    node = shutil.which("node")
    if not node or not TIMEMAP_MODULE.is_file():
        raise ValueError("Native speed ramps require Node and the bundled drp-format encoder")
    result = subprocess.run([node, "-e", NODE_BRIDGE, str(TIMEMAP_MODULE)],
                            input=json.dumps(request), text=True, capture_output=True,
                            timeout=60, check=False)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Native timemap encoder failed")
    return json.loads(result.stdout)


def validate_points(points, duration):
    if type(duration) is not int or not 2 <= duration <= 100000:
        raise ValueError("Clip duration must be 2..100000 output frames")
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError("speed_points needs at least two {frame, speed} points")
    previous = -1
    for point in points:
        if not isinstance(point, dict) or set(point) != {"frame", "speed"}:
            raise ValueError("Each speed point requires exactly frame and speed")
        frame, speed = point["frame"], point["speed"]
        try:
            finite = type(speed) in (int, float) and math.isfinite(speed) and speed > 0
        except OverflowError:
            finite = False
        if type(frame) is not int or not previous < frame < duration or not finite:
            raise ValueError("Speed points need increasing output frames and positive finite speed multipliers")
        previous = frame
    if points[0]["frame"] != 0 or points[-1]["frame"] != duration - 1:
        raise ValueError("Speed points must cover frame 0 through duration-1")


def source_duration_seconds(properties, source_fps, track_type):
    """Read source extent, including WAVs whose Resolve Frames field is empty."""
    frames = properties.get("Frames")
    if track_type == "audio" and properties.get("File Path"):
        from src.utils.media_analysis import _ffprobe
        probe = _ffprobe(str(properties["File Path"]))
        summary = probe.get("summary") or {}
        streams = summary.get("audio") or []
        values = [s["duration_seconds"] for s in streams if s.get("duration_seconds")]
        # Every mapped stream must have enough source; prefer the shortest
        # known audio stream over the container's possibly longer video span.
        duration = min(values) if values else (summary.get("format") or {}).get("duration_seconds")
        if not probe.get("success") or duration is None:
            raise ValueError("Could not read linked audio source duration")
    elif frames not in (None, ""):
        duration = int(frames) / source_fps
    else:
        raise ValueError("Could not read source frame count")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid source duration")
    return duration


def patch_sequence(data, track_index, start_frame, duration, hex_map=None, *, track_type="video", new_duration=None):
    """Locate a direct media item uniquely; patch retime fields by byte offsets.

    With no hex_map this reads the existing map, for post-import verification.
    XML is never reserialized, so opaque grades/effects stay byte exact.
    """
    if track_type not in {"video", "audio"}:
        raise ValueError("Native timemap track_type must be video or audio")
    vector = "VideoTrackVec" if track_type == "video" else "AudioTrackVec"
    tag = "Sm2TiVideoClip" if track_type == "video" else "Sm2TiAudioClip"
    if hex_map is not None and not re.fullmatch(r"[0-9a-fA-F]+", hex_map):
        raise ValueError("Invalid native timemap hex")
    parser = expat.ParserCreate()
    stack, candidates = [], []
    track, current, field = 0, None, None

    def begin(name, attrs):
        nonlocal track, current, field
        stack.append(name)
        if stack == ["Sm2SequenceContainer", vector, "Element"]:
            track += 1
        if (len(stack) == 7 and stack[1] == vector
                and stack[3:6] == ["Sm2TiTrack", "Items", "Element"]
                and name == tag):
            current = {"track": track, "spans": {}}
        elif current is not None and len(stack) == 8 and name in {"Start", "Duration", "In", "MediaTimemapBA"}:
            field = (name, parser.CurrentByteIndex)

    def end(name):
        nonlocal current, field
        if field and len(stack) == 8 and name == field[0]:
            key, begin_at = field
            content_at = data.index(b">", begin_at) + 1
            self_closed = data[content_at - 2:content_at] == b"/>"
            end_at = content_at if self_closed else data.index(b">", parser.CurrentByteIndex) + 1
            value = b"" if self_closed else data[content_at:parser.CurrentByteIndex]
            current[key] = value.decode("ascii").strip()
            current["spans"][key] = (begin_at, end_at)
            field = None
        if current is not None and len(stack) == 7:
            candidates.append(current)
            current = None
        stack.pop()

    parser.StartElementHandler, parser.EndElementHandler = begin, end
    parser.ExternalEntityRefHandler = lambda *args: 0
    parser.Parse(data, True)
    matches = [c for c in candidates if c["track"] == track_index
               and int(c.get("Start") or 0) == start_frame
               and int(c.get("Duration") or 0) == duration]
    if len(matches) != 1:
        raise ValueError(f"Cannot uniquely locate the target {track_type} clip in the native DRT")
    clip = matches[0]
    if not {"In", "MediaTimemapBA"} <= clip["spans"].keys():
        raise ValueError("Native clip lacks In or MediaTimemapBA")
    if hex_map is None:
        return clip["MediaTimemapBA"]
    patches = []
    values = [("In", "0"), ("MediaTimemapBA", hex_map)]
    if new_duration is not None:
        if type(new_duration) is not int or new_duration < 2:
            raise ValueError("new_duration must be an integer of at least two frames")
        values.append(("Duration", str(new_duration)))
    for key, value in values:
        begin_at, end_at = clip["spans"][key]
        patches.append((begin_at, end_at, f"<{key}>{value}</{key}>".encode("ascii")))
    for begin_at, end_at, value in sorted(patches, reverse=True):
        data = data[:begin_at] + value + data[end_at:]
    return data


def patch_archive(source, output, plan, hex_map=None):
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        sequences = [n for n in names if re.fullmatch(r"SeqContainer/[^/]+\.xml", n)]
        if len(names) != len(set(names)) or "project.xml" not in names or len(sequences) != 1:
            raise ValueError("Speed ramps currently require a single-sequence DRT; nested timelines are unsupported")
        entry = sequences[0]
        if hex_map is None:
            return patch_sequence(archive.read(entry), plan["track_index"], plan["start"], plan["duration"],
                                  track_type=plan.get("track_type", "video"))
        members = plan.get("members") or [plan]
        maps = [hex_map] if isinstance(hex_map, str) else hex_map
        if len(maps) != len(members):
            raise ValueError("Every retimed linked member requires its own native timemap")
        patched = archive.read(entry)
        if plan.get("ripple") and plan.get("shift_frames"):
            patched, _ = shift_sequence(patched, plan["start"] + plan["duration"], plan["shift_frames"])
        for member, blob in zip(members, maps):
            patched = patch_sequence(patched, member["track_index"], member["start"], member["duration"], blob,
                                     track_type=member.get("track_type", "video"), new_duration=member.get("new_duration"))
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as target:
            for info in archive.infolist():
                target.writestr(info, patched if info.filename == entry else archive.read(info))


def verify_map(hex_map, encoded, fps, source_start):
    decoded = timemap({"mode": "inspect", "hex": hex_map})
    if decoded.get("form") != "retimed":
        raise RuntimeError("Resolve dropped the native speed ramp")
    points = [decoded["origin"], *decoded["keyframes"]]
    expected, segment = source_start, 0
    for frame in range(len(encoded["speeds"]) + 1):
        time = frame / fps
        while segment < len(points) - 2 and time > points[segment + 1]["recordSec"] + 1e-10:
            segment += 1
        a, b = points[segment:segment + 2]
        span = b["recordSec"] - a["recordSec"]
        if span <= 0 or time > b["recordSec"] + 1e-8:
            raise RuntimeError("Resolve returned an incomplete speed ramp")
        actual = a["sourceSec"] + (b["sourceSec"] - a["sourceSec"]) * (time - a["recordSec"]) / span
        if abs(actual - expected) > 1e-6:
            raise RuntimeError(f"Speed ramp source mapping failed at output frame {frame}")
        if frame < len(encoded["speeds"]):
            expected += encoded["speeds"][frame] / fps


def ripple_inventory(before, plan):
    """Expected record geometry and link addresses after retime and ripple."""
    result = copy.deepcopy(before)
    members = plan.get("members") or [plan]
    slots = {(m.get("track_type", "video"), m["track_index"], m["start"], m["duration"]):
             m.get("new_duration", m["duration"]) for m in members}
    cut, delta = plan["start"] + plan["duration"], plan.get("shift_frames", 0)
    def address(key):
        kind, track, start, duration = key
        if tuple(key) in slots:
            return (kind, track, start, slots[tuple(key)])
        return (kind, track, start + (delta if start >= cut else 0), duration)
    for row in result["items"]:
        key = (row["track_type"], row["track_index"], row["start"], row["duration"])
        _, _, row["start"], row["duration"] = address(key)
        row["links"] = sorted(address(key) for key in row.get("links", []))
    return result


def ripple_markers(markers, timeline_start, plan):
    if not plan.get("ripple") or not plan.get("shift_frames"):
        return markers
    start = plan["start"] - timeline_start
    end = start + plan["duration"]
    def move(frame):
        if frame < start:
            return frame
        if frame >= end:
            return frame + plan["shift_frames"]
        return start + round((frame-start) * plan["new_duration"] / plan["duration"])
    result = {}
    for frame, marker in markers.items():
        pos = move(frame)
        if pos in result:
            raise ValueError("Timeline markers collide after ripple retiming")
        result[pos] = dict(marker, duration=max(1, move(frame+marker["duration"])-pos))
    return result


def execute(resolve, project, timeline, plan, encoded, safe_dir, revision_name=None):
    staging = Path(safe_dir(str(ROOT / "logs" / "speed-ramp"))) / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=False)
    original_active = project.GetCurrentTimeline()
    result = {"success": False, "original_timeline_id": timeline.GetUniqueId(),
              "staging_directory": str(staging), "render_verified": False}
    revision = None
    try:
        members = plan.get("members") or [plan]
        maps = [encoded, *encoded.get("linked_maps", [])]
        before = inventory(timeline)
        markers = timeline.GetMarkers() or {}
        expected = ripple_inventory(before, plan)
        expected_markers = ripple_markers(markers, int(timeline.GetStartFrame()), plan)
        source, output = staging / "original.drt", staging / "speed-ramp.drt"
        if not timeline.Export(str(source), resolve.EXPORT_DRT):
            raise RuntimeError("Native timeline export failed")
        patch_archive(source, output, plan, [m["hex"] for m in maps])
        revision = project.GetMediaPool().ImportTimelineFromFile(str(output), {"importSourceClips": False})
        if not revision:
            raise RuntimeError("Resolve refused the speed ramp revision")
        result["timeline_id"] = revision.GetUniqueId()
        name = revision_name or f"{timeline.GetName()} - speed ramp {uuid.uuid4().hex[:6]}"
        if not revision.SetName(name) or revision.GetName() != name:
            raise RuntimeError("Could not name the speed ramp revision")
        selected, detail = set_current_timeline(project, revision)
        if not selected:
            raise RuntimeError(f"Could not select revision: {detail}")
        if expected_markers != markers:
            if not revision.DeleteMarkersByColor("All"):
                raise RuntimeError("Could not update timeline markers for ripple")
            for frame, marker in expected_markers.items():
                if not revision.AddMarker(frame, marker["color"], marker["name"], marker["note"],
                                          marker["duration"], marker.get("customData", "")):
                    raise RuntimeError("Could not restore a rippled timeline marker")
        after = inventory(revision)
        retimed_slots = {(m.get("track_type", "video"), m["track_index"], m["start"], m.get("new_duration", m["duration"]))
                         for m in members}
        def comparable(state):
            rows = []
            for row in state["items"]:
                row = dict(row)
                if (row["track_type"], row["track_index"], row["start"], row["duration"]) in retimed_slots:
                    for key in ("GetSpeed", "GetSourceStartTime", "GetSourceEndTime"):
                        row.pop(key, None)
                rows.append(row)
            return {"tracks": state["tracks"], "items": rows}
        if comparable(expected) != comparable(after) or (revision.GetMarkers() or {}) != expected_markers:
            (staging / "inventory-diff.json").write_text(json.dumps({"expected": expected, "after": after}, indent=2), encoding="utf-8")
            raise RuntimeError("Native import changed other readable item state; see inventory-diff.json")
        exported = staging / "readback.drt"
        if not revision.Export(str(exported), resolve.EXPORT_DRT):
            raise RuntimeError("Could not re-export speed ramp for verification")
        for member, mapping in zip(members, maps):
            lookup = dict(member, duration=member.get("new_duration", member["duration"]))
            verify_map(patch_archive(exported, None, lookup), mapping, plan["fps"], member["source_start_seconds"])
        selected, detail = set_current_timeline(project, timeline)
        if not selected or inventory(timeline) != before or (timeline.GetMarkers() or {}) != markers:
            raise RuntimeError(f"Could not verify the unchanged original: {detail}")
        selected, detail = set_current_timeline(project, revision)
        if not selected:
            raise RuntimeError(f"Could not select verified revision: {detail}")
        result.update(success=True, timeline=name, plan=plan, original_retained=True,
                      verification={"scope": "native_timemap_and_readable_state", "verified": True,
                                    "render_verified": False},
                      interpolation=encoded["interpolation"],
                      unverified_components=["rendered_pixels", "opaque_state_after_import"],
                      _changes={"items_retimed": len(members), "items_added": 0, "items_deleted": 0,
                                "items_moved": sum(row["start"] >= plan["start"]+plan["duration"]
                                                   for row in before["items"]) if plan.get("shift_frames") else 0,
                                "timeline_duration_delta_frames": plan.get("shift_frames", 0)})
    except Exception as exc:
        result["error"] = {"code": "SPEED_RAMP_REVISION_FAILED", "message": str(exc), "retryable": False}
    finally:
        if not result["success"]:
            restored, detail = set_current_timeline(project, original_active)
            result["original_restored"] = restored
            if not restored:
                result["restore_error"] = detail
            # Only this operation's imported revision is eligible for deletion.
            if revision and restored:
                try:
                    result["failed_revision_deleted"] = bool(project.GetMediaPool().DeleteTimelines([revision]))
                except Exception as exc:
                    result["failed_revision_deleted"] = False
                    result["cleanup_error"] = str(exc)
    return result
