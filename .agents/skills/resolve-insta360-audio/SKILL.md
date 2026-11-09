---
name: resolve-insta360-audio
description: Extract Insta360 dual-track recordings into separate microphone WAV clips for DaVinci Resolve. Apply when asked to separate X5 camera audio and Mic Air audio, extract both recorded audio streams from INSV/camera MP4 files, or repeat that extraction for a folder. This extracts recorded streams; it does not separate voices or instruments from an already mixed track.
---

# Separate Insta360 audio for Resolve

Deliver one verified WAV per recorded audio stream, paired by original filename.
An explicit request to extract/separate these recordings authorizes creating these
derivatives; proceed without another confirmation. A request only to analyze audio
does not authorize extraction. Read originals only, and write into a new output
folder. Do not render the video, rename originals, use LRV proxies, normalize,
denoise, mix, or change channel counts as part of extraction.

## Run in this checkout

Use the file/folder specified in the current request or session. If the source
cannot be inferred, ask for its path. Do not bake in a previous user's folder.
For a folder request, process all originals recursively. Check that `ffmpeg` and
`ffprobe` are available; do not install dependencies without authorization.

Run from the repository root, with each path passed as its own quoted argument:

```powershell
python scripts/extract_insta360_audio.py "D:\Footage\X5"
```

Optional `--output "D:\Audio\New extraction"` must name a new directory.
The default is a sibling `<source> - Resolve Audio` directory, with a unique
suffix when a prior run exists. `--jobs 1` reduces concurrent reads on slow disks.
The helper searches INSV first; only when none exist does it use camera MP4s.
For a mixed folder requiring both formats, invoke each intended MP4 separately
as well. Skip exports already mixed by Studio; prefer the camera originals.

The helper probes each file, requires exactly two recorded audio streams, then
maps `0:a:0` and `0:a:1` into separate 24-bit PCM WAVs in one source read. It
preserves each stream's native sample rate and channels (X5 recordings commonly
have two stereo streams at 48 kHz). It preserves nested source paths to avoid
same-name collisions. Read `extraction_manifest.json` and the exit status:

- `verified_pairs` must match the requested recording count for full success.
- Failures are recorded individually and produce a nonzero exit status. Do not
  report a partial batch as complete or treat `.partial.wav` files as deliverables.
- Format, duration and full PCM decoding are verified with FFprobe/FFmpeg. This
  supports `WAVE_FORMAT_EXTENSIBLE`, which Python's `wave` reader before 3.12
  rejects even for valid 24-bit WAVs. Do not re-extract merely for that reader error.
- PCM hashes identify identical pairs. Identical tracks may be silence or duplicate
  signals; report the warning rather than claiming distinct microphone recordings.
- Source size/mtime checks are recorded; these are not source content hashes.

There is no need to start Resolve or run visual analysis/transcription to extract
files. Import or change a Resolve project only when the user requests that step;
use `resolve-audio` and `resolve-media-pool` for the live work.

## Microphone identity and timing

`Audio 1` and `Audio 2` are stream ordinals, not camera/microphone identities.
Do not assume stereo left/right correspond to two microphones, or assume the
first stream is always the camera. X5 track metadata may label both `INS.AAC`.
Keep neutral names unless microphone identity is established by listening or
reliable user evidence; distinguish an inference from verified identification.

Import matching WAVs on separate Resolve tracks with the original channel layout.
WAV sample zero corresponds to the first decoded sample of its source stream.
Use `source_start_seconds` plus the video source timing in the manifest when
placing them. If both audio starts are zero, place the pair at the same source
position. Check audible sync/Bluetooth delay; zero container offsets do not prove
acoustic synchronization. Match source trims when using trimmed Studio exports.
Preserve small differences in recorded end times rather than padding/trimming them.

If only one audio stream exists, stop for that recording and report it. Check the
original rather than a mixed Studio export. A single stereo stream is not evidence
of two independently recoverable microphone recordings; this workflow cannot
recover a source that was never recorded separately.

## Hosts without a shell or checkout

This skill is available through `knowledge(action="get",
params={"topic":"resolve-insta360-audio"})`. Knowledge serves instructions;
it does not execute the helper. If the host cannot access source files and run
FFmpeg, state that limitation and give the user these local commands rather than
inventing an MCP extraction action:

```powershell
ffprobe -v error -select_streams a -show_streams -of json "D:\Footage\recording.insv"
# Only after confirming two audio streams; run in a separate output folder:
ffmpeg -n -i "D:\Footage\recording.insv" -map 0:a:0 -c:a pcm_s24le "recording_audio_1.wav"
ffmpeg -n -i "D:\Footage\recording.insv" -map 0:a:1 -c:a pcm_s24le "recording_audio_2.wav"
```

Probe the outputs and check durations/sample rates/channels before reporting
success. Keep the timing and microphone identity caveats above. Finish with a
clickable output path, verified pair/file counts, format, failures/warnings, and
the brief Resolve placement instructions. Do not claim a live Resolve import
unless it was actually performed.
