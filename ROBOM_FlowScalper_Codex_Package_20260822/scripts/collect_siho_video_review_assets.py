# SIHO 장문영상의 전체 timeline·장면 frame 검토용 로컬 자산과 검증 manifest를 만든다.

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_INDEX = Path("evidence/SIHO_VIDEO_INDEX.json")
DEFAULT_TRANSCRIPTS = Path("evidence/SIHO_TRANSCRIPT_MANIFEST.json")
DEFAULT_OUTPUT = Path("evidence/SIHO_FRAME_EVIDENCE_MANIFEST.json")
DEFAULT_CACHE_DIR = Path("data/research/siho")
DEFAULT_OVERVIEW_SECONDS = 10
DEFAULT_SCENE_THRESHOLD = 0.25
FRAME_REVIEW_CATEGORIES = (
    "SCENE_TRANSITIONS",
    "INDICATOR_SETTINGS",
    "ORDER_OR_POSITION_SCREEN",
    "ENTRY_STOP_TARGET_MARKERS",
    "PERFORMANCE_TABLE",
    "SUMMARY_AND_WARNINGS",
)
PTS_TIME_PATTERN = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _write_object_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _records_by_video(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records = manifest.get("records", [])
    if not isinstance(records, list):
        raise ValueError("Transcript manifest records must be a list")
    return {
        str(record["video_id"]): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("video_id"), str)
    }


def _required_video_ids(index: Mapping[str, Any]) -> list[str]:
    selection = index.get("selection_scope", {})
    if not isinstance(selection, dict):
        raise ValueError("SIHO index selection_scope must be an object")
    required = selection.get("required_full_review")
    if not isinstance(required, list) or not required:
        raise ValueError("Hydrated required_full_review scope is missing")
    if not all(isinstance(video_id, str) and video_id for video_id in required):
        raise ValueError("required_full_review must contain non-empty video IDs")
    if len(required) != len(set(required)):
        raise ValueError("required_full_review contains duplicate video IDs")
    return required


def build_review_asset_manifest(
    index: Mapping[str, Any],
    transcript_manifest: Mapping[str, Any],
    *,
    overview_interval_seconds: int = DEFAULT_OVERVIEW_SECONDS,
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD,
) -> dict[str, Any]:
    if overview_interval_seconds <= 0:
        raise ValueError("Overview interval must be positive")
    if not 0 < scene_threshold < 1:
        raise ValueError("Scene threshold must be between zero and one")
    required_ids = _required_video_ids(index)
    videos = index.get("videos", [])
    if not isinstance(videos, list):
        raise ValueError("SIHO index videos must be a list")
    by_id = {
        str(video["video_id"]): video
        for video in videos
        if isinstance(video, dict) and isinstance(video.get("video_id"), str)
    }
    transcripts = _records_by_video(transcript_manifest)
    missing = [video_id for video_id in required_ids if video_id not in by_id]
    if missing:
        raise ValueError(f"Required review videos missing from index: {missing}")
    records: list[dict[str, Any]] = []
    for video_id in required_ids:
        video = by_id[video_id]
        if video.get("content_kind") != "LONG_FORM":
            raise ValueError(f"Required review item is not long form: {video_id}")
        transcript = transcripts.get(video_id)
        if transcript is None:
            raise ValueError(f"Transcript record missing for required video: {video_id}")
        records.append(
            {
                "video_id": video_id,
                "title": video.get("title"),
                "upload_timestamp": video.get("upload_timestamp"),
                "length_seconds": video.get("length_seconds"),
                "required_full_review": True,
                "public_caption_status": transcript.get("public_caption_status", "NOT_RUN"),
                "normalized_timeline_status": transcript.get(
                    "normalized_timeline_status", "NOT_AVAILABLE"
                ),
                "asr_required": transcript.get("asr_required"),
                "asr_status": transcript.get("asr_status", "NOT_RUN"),
                "media_collection_status": "NOT_RUN",
                "media_sha256": None,
                "media_bytes": None,
                "media_retained": False,
                "overview_frame_extraction": {
                    "status": "NOT_RUN",
                    "interval_seconds": overview_interval_seconds,
                    "count": 0,
                    "files": [],
                },
                "scene_frame_extraction": {
                    "status": "NOT_RUN",
                    "threshold": scene_threshold,
                    "count": 0,
                    "files": [],
                },
                "review_categories": {category: "NOT_RUN" for category in FRAME_REVIEW_CATEGORIES},
                "timeline_review_status": "NOT_RUN",
                "frame_review_status": "NOT_RUN",
                "full_video_review_status": "NOT_RUN",
                "timestamp_evidence_status": "NOT_RUN",
                "evidence_grade": "UNKNOWN",
            }
        )
    return {
        "schema_version": 1,
        "collected_ts_utc": _utc_now(),
        "channel_id": index.get("channel_id"),
        "index_collected_ts_utc": index.get("collected_ts_utc"),
        "required_scope_count": len(required_ids),
        "status": "NOT_RUN",
        "counts": {
            "required": len(records),
            "assets_collected": 0,
            "timeline_reviewed": 0,
            "frame_reviewed": 0,
            "full_video_reviewed": 0,
        },
        "collection_policy": {
            "overview_interval_seconds": overview_interval_seconds,
            "scene_threshold": scene_threshold,
            "max_video_height": 720,
            "raw_video_git_tracked": False,
            "raw_audio_git_tracked": False,
            "raw_transcript_git_tracked": False,
            "frame_files_git_tracked": False,
        },
        "records": records,
        "claims_boundary": {
            "asset_collection_is_full_video_review": False,
            "frame_extraction_is_frame_review": False,
            "captions_or_asr_are_rule_proof": False,
            "exact_public_rules_are_verified": False,
        },
    }


