---
name: resolve-edit
description: Editing, cutting, trimming, pacing, and timeline restructuring in the DaVinci Resolve MCP. Apply when duplicating/moving clips, copying ranges, building variants, tightening or restructuring a cut, editing selects, or generating an editorial changelist/turnover — live in a running Resolve OR offline against .drt timeline files. Routes to the live edit tools, the offline editorial tools, and the project's editorial craft guidance.
---

# Resolve Timeline Edit
Bridges editorial *craft* to this repo's *tools*. Open the right manual; don't
re-derive it here.

- **Craft / story** — `docs/guides/editorial-decision-guide.md`. The global
  `editor` / `assistant-editor` skills add editorial philosophy and cutting-room
  practice; use them for *why to cut*, not tool mechanics.
- **Live tool mechanics** — `docs/kernels/timeline-edit-kernel.md` (the `timeline`
  edit-kernel boundary: duplicate/copy/move, range ops, item-state copy).
- **Offline timeline / changelist** — `resolve-advanced/README.md` → `drt`
  (timeline file authoring) and `editorial` (interchange + turnover).

## Two servers

| Job | Server | Tools |
|---|---|---|
| Restructure a **running** timeline | `davinci-resolve` (Python, live) | `timeline` (edit kernel), `timeline_item`, `edit_engine`, `timeline_markers` |
| Author/diff a `.drt` **file**, or parse/compare editorial interchange with **no Resolve open** | `davinci-resolve-advanced` (Node) | `drt`, `editorial` |

## What this build cannot do (check before you offer it)

The scripting API changes per **patch** release, so "Resolve 21" is not a usable
label. Read `resolve_control get_version` → `build.unavailable_on_this_build`
before offering a gated surface; `check_version_support` asks about one named
symbol. Gated in *this* domain:

| Surface | Needs | If absent |
|---|---|---|
| `Timeline.GetSelectedClips` | 21.0.4 | No selection readback. Identify clips by track/index or id instead — this repo's selection helper duck-probes three names, so it degrades to "no selection" rather than erroring |
| `TimelineItem.SetName` | 20.2 | Clip names are read-only from a script |
| `TimelineItem.SetSpeed` / `GetSpeed` | 21.1 | Native constant speed/readback unavailable; `SetProperty('Speed')` is not a substitute |

An empty `unavailable_on_this_build` means **nothing recorded is missing**, not
that everything exists — most of the API has never been version-bisected. A
symbol with no gate returns `unknown`, which means probe it. Probe with
`name in dir(obj)`, never bare `hasattr`: on a Resolve object `hasattr` returns
`True` for every name, real or invented, so it can only say yes.

Distinguish *gated* from *absent*. Native constant speed is available on 21.1
through `timeline_item set_speed`. Native speed-curve setters remain absent;
`timeline set_speed_ramp` uses a surgical native DRT revision instead. Never
offer `set_retime` for clip speed: it changes interpolation quality only.

## Live edit-kernel essentials

- Duplicate/relocate: `duplicate_clips` (modes `same_time`/`offset`/
  `at_playhead`/`track_above`/`after_source`/`next_gap`), `copy_clips` (alias),
  `move_clips` (duplicate-then-delete). `include_linked=True` carries linked audio.
- Ranges: `copy_range`, `duplicate_range`, `overwrite_range`, `lift_range`.
  **No public razor/split** — partial overlaps in `lift_range` are blocked unless
  `allow_partial_item_delete=True` (whole-item delete, not a trim).
- Item state copy: `copy_properties` (transform/crop/composite/audio/retime/
  markers/flags/grades/takes/keyframes …); scope with a group list.
