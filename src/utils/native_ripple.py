"""Open a gap in a native DRT without reconstructing timeline items.

Only direct record Start fields are patched. Opaque grade, Fusion, retime,
keyframe, audio and link payloads, and every other ZIP member, stay byte exact.
The live operation imports a revision; it never deletes the working edit.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import uuid
import zipfile
from xml.parsers import expat

from src.utils.resolve_writes import set_current_timeline


def shift_sequence(data: bytes, at: int, delta: int) -> tuple[bytes, int]:
    """Patch direct track item positions, rejecting unknown or crossing items.

    Expat byte offsets avoid reserializing XML (or changing binary payloads).
    Only the outer sequence's tracks are eligible, never a nested composition.
    """
    if type(at) is not int or type(delta) is not int or delta <= 0:
        raise ValueError("at must be an integer and delta a positive integer")
    parser = expat.ParserCreate()
    stack, items, patches = [], [], []
    current = None
    field = None
    vectors = {"VideoTrackVec", "AudioTrackVec", "SubtitleTrackVec"}

    def start(name, attrs):
        nonlocal current, field
        stack.append(name)
        if len(stack) == 7 and stack[1] in vectors and stack[3:6] == ["Sm2TiTrack", "Items", "Element"]:
            if name not in {"Sm2TiVideoClip", "Sm2TiAudioClip", "Sm2TiSubtitleClip", "Sm2TiTransition"}:
                raise ValueError(f"Unsupported native timeline item: {name}")
            current = {"tag": name}
        elif current is not None and len(stack) == 8 and name in {"Start", "Duration"}:
            begin = data.index(b">", parser.CurrentByteIndex) + 1
            field = (name, begin)

    def end(name):
        nonlocal current, field
        if field and len(stack) == 8 and name == field[0]:
            key, begin = field
            raw = data[begin:parser.CurrentByteIndex]
            if not re.fullmatch(rb"-?\d+", raw):
                raise ValueError(f"Unsupported native {key} value")
            current[key] = int(raw)
            if key == "Start":
                current["span"] = (begin, parser.CurrentByteIndex)
            field = None
        if current is not None and len(stack) == 7:
            if "Start" not in current or "Duration" not in current:
                raise ValueError("Native item lacks Start/Duration")
            items.append(current)
            current = None
        stack.pop()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.ExternalEntityRefHandler = lambda *args: 0
    parser.Parse(data, True)
    for item in items:
        pos, duration = item["Start"], item["Duration"]
        if duration < 0 or pos < at < pos + duration:
            raise ValueError("Insert point crosses a native clip or transition")
        if pos >= at:
            begin, end_pos = item["span"]
            patches.append((begin, end_pos, str(pos + delta).encode()))
    for begin, end_pos, value in reversed(patches):
        data = data[:begin] + value + data[end_pos:]
    return data, len(patches)


def shift_archive(source: Path, output: Path, at: int, delta: int) -> int:
    """Copy a native single-sequence DRT, patching only record positions."""
    with zipfile.ZipFile(source) as original:
        names = original.namelist()
        sequences = [n for n in names if re.fullmatch(r"SeqContainer/[^/]+\.xml", n)]
        if len(names) != len(set(names)) or "project.xml" not in names or len(sequences) != 1:
            raise ValueError("Expected a native single-sequence DRT; nested timelines are not supported")
        entry = sequences[0]
        patched, shifted = shift_sequence(original.read(entry), at, delta)
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as target:
            for info in original.infolist():
                target.writestr(info, patched if info.filename == entry else original.read(info))
    return shifted


def inventory(timeline):
    """Stable record/source identity, readable state and link topology.

    IDs change on native import, so links are represented by track/item slots.
    A failed read is a failure, never an empty successful inventory.
    """
    rows, addresses, links = [], {}, []
    counts = {}
    for kind in ("video", "audio", "subtitle"):
        counts[kind] = int(timeline.GetTrackCount(kind))
        for track in range(1, counts[kind] + 1):
            for item in timeline.GetItemListInTrack(kind, track) or []:
                address = (kind, track, int(item.GetStart()), int(item.GetDuration()))
                addresses[item.GetUniqueId()] = address
                media = item.GetMediaPoolItem()
                row = dict(track_type=kind, track_index=track, start=int(item.GetStart()),
                           duration=int(item.GetDuration()), name=item.GetName(),
                           media=media.GetMediaId() if media else None)
                methods = set(dir(item))
                for method in ("GetProperty", "GetClipEnabled", "GetClipColor", "GetFlagList",
                               "GetMarkers", "GetSourceStartTime", "GetSourceEndTime",
                               "GetFusionCompCount", "GetSpeed", "GetFades"):
                    if method in methods:
                        row[method] = getattr(item, method)()
                links.append([link.GetUniqueId() for link in (item.GetLinkedItems() or [])])
                rows.append(row)
    for row, linked in zip(rows, links):
        row["links"] = sorted(addresses[key] for key in linked)
    return {"tracks": counts, "items": rows}


def execute(resolve, project, timeline, built_inserts, plan, safe_dir, revision_name=None):
    """Export -> shift -> import revision -> verify -> append -> verify.

    On any failure reselect the original. Keep failed revisions and exports for
    diagnosis; never report partial completion as success or delete user work.
    """
    staging = Path(safe_dir(str(Path(__file__).resolve().parents[2] / "logs" / "ripple-insert"))) / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=False)
    original_active = project.GetCurrentTimeline()
    source, shifted_path = staging / "original.drt", staging / "shifted.drt"
    revision = None
    result = {"success": False, "original_timeline": timeline.GetName(),
              "original_timeline_id": timeline.GetUniqueId(), "staging_directory": str(staging)}
    try:
        before = inventory(timeline)
        at, shift = plan["insert_frame_absolute"], plan["shift_frames"]
        expected = {"tracks": before["tracks"], "items": [
            dict(row, start=row["start"] + (shift if row["start"] >= at else 0),
                 links=[(kind, track, pos + (shift if pos >= at else 0), duration)
                        for kind, track, pos, duration in row["links"]])
            for row in before["items"]]}
        if not timeline.Export(str(source), resolve.EXPORT_DRT):
            raise RuntimeError("Native timeline export failed")
        shifted = shift_archive(source, shifted_path, at, shift)
        if shifted != plan["tail_item_count"]:
            raise RuntimeError("Native item count differs from the live plan")
        pool = project.GetMediaPool()
        revision = pool.ImportTimelineFromFile(str(shifted_path), {"importSourceClips": False})
        if not revision:
            raise RuntimeError("Resolve refused the shifted native timeline")
        name = revision_name or f"{timeline.GetName()} - insert {uuid.uuid4().hex[:8]}"
        if not revision.SetName(name) or revision.GetName() != name:
            raise RuntimeError("Could not name the insertion revision")
        result.update(timeline=name, timeline_id=revision.GetUniqueId())
        selected, detail = set_current_timeline(project, revision)
        if not selected:
            raise RuntimeError(f"Could not select insertion revision: {detail}")
        actual = inventory(revision)
        if actual != expected:
            (staging / "inventory-diff.json").write_text(json.dumps({"expected": expected, "actual": actual}, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError("Native import did not preserve the edit; see inventory-diff.json")
        # Native export/import retains timeline markers, whose record positions
        # need the same shift as clips. Clip markers remain inside their items.
        markers = timeline.GetMarkers() or {}
        expected_markers = {}
        if markers:
            if not revision.DeleteMarkersByColor("All"):
                raise RuntimeError("Could not shift timeline markers")
            tl_start = int(timeline.GetStartFrame())
            for frame, marker in markers.items():
                relative_at = at - tl_start
                pos = frame + (shift if frame >= relative_at else 0)
                duration = marker["duration"] + (shift if frame < relative_at < frame + marker["duration"] else 0)
                if not revision.AddMarker(pos, marker["color"], marker["name"], marker["note"], duration, marker.get("customData", "")):
                    raise RuntimeError("Timeline marker restore failed")
                expected_markers[pos] = dict(marker, duration=duration)
        if (revision.GetMarkers() or {}) != expected_markers:
            raise RuntimeError("Timeline markers failed readback")
        # End frames here follow the server's existing append contract. Each
        # actual duration is checked below, including mixed-rate inserts.
        for info in built_inserts:
            if not pool.AppendToTimeline([info]):
                raise RuntimeError("AppendToTimeline failed on the revision")
        # Explicit video/audio rows for the same source window form one insert.
        # Restore their link without touching the imported edit's link groups.
        groups = {}
        for info in built_inserts:
            key = (info["mediaPoolItem"].GetMediaId(), info["startFrame"], info["endFrame"], info["recordFrame"])
            kind = "video" if info["mediaType"] == 1 else "audio"
            found = [item for item in revision.GetItemListInTrack(kind, info["trackIndex"]) or []
                     if int(item.GetStart()) == info["recordFrame"] and item.GetMediaPoolItem()
                     and item.GetMediaPoolItem().GetMediaId() == key[0]]
            if len(found) != 1:
                raise RuntimeError("Cannot uniquely identify an inserted item")
            groups.setdefault(key, []).append((kind, found[0]))
        for group in groups.values():
            if len(group) == 2 and {kind for kind, _ in group} == {"video", "audio"}:
                items = [item for _, item in group]
                if not revision.SetClipsLinked(items, True):
                    raise RuntimeError("Could not link inserted picture and sound")
                for item, other in (items, items[::-1]):
                    if other.GetUniqueId() not in {link.GetUniqueId() for link in item.GetLinkedItems() or []}:
                        raise RuntimeError("Inserted A/V link did not persist")
        after = inventory(revision)
        def geometry(row):
            return row["track_type"], row["track_index"], row["start"], row["duration"], row["media"]
        wanted = Counter(geometry(row) for row in expected["items"])
        for info, summary in zip(built_inserts, plan["inserts"]):
            wanted[("video" if info["mediaType"] == 1 else "audio", info["trackIndex"], info["recordFrame"], summary["duration"], info["mediaPoolItem"].GetMediaId())] += 1
        actual_geometry = Counter(geometry(row) for row in after["items"])
        if actual_geometry != wanted:
            difference = {"missing": list((wanted - actual_geometry).elements()),
                          "unexpected": list((actual_geometry - wanted).elements())}
            (staging / "geometry-diff.json").write_text(json.dumps(difference, indent=2), encoding="utf-8")
            raise RuntimeError("Inserted media, positions, durations or item counts failed readback; see geometry-diff.json")
        existing_keys = {geometry(row) for row in expected["items"]}
        if [row for row in after["items"] if geometry(row) in existing_keys] != expected["items"]:
            raise RuntimeError("Appending the inserts changed existing item state")
        # Resolve 21.1 omits AI audio properties on an inactive timeline. Read
        # both inventories while their timeline is current, rather than
        # interpreting an unavailable getter as lost user state.
        selected, detail = set_current_timeline(project, timeline)
        if not selected:
            raise RuntimeError(f"Could not select original for verification: {detail}")
        original_after = inventory(timeline)
        if original_after != before:
            (staging / "original-diff.json").write_text(json.dumps({"expected": before, "actual": original_after}, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError("The original timeline changed during the operation")
        selected, detail = set_current_timeline(project, revision)
        if not selected:
            raise RuntimeError(f"Could not select verified insertion revision: {detail}")
        result.update(success=True, dry_run=False, insert_frame_absolute=at,
                      shift_frames=shift, inserted_clips=len(built_inserts), tail_items_shifted=shifted,
                      readback={"missing": [], "readable_state_verified": True},
                      verification={"verified": True, "contradiction": False,
                                    "scope": "readable_timeline_state", "render_verified": False},
                      gap_frames_by_track=plan["gap_frames_by_track"],
                      warnings=plan["warnings"],
                      preservation="native_drt_revision", render_verified=False,
                      verified_components=["record_positions", "source_ranges", "readable_item_properties", "links", "markers", "original_unchanged"],
                      unverified_components=["rendered_pixels", "opaque_payloads_after_import", "track_automation"],
                      _changes={"items_added": len(built_inserts), "items_moved": shifted, "items_deleted": 0})
    except Exception as exc:
        result["error"] = {"code": "RIPPLE_REVISION_FAILED", "message": str(exc), "retryable": False}
    finally:
        if not result["success"]:
            restored, detail = set_current_timeline(project, original_active)
            result["original_restored"] = restored
            if not restored:
                result["restore_error"] = detail
    return result