def merge_asr_into_transcript_manifest(
    transcript_manifest: dict[str, Any],
    review_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    review_records = review_manifest.get("records", [])
    transcript_records = transcript_manifest.get("records", [])
    if not isinstance(review_records, list) or not isinstance(transcript_records, list):
        raise ValueError("SIHO transcript 또는 review record 목록이 잘못됐습니다.")
    by_video_id = {
        str(record["video_id"]): record
        for record in transcript_records
        if isinstance(record, dict) and isinstance(record.get("video_id"), str)
    }
    for review in review_records:
        if not isinstance(review, Mapping):
            continue
        video_id = review.get("video_id")
        evidence = review.get("asr_evidence")
        if not isinstance(video_id, str) or not isinstance(evidence, Mapping):
            continue
        transcript = by_video_id.get(video_id)
        if transcript is None:
            raise ValueError(f"ASR review에 대응하는 transcript record가 없습니다: {video_id}")
        if evidence.get("status") != "COMPLETE":
            continue
        timeline_path = Path(str(evidence.get("timeline_cache_path", "")))
        expected_sha256 = evidence.get("timeline_sha256")
        if (
            not timeline_path.is_file()
            or not isinstance(expected_sha256, str)
            or _sha256_file(timeline_path) != expected_sha256
        ):
            raise ValueError(f"ASR timeline checksum이 다릅니다: {video_id}")
        transcript["asr_status"] = "COMPLETE"
        transcript["normalized_timeline_status"] = "AVAILABLE"
        transcript["normalized_timeline_tracks"] = 1
        transcript["asr_evidence"] = dict(evidence)
    ready = sum(
        record.get("public_caption_status") == "COLLECTED" or record.get("asr_status") == "COMPLETE"
        for record in transcript_records
        if isinstance(record, Mapping)
    )
    transcript_manifest["counts"] = {
        "long_form": len(transcript_records),
        "transcript_available": ready,
        "not_available": len(transcript_records) - ready,
    }
    transcript_manifest["status"] = (
        "COMPLETE" if transcript_records and ready == len(transcript_records) else "PARTIAL_ASR"
    )
    transcript_manifest["collected_ts_utc"] = _utc_now()
    return transcript_manifest


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - 명시적으로 확인한 연구 도구만 실행한다.
        command,
        check=True,
        capture_output=True,
        text=True,
    )