- **Reading the user's selection is best-effort and version-dependent.** The
  selection helper duck-probes three method names; the documented one,
  `Timeline.GetSelectedClips`, is **21.0.4+** (issue #131), so on older builds
  selection resolves by luck or not at all. Never build a destructive operation
  on "the selected clips" without reading back what you actually got — an empty
  or partial selection is indistinguishable from a small one.
- **Speed and interpolation quality are different controls.** On 21.1 use
  `timeline_item set_speed` with `options={Percentage:300}` for constant 3x.
  `set_retime` changes *quality* only (`RetimeProcess`, `MotionEstimation`).
  The legacy `SetProperty('Speed'|'PlaybackSpeed'|'RetimeSpeed'|'ClipSpeed')`
  keys remain unsupported; their failure does not establish that native
  `SetSpeed` is absent. See `docs/reference/resolve211-speed-fades.md`.
- **Smooth speed ramps:** `timeline set_speed_ramp` plans by default. Supply
  `timeline_item_id` or a verified single selection, and `speed_points` with
  increasing frames spanning the current clip's 0 through duration-1; `speed`
  is a multiplier, so 1 means 100%, 3 means 300%. Set `dry_run=false` to create
  and select a recoverable native DRT revision. Default `ripple=true` preserves
  the trimmed source window to frame precision, changes duration, remaps point
  positions proportionally, and shifts later tracks and timeline markers.
  Explicit `ripple=false` preserves output duration instead. Include aligned
  unlinked audio/video via `additional_item_ids`; existing links are retained.
  Unrelated overlapping items and locked moving tracks are refused.
  Speed changes use smoothstep easing sampled once
  per output frame, encoded as linear source-position keyframes; these are
  not native Bezier speed handles. Ordinary video and linked video/audio groups
  sharing record start/duration are supported. Linked audio follows by default
  on 21.1+; with `ripple=false`, `linked_audio="preserve"` keeps its current timing and
  reports a sync warning. Each member retains its source offset, track and
  links; every source extent and re-exported map is checked. WAV extents are
  read with FFprobe when Resolve has no frame count. Nested sequences remain
  unsupported. Inspect rendered frames separately. Live-tested on Studio
  21.1.0.17, including an independent offset WAV on A2: six rendered video
  samples and all 19 rendered audio beeps matched the planned timing (under
  6 ms audio error). The `--linked --ripple` live test also verified duration
  96 to 40 frames, eight source beeps within 4 ms, later track/marker shifts
  and identical following-shot pixels. Older audio engines may ignore the map
  and are refused.
- **Routing to the UI is where issue #132 actually went wrong — and it was not
  a tool lie.** Having been told to hand off, the assistant improvised the
  handoff: it sent the user hunting for a dropdown "in the lower left of the
  clip" and never mentioned the keyframe tray. That direction came from nowhere
  in this repo. **This repo verifies API behavior, not UI geography, and has no
  mechanism to version-guard a UI claim** — Resolve's controls move between
  builds, pages and layouts, and every ledger entry here is stamped with the
  build it was measured on precisely because unstamped claims rot. So:
  **never improvise UI geography.** Say *what* the user needs to do ("set the
  clip's speed / build the speed ramp in Resolve's own retime controls") and say
  plainly that you cannot see their screen. Do **not** invent where a control
  sits, what it looks like, which corner of a clip to click, or a menu path or
  keyboard shortcut recalled from general knowledge — an invented location costs
  the user more than "I don't know where it is on your build" ever does, and it
  reads as authoritative because everything else you told them was measured.
  **The line is improvised vs. written down here.** A UI pointer that already
  exists in these skills (e.g. the playback-frame-rate path in
  `resolve-rough-cut`) was authored and reviewed deliberately — use it verbatim.
  If no pointer exists, that is not an invitation to supply one: name the
  operation and point at Blackmagic's manual for their build. The same rule
  covers every other "do it in the UI" handoff in these skills.
- `edit_engine` drives higher-level selects/tighten/swap flows
  (plan → confirm → execute); tighten variants can carry audio via `keep_ranges`
  mirror / `include_audio`. For a full dead-air pass over **one long single-take
  recording** — transcribe → plan → review → execute, with the source/record
  coordinate trap spelled out — use the `resolve-tighten-recording` skill.

## Show the gaps before cutting them (`edit_engine`)

- **`plan_dead_space_markers(timeline_name?, tightness?)`** — the review gate.
  Same calibrated detection as `plan_silence_ripple`, but it proposes **markers**
  instead of assembling a variant, so a human sees every gap before agreeing to
  lose it. **Use this whenever the ask is "mark the dead space so I can review
  and approve"** — that is a request for this verb, not for a tighten. Red =
  confident, Yellow = the gate only just cleared its separation floor. Nothing
  is written; pair the marker specs with `timeline_markers`.
- **`tightness`** on the dead-space planner: `generous` (default) | `balanced` |
  `tight`. Default is deliberately the loosest. A first assembly is supposed to
  be long — trimming a generous cut is fast and visible, while recovering
  material the machine already discarded is slow and invisible. Move to `tight`
  only when the editor asks for it.
- Guard bands are floored regardless of tightness: a removed gap always leaves
  frames before it (the outgoing word's decay) and after it (the incoming word's
  attack). `tight` removes *more gaps*, never *more speech*.

## Word-level editing and spoken search (`edit_engine`)

Silence-driven tightening cannot remove an audible "um" mid-phrase or a
stammered restart. These work on the words instead and are complementary to
`plan_silence_ripple`, not a replacement.

- **`plan_transcript_tighten(clip_ref)`** — fillers, false starts and long
  pauses, at word boundaries. Emits the same `keep_ranges` shape the variant
  assembler already takes, and **every removal states its reason**, so the plan
  can be argued with rather than only accepted. False starts are a flagged
  heuristic: tight repeats only, so "no, no, no" as emphasis survives.
- **`search_spoken_content(query, mode)`** — phrase / all_words / regex across
  every transcribed clip, returning timestamped hits with context plus
  `selects` (in/out with handles). A *lexical* axis; `find_similar` is the
  semantic-visual one. Use both — a shoot should be searchable either way.
  Deterministic order (clip name, then time), so an identical search gives an
  identical selects list.
- Needs `transcript_words`: run transcription, then strata backfill. Both
  actions say so rather than returning an empty plan.

## Offline editorial (`editorial` actions)

- `parse_interchange` — EDL / OTIO / XMEML (AAF = an honest refuse, not a fake).
- `turnover_changelist` — moved / retimed / trimmed / replaced / new / gone between
  two cuts PLUS the junction diff (transitions added / dropped / changed, fade
  in/out, span deltas — carrier lines and BL fade legs never read as sources),
  with timing silent-lie guards (it flags what it cannot verify).
- `conform_manifest`, `marker_roundtrip`.

Use these to answer "what changed between v3 and v4" or to hand a conform an
accurate change list **without** opening either timeline in Resolve. For carrying
a *conform* across a re-edit, see the `resolve-conform` skill.

## Source-media safety (AGENTS.md)

Edit operations reference existing Media Pool items — they never transcode,
render, proxy, or create derivatives of source media. Keep it that way. Treat
generated probe reports as local scratch artifacts, not committed files.
