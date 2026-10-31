---
name: house-style
description: The editorial and finishing preferences this project's work is judged against — cut rhythm, shot selection, delivery conventions, and the corrections that have already been given. Load before assembling, restructuring, or refining any cut so the same note does not have to be given twice.
user-invocable: false
---

# House Style

The craft guides in `docs/guides/` describe editing in general. This file
describes **how this editor wants it done** — the accumulated, specific
corrections that would otherwise have to be repeated every session.

Read it before any edit task. Append to it whenever a correction is given.

## The capture protocol

This file is only worth what gets written into it. When the user corrects an
editorial decision — rejects a cut, changes a shot choice, adjusts a duration,
says "not like that" — do not just fix it. Fix it, then add the rule here.

A useful entry has three parts:

- **The rule**, stated as an instruction, not an observation.
- **Why**, in the user's terms — what it was in service of.
- **The trap**, if there is one: what makes it easy to get wrong.

Write rules that are falsifiable. "Cut on motion" is a rule; "make it feel
dynamic" is not. If a correction is one-off and situational, it does not belong
here — this file is for what generalizes.

When an entry turns out to be wrong or too broad, edit it. A stale rule
confidently followed is worse than no rule.

---

## Pacing and rhythm

<!-- Hold lengths, when to cut early, what "too long" means for this material. -->

_Not yet captured._

## Shot selection

<!-- What earns a place in the cut; what gets dropped even when it's a good shot. -->

Every trailer shot must show a clear subject, action, or distinctive place in the final phone crop. Replace generic road passes, railings, and distant details that lose their meaning when cropped vertically. Preserve meaningful changes of activity or motorcycle; a short travel trailer should include the distinctive bike-riding experience, not only repeated views from one cockpit.

## Cut points

<!-- Cut on motion vs on rest, handles, how much air before and after a beat. -->

_Not yet captured._

## Structure and openings

<!-- How a piece starts, what the first frames have to do, how it lands. -->

_Not yet captured._

## Rejected by default

Things not to add unless explicitly asked. Seeded from the rough-cut deliverable
contract in the `resolve-rough-cut` skill, which exists because this work gets
thrown away:

- Titles, captions, and text cards
- Transitions (an assembly is hard cuts)
- Effects and speed ramps
- Music beds
- Grading on a cut that was asked for as an assembly

## Delivery conventions

<!-- Aspect ratios, timeline naming, versioning, where renders go. -->

_Not yet captured._

## Resolve execution and snag handling

- When analyzed regions have been approved for cuts or track routing, perform
  those edits. Markers are supporting evidence, not a substitute for the
  requested timeline changes. Preserve the editor's explicit cursor boundary
  and follow the existing example clips' destination track.
- For rapid conversational exchanges, refine routing with local level
  thresholds and short boundary handles. Broad loudness regions can carry a
  quieter reply onto the louder speaker's track; preserve those quick returns.
- After an interrupted, delayed, or failed editing call, stop and inspect the
  actual Resolve timeline and representative rendered frames before retrying.
  Clips can appear even when the call has not returned; assuming failure can
  duplicate completed work.
- Use the MCP editing tools for cutting. Do not write or run custom cutting
  scripts as a fallback. If those tools fail, report the snag and the verified
  timeline state to the user before choosing another approach.
- On memory-limited footage, perform Resolve operations sequentially and avoid
  batch seeking or simultaneous analysis/render jobs. Check available RAM and
  decoder errors before expensive work; save and restart Resolve when retained
  preview buffers materially reduce headroom. Use a recoverable lower-resolution
  working timeline when appropriate. A resized capture still decodes the original
  media and does not by itself reduce decoder memory. Stop and report new memory
  allocation failures rather than repeating the failed operation.

---

## Where personal grading taste lives

Colour and look preferences are **not** kept here — this file travels with the
repository. Grading taste lives in the user-level `colorist-assistant` skill and
in persistent memory. Load those for look selection, grade transfer, and the
Resolve API traps around them.

If any entry below would be specific to one person rather than to this project's
work, it belongs in the user-level skill instead, and this file should be
gitignored rather than committed.