def _frame_records(paths: Iterable[Path], timestamps: Iterable[float]) -> list[dict[str, Any]]:
    timestamp_list = list(timestamps)
    path_list = sorted(paths)
    if len(path_list) != len(timestamp_list):
        raise ValueError("Frame count and timestamp count differ")
    return [
        {
            "timestamp_seconds": round(timestamp, 3),
            "research_cache_path": path.as_posix(),
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path, timestamp in zip(path_list, timestamp_list, strict=True)
    ]


def _download_video(
    *,
    yt_dlp: str,
    video_id: str,
    target_dir: Path,
    max_height: int,
) -> Path:
    output_template = target_dir / f"{video_id}.%(ext)s"
    _run(
        [
            yt_dlp,
            "--no-playlist",
            "--no-write-comments",
            "--merge-output-format",
            "mp4",
            "-f",
            (f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best"),
            "-o",
            output_template.as_posix(),
            f"https://www.youtube.com/watch?v={video_id}",
        ]
    )
    matches = sorted(target_dir.glob(f"{video_id}.*"))
    if len(matches) != 1:
        raise ValueError(f"Expected one downloaded media file for {video_id}: {matches}")
    return matches[0]


def _normalize_whisper_json(payload: bytes) -> bytes:
    value = json.loads(payload)
    if not isinstance(value, dict) or not isinstance(value.get("segments"), list):
        raise ValueError("Whisper JSON must contain a segments list")
    timeline: list[dict[str, Any]] = []
    previous_start_ms = -1
    for segment in value["segments"]:
        if not isinstance(segment, dict):
            continue
        start = segment.get("start")
        end = segment.get("end")
        text = str(segment.get("text", "")).replace("\n", " ").strip()
        if not isinstance(start, int | float) or not isinstance(end, int | float):
            continue
        start_ms = round(float(start) * 1000)
        end_ms = round(float(end) * 1000)
        if start_ms < previous_start_ms or end_ms < start_ms:
            raise ValueError("Whisper timeline is not chronological")
        if not text:
            continue
        timeline.append(
            {
                "start_ms": start_ms,
                "duration_ms": end_ms - start_ms,
                "text": text,
            }
        )
        previous_start_ms = start_ms
    if not timeline:
        raise ValueError("Whisper timeline contains no text segments")
    return (
        "\n".join(
            json.dumps(segment, ensure_ascii=False, separators=(",", ":")) for segment in timeline
        )
        + "\n"
    ).encode()


def _collect_asr(
    *,
    ffmpeg: str,
    asr_executable: str,
    asr_model: str,
    asr_model_dir: Path,
    asr_device: str,
    asr_threads: int,
    video_id: str,
    video_path: Path,
    cache_dir: Path,
) -> dict[str, Any]:
    if shutil.which(asr_executable) is None:
        return {
            "status": "BLOCKED_TOOL_MISSING",
            "tool": Path(asr_executable).name,
            "model": asr_model,
        }
    with tempfile.TemporaryDirectory(prefix=f"{video_id}-asr-", dir=cache_dir) as raw:
        working = Path(raw)
        audio_path = working / f"{video_id}.wav"
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                video_path.as_posix(),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                audio_path.as_posix(),
            ]
        )
        asr_model_dir.mkdir(parents=True, exist_ok=True)
        _run(
            [
                asr_executable,
                audio_path.as_posix(),
                "--model",
                asr_model,
                "--model_dir",
                asr_model_dir.as_posix(),
                "--device",
                asr_device,
                "--threads",
                str(asr_threads),
                "--language",
                "Korean",
                "--output_format",
                "json",
                "--output_dir",
                working.as_posix(),
            ]
        )
        raw_json = working / f"{video_id}.json"
        if not raw_json.is_file():
            raise ValueError(f"Whisper JSON output missing for {video_id}")
        timeline_payload = _normalize_whisper_json(raw_json.read_bytes())
        timeline_path = cache_dir / "transcripts" / f"{video_id}.asr.timeline.jsonl"
        timeline_path.parent.mkdir(parents=True, exist_ok=True)
        timeline_path.write_bytes(timeline_payload)
        segment_count = sum(1 for line in timeline_payload.splitlines() if line)
        return {
            "status": "COMPLETE",
            "tool": Path(asr_executable).name,
            "model": asr_model,
            "device": asr_device,
            "threads": asr_threads,
            "timeline_sha256": hashlib.sha256(timeline_payload).hexdigest(),
            "timeline_bytes": len(timeline_payload),
            "timeline_segment_count": segment_count,
            "timeline_cache_path": timeline_path.as_posix(),
            "raw_audio_git_tracked": False,
            "raw_asr_json_git_tracked": False,
        }


