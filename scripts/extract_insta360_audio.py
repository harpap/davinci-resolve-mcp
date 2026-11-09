#!/usr/bin/env python3
"""Extract two recorded Insta360 audio streams into verified WAV pairs.

Requires FFmpeg and FFprobe on PATH. Reads originals only; every run owns a
new output directory. Does not connect to Resolve or identify microphones.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import math
from pathlib import Path
import re
import shutil
import subprocess


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, errors="replace")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Process exited {result.returncode}")
    return result.stdout


def probe(path: Path, ffprobe: str) -> dict:
    return json.loads(run([
        ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ]))


def discover(source: Path) -> list[Path]:
    if source.is_file():
        if source.suffix.lower() not in {".insv", ".mp4"}:
            raise ValueError("Use an original .insv or camera .mp4 file")
        return [source]
    if not source.is_dir():
        raise ValueError(f"Source does not exist: {source}")
    files = sorted(p for p in source.rglob("*") if p.is_file())
    # An export sitting beside INSV originals must not be treated as another take.
    originals = [p for p in files if p.suffix.lower() == ".insv"]
    if not originals:
        originals = [p for p in files if p.suffix.lower() == ".mp4"]
    if not originals:
        raise ValueError("No .insv or .mp4 recordings found (LRV files are excluded)")
    if any(not p.resolve().is_relative_to(source) for p in originals):
        raise ValueError("A source symlink points outside the requested folder")
    return originals


def audio_streams(info: dict) -> list[dict]:
    streams = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    if len(streams) != 2:
        raise ValueError(f"Expected two recorded audio streams; found {len(streams)}")
    for stream in streams:
        duration = float(stream.get("duration", "nan"))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Missing or invalid source audio duration")
        if int(stream.get("sample_rate", 0)) <= 0 or int(stream.get("channels", 0)) <= 0:
            raise ValueError("Missing source sample rate or channel count")
    return streams


def validate_wav(path: Path, source_stream: dict, ffmpeg: str, ffprobe: str) -> dict:
    # Python <3.12 wave.open rejects FFmpeg's valid WAVE_FORMAT_EXTENSIBLE PCM.
    # Probe and decode it with FFmpeg instead of relying on that reader.
    streams = probe(path, ffprobe)["streams"]
    if len(streams) != 1:
        raise ValueError(f"Expected one WAV audio stream: {path}")
    stream = streams[0]
    expected = ("pcm_s24le", int(source_stream["sample_rate"]), source_stream["channels"], 24)
    actual = (stream.get("codec_name"), int(stream.get("sample_rate", 0)),
              stream.get("channels"), stream.get("bits_per_sample"))
    if actual != expected:
        raise ValueError(f"WAV format mismatch: {actual} != {expected}")
    duration = float(stream["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Empty/invalid WAV: {path}")
    # AAC packet rounding/priming can differ from decoded duration by a packet.
    tolerance = max(0.1, 2048 / int(source_stream["sample_rate"]))
    if abs(duration - float(source_stream["duration"])) > tolerance:
        raise ValueError(f"WAV duration does not match recorded audio: {path}")
    digest = run([
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
        "-map", "0:a:0", "-c:a", "pcm_s24le", "-f", "hash", "-hash", "sha256", "-",
    ]).strip()
    if not re.fullmatch(r"SHA256=[0-9a-fA-F]{64}", digest):
        raise ValueError(f"Could not verify decoded PCM: {path}")
    return {"path": str(path), "duration_seconds": duration,
            "sample_rate": actual[1], "channels": actual[2], "bits_per_sample": 24,
            "bytes": path.stat().st_size, "pcm_sha256": digest.split("=", 1)[1]}


def _extract_pair(record: dict, relative: Path, output: Path, ffmpeg: str, ffprobe: str) -> dict:
    source = Path(record["source"])
    streams = record["source_audio_streams"]
    final = [output / f"Audio {i + 1}" / relative.parent / f"{source.stem}_audio_{i + 1}.wav"
             for i in range(2)]
    partial = [p.with_suffix(".partial.wav") for p in final]
    command = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-n", "-i", str(source)]
    for i, path in enumerate(partial):
        path.parent.mkdir(parents=True, exist_ok=True)
        command += ["-map", f"0:a:{i}", "-c:a", "pcm_s24le", "-threads", "1", str(path)]
    record["partial_outputs"] = [str(p) for p in partial]
    run(command)
    audio = [validate_wav(p, s, ffmpeg, ffprobe) for p, s in zip(partial, streams)]
    after = source.stat()
    if (after.st_size, after.st_mtime_ns) != (record["source_bytes"], record["source_mtime_ns"]):
        raise ValueError("Source size or modification time changed during extraction")
    for i, (path, destination, result) in enumerate(zip(partial, final, audio)):
        path.rename(destination)
        result.update(path=str(destination), source_audio_ordinal=i,
                      source_stream_index=streams[i]["index"],
                      source_start_seconds=streams[i].get("start_time"))
    record.pop("partial_outputs")
    record.update(status="verified", audio=audio, source_stat_unchanged=True,
                  decoded_streams_differ=audio[0]["pcm_sha256"] != audio[1]["pcm_sha256"])
    if not record["decoded_streams_differ"]:
        record["warnings"] = ["Both decoded streams are identical; do not claim two distinct microphone signals."]
    return record


def extract_pair(record: dict, relative: Path, output: Path, ffmpeg: str, ffprobe: str) -> dict:
    # Workers own copies; the main thread alone updates/checkpoints the manifest.
    result = dict(record)
    try:
        return _extract_pair(result, relative, output, ffmpeg, ffprobe)
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        return result


def extract(source: Path, output: Path | None = None, jobs: int = 2) -> dict:
    if jobs < 1 or jobs > 4:
        raise ValueError("jobs must be between 1 and 4")
    source = source.expanduser().resolve()
    files = discover(source)
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ValueError("Install FFmpeg and FFprobe and make both available on PATH")
    if output is None:
        output = source.parent / f"{source.stem if source.is_file() else source.name} - Resolve Audio"
        if output.exists():
            output = output.with_name(output.name + " " + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output = output.expanduser().resolve()
    if output == source or (source.is_dir() and (output.is_relative_to(source) or source.is_relative_to(output))):
        raise ValueError("Output must be separate from the source directory tree")
    # Never merge into a prior run or overwrite user files, even on a retry.
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for path in files:
        stat = path.stat()
        record = {"source": str(path), "source_bytes": stat.st_size,
                  "source_mtime_ns": stat.st_mtime_ns, "status": "pending"}
        try:
            info = probe(path, ffprobe)
            record.update(source_audio_streams=audio_streams(info), source_probe=info)
        except Exception as exc:
            record.update(status="failed", error=str(exc))
        records.append(record)
    manifest = {"source": str(source), "output_directory": str(output),
                "processing": "24-bit PCM WAV; native sample rates/channels; no filters, gain or trimming",
                "microphone_identity": "Unverified: Audio 1/2 mean stream order, not microphone names",
                "clips": records}

    def save() -> None:
        manifest["summary"] = {
            "recordings": len(records),
            "verified_pairs": sum(r["status"] == "verified" for r in records),
            "failed_pairs": sum(r["status"] == "failed" for r in records),
            "pending_pairs": sum(r["status"] == "pending" for r in records),
        }
        temporary = output / "extraction_manifest.tmp"
        temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary.replace(output / "extraction_manifest.json")

    save()
    estimate = sum(float(s["duration"]) * int(s["sample_rate"]) * s["channels"] * 3
                   for r in records if r["status"] == "pending" for s in r["source_audio_streams"])
    if shutil.disk_usage(output).free < estimate * 1.1 + 64 * 1024**2:
        for record in records:
            if record["status"] == "pending":
                record.update(status="failed", error="Insufficient free space for PCM outputs")
        save()
        return manifest
    base = source if source.is_dir() else source.parent
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(extract_pair, r, Path(r["source"]).relative_to(base), output,
                               ffmpeg, ffprobe): r for r in records if r["status"] == "pending"}
        for future in as_completed(futures):
            record = futures[future]
            try:
                record.update(future.result())
            except Exception as exc:
                record.update(status="failed", error=str(exc))
            print(f"{record['status'].upper()}: {Path(record['source']).name}", flush=True)
            save()
    save()
    (output / "READ ME.txt").write_text(
        "Import Audio 1 and Audio 2 WAVs into Resolve on separate tracks. Match by original filename.\n"
        "Audio 1/2 preserve source stream order; solo them to identify each microphone.\n"
        "Use recorded source start times in extraction_manifest.json for placement; unknown timings need checking.\n"
        "Check audible sync/Bluetooth delay against the video. Apply matching source trims to trimmed Studio exports.\n"
        "Native channel counts/sample rates and 24-bit PCM are preserved; no gain, denoise or normalization.\n"
        "Only verified pairs have final .wav names. Failed .partial.wav outputs are not verified deliverables.\n"
        "Source files were opened only for reading; source stat comparisons are not full content hashes.\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Original .insv/.mp4 or a folder to search recursively")
    parser.add_argument("--output", type=Path, help="New output directory (default: sibling Resolve Audio folder)")
    parser.add_argument("--jobs", type=int, default=2, help="Concurrent recordings, 1-4 (default: 2)")
    args = parser.parse_args()
    try:
        manifest = extract(args.source, args.output, args.jobs)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Extraction failed: {exc}\n")
    print(json.dumps({"output_directory": manifest["output_directory"], **manifest["summary"]}))
    return 1 if manifest["summary"]["failed_pairs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