def _verified_complete_asr_evidence(record: Mapping[str, Any]) -> dict[str, Any] | None:
    evidence = record.get("asr_evidence")
    if not isinstance(evidence, Mapping) or evidence.get("status") != "COMPLETE":
        return None
    timeline_path = Path(str(evidence.get("timeline_cache_path", "")))
    expected_sha256 = evidence.get("timeline_sha256")
    if (
        not timeline_path.is_file()
        or not isinstance(expected_sha256, str)
        or _sha256_file(timeline_path) != expected_sha256
    ):
        return None
    return dict(evidence)


def _extract_frames(
    *,
    ffmpeg: str,
    video_path: Path,
    frame_dir: Path,
    overview_interval_seconds: int,
    scene_threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    overview_dir = frame_dir / "overview"
    scene_dir = frame_dir / "scene"
    overview_dir.mkdir(parents=True, exist_ok=True)
    scene_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            video_path.as_posix(),
            "-vf",
            f"fps=1/{overview_interval_seconds}",
            "-q:v",
            "3",
            (overview_dir / "%06d.jpg").as_posix(),
        ]
    )
    overview_paths = sorted(overview_dir.glob("*.jpg"))
    overview_records = _frame_records(
        overview_paths,
        (index * overview_interval_seconds for index in range(len(overview_paths))),
    )
    scene_filter = f"select='gt(scene,{scene_threshold})'"
    scene_probe = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            video_path.as_posix(),
            "-vf",
            f"{scene_filter},showinfo",
            "-f",
            "null",
            "-",
        ]
    )
    scene_timestamps = [float(value) for value in PTS_TIME_PATTERN.findall(scene_probe.stderr)]
    if scene_timestamps:
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                video_path.as_posix(),
                "-vf",
                f"{scene_filter},format=yuvj420p",
                "-fps_mode",
                "vfr",
                "-q:v",
                "2",
                (scene_dir / "%06d.jpg").as_posix(),
            ]
        )
    scene_paths = sorted(scene_dir.glob("*.jpg"))
    scene_records = _frame_records(scene_paths, scene_timestamps)
    return overview_records, scene_records


def collect_review_assets(
    manifest: dict[str, Any],
    *,
    cache_dir: Path,
    yt_dlp: str,
    ffmpeg: str,
    asr_executable: str,
    asr_model: str,
    asr_model_dir: Path,
    asr_device: str,
    asr_threads: int,
    max_height: int,
    keep_media: bool,
    checkpoint_path: Path | None = None,
    only_video_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    if shutil.which(yt_dlp) is None:
        raise FileNotFoundError(f"yt-dlp executable not found: {yt_dlp}")
    if shutil.which(ffmpeg) is None:
        raise FileNotFoundError(f"ffmpeg executable not found: {ffmpeg}")
    if asr_threads <= 0:
        raise ValueError("ASR thread count must be positive")
    policy = manifest["collection_policy"]
    policy["max_video_height"] = max_height
    overview_interval = int(policy["overview_interval_seconds"])
    scene_threshold = float(policy["scene_threshold"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    for record in manifest["records"]:
        video_id = record["video_id"]
        if only_video_ids is not None and video_id not in only_video_ids:
            continue
        if record.get("asset_collection_status") == "COMPLETE":
            continue
        try:
            with tempfile.TemporaryDirectory(prefix=f"{video_id}-", dir=cache_dir) as temporary:
                temporary_dir = Path(temporary)
                media = _download_video(
                    yt_dlp=yt_dlp,
                    video_id=video_id,
                    target_dir=temporary_dir,
                    max_height=max_height,
                )
                record["media_collection_status"] = "COLLECTED"
                record["media_sha256"] = _sha256_file(media)
                record["media_bytes"] = media.stat().st_size
                if record.get("asr_required"):
                    verified_asr = _verified_complete_asr_evidence(record)
                    record["asr_evidence"] = verified_asr or _collect_asr(
                        ffmpeg=ffmpeg,
                        asr_executable=asr_executable,
                        asr_model=asr_model,
                        asr_model_dir=asr_model_dir,
                        asr_device=asr_device,
                        asr_threads=asr_threads,
                        video_id=video_id,
                        video_path=media,
                        cache_dir=cache_dir,
                    )
                    record["asr_status"] = record["asr_evidence"]["status"]
                frame_dir = cache_dir / "frames" / video_id
                if frame_dir.exists():
                    shutil.rmtree(frame_dir)
                overview, scenes = _extract_frames(
                    ffmpeg=ffmpeg,
                    video_path=media,
                    frame_dir=frame_dir,
                    overview_interval_seconds=overview_interval,
                    scene_threshold=scene_threshold,
                )
                record["overview_frame_extraction"].update(
                    {"status": "COLLECTED", "count": len(overview), "files": overview}
                )
                record["scene_frame_extraction"].update(
                    {"status": "COLLECTED", "count": len(scenes), "files": scenes}
                )
                if keep_media:
                    media_target = cache_dir / "media" / media.name
                    media_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(media, media_target)
                    record["media_retained"] = True
                    record["media_cache_path"] = media_target.as_posix()
                record["asset_collection_status"] = (
                    "COMPLETE"
                    if not record.get("asr_required") or record.get("asr_status") == "COMPLETE"
                    else "FRAMES_COLLECTED_ASR_BLOCKED"
                )
                record.pop("collection_error_type", None)
                record.pop("collection_error", None)
        except Exception as exc:  # noqa: BLE001 - 영상별 실패를 보존하고 다음 영상으로 간다.
            record["asset_collection_status"] = "FAILED"
            record["collection_error_type"] = type(exc).__name__
            record["collection_error"] = str(exc)[:500]
        if checkpoint_path is not None:
            collected = sum(
                item.get("asset_collection_status") == "COMPLETE" for item in manifest["records"]
            )
            manifest["counts"]["assets_collected"] = collected
            manifest["status"] = "ASSET_COLLECTION_IN_PROGRESS"
            manifest["collected_ts_utc"] = _utc_now()
            _write_object_atomic(checkpoint_path, manifest)
    collected = sum(
        record.get("asset_collection_status") == "COMPLETE" for record in manifest["records"]
    )
    manifest["counts"]["assets_collected"] = collected
    manifest["status"] = (
        "ASSETS_COLLECTED_REVIEW_NOT_RUN"
        if collected == len(manifest["records"])
        else "PARTIAL_ASSET_COLLECTION"
    )
    manifest["collected_ts_utc"] = _utc_now()
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--yt-dlp", default="yt-dlp")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--asr-executable", default="whisper")
    parser.add_argument("--asr-model", default="small")
    parser.add_argument("--asr-model-dir", type=Path, default=Path("data/research/siho/models"))
    parser.add_argument("--asr-device", default="cpu")
    parser.add_argument("--asr-threads", type=int, default=4)
    parser.add_argument("--max-height", type=int, default=720)
    parser.add_argument("--keep-media", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--video-id", action="append", default=[])
    parser.add_argument("--overview-interval-seconds", type=int, default=DEFAULT_OVERVIEW_SECONDS)
    parser.add_argument("--scene-threshold", type=float, default=DEFAULT_SCENE_THRESHOLD)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    transcript_manifest = _read_object(args.transcripts)
    if args.resume and args.output.is_file():
        manifest = _read_object(args.output)
    else:
        manifest = build_review_asset_manifest(
            _read_object(args.index),
            transcript_manifest,
            overview_interval_seconds=args.overview_interval_seconds,
            scene_threshold=args.scene_threshold,
        )
    if args.collect:
        manifest = collect_review_assets(
            manifest,
            cache_dir=args.cache_dir,
            yt_dlp=args.yt_dlp,
            ffmpeg=args.ffmpeg,
            asr_executable=args.asr_executable,
            asr_model=args.asr_model,
            asr_model_dir=args.asr_model_dir,
            asr_device=args.asr_device,
            asr_threads=args.asr_threads,
            max_height=args.max_height,
            keep_media=args.keep_media,
            checkpoint_path=args.output,
            only_video_ids=(frozenset(args.video_id) if args.video_id else None),
        )
        transcript_manifest = merge_asr_into_transcript_manifest(
            transcript_manifest,
            manifest,
        )
        _write_object_atomic(args.transcripts, transcript_manifest)
    _write_object_atomic(args.output, manifest)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "status": manifest["status"],
                **manifest["counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
