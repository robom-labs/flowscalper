# SIHO 전체 영상 검토 자산 manifest가 미검토 상태와 범위를 정직하게 보존하는지 검증한다.

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.collect_siho_video_review_assets import (
    _extract_frames,
    _normalize_whisper_json,
    _verified_complete_asr_evidence,
    build_review_asset_manifest,
    collect_review_assets,
    merge_asr_into_transcript_manifest,
)


def _hydrated_index() -> dict[str, object]:
    return {
        "channel_id": "channel",
        "collected_ts_utc": "2026-08-28T00:00:00Z",
        "selection_scope": {"required_full_review": ["long-1", "long-2"]},
        "videos": [
            {
                "video_id": "long-1",
                "content_kind": "LONG_FORM",
                "title": "첫 영상",
                "length_seconds": 120,
            },
            {
                "video_id": "long-2",
                "content_kind": "LONG_FORM",
                "title": "둘째 영상",
                "length_seconds": 240,
            },
            {
                "video_id": "short-1",
                "content_kind": "SHORTS",
                "title": "요약",
            },
        ],
    }


def _transcripts() -> dict[str, object]:
    return {
        "records": [
            {
                "video_id": "long-1",
                "public_caption_status": "COLLECTED",
                "normalized_timeline_status": "AVAILABLE",
                "asr_required": False,
                "asr_status": "NOT_REQUIRED_PUBLIC_CAPTION_AVAILABLE",
            },
            {
                "video_id": "long-2",
                "public_caption_status": "UNAVAILABLE",
                "normalized_timeline_status": "NOT_AVAILABLE",
                "asr_required": True,
                "asr_status": "NOT_RUN_REQUIRED",
            },
        ]
    }


def test_review_manifest_includes_exact_required_long_form_scope() -> None:
    manifest = build_review_asset_manifest(_hydrated_index(), _transcripts())

    assert manifest["status"] == "NOT_RUN"
    assert manifest["required_scope_count"] == 2
    assert [record["video_id"] for record in manifest["records"]] == [
        "long-1",
        "long-2",
    ]
    assert manifest["records"][1]["asr_required"] is True
    assert manifest["records"][1]["full_video_review_status"] == "NOT_RUN"
    assert all(
        status == "NOT_RUN" for status in manifest["records"][0]["review_categories"].values()
    )
    assert manifest["claims_boundary"]["asset_collection_is_full_video_review"] is False


def test_review_manifest_rejects_unhydrated_or_duplicate_scope() -> None:
    index = _hydrated_index()
    index["selection_scope"] = {"required_full_review": "PENDING_HYDRATED_SCOPE"}
    with pytest.raises(ValueError, match="Hydrated required_full_review"):
        build_review_asset_manifest(index, _transcripts())

    index["selection_scope"] = {"required_full_review": ["long-1", "long-1"]}
    with pytest.raises(ValueError, match="duplicate"):
        build_review_asset_manifest(index, _transcripts())


def test_whisper_json_normalization_is_deterministic_and_chronological() -> None:
    payload = json.dumps(
        {
            "segments": [
                {"start": 0.0, "end": 1.25, "text": " 첫 문장 "},
                {"start": 1.25, "end": 2.0, "text": "둘째 문장"},
            ]
        }
    ).encode()

    assert _normalize_whisper_json(payload) == (
        b'{"start_ms":0,"duration_ms":1250,"text":"\xec\xb2\xab \xeb\xac\xb8\xec\x9e\xa5"}\n'
        b'{"start_ms":1250,"duration_ms":750,'
        b'"text":"\xeb\x91\x98\xec\xa7\xb8 \xeb\xac\xb8\xec\x9e\xa5"}\n'
    )


def test_review_collection_rejects_unbounded_asr_threads(tmp_path: Path) -> None:
    manifest = build_review_asset_manifest(_hydrated_index(), _transcripts())

    with pytest.raises(ValueError, match="ASR thread count"):
        collect_review_assets(
            manifest,
            cache_dir=tmp_path / "cache",
            yt_dlp=sys.executable,
            ffmpeg=sys.executable,
            asr_executable=sys.executable,
            asr_model="small",
            asr_model_dir=tmp_path / "models",
            asr_device="cpu",
            asr_threads=0,
            max_height=720,
            keep_media=False,
        )


def test_completed_asr_updates_transcript_manifest_with_verified_timeline(tmp_path: Path) -> None:
    transcripts = _transcripts()
    review = build_review_asset_manifest(_hydrated_index(), transcripts)
    timeline = tmp_path / "long-2.timeline.jsonl"
    timeline.write_text('{"start_ms":0,"duration_ms":1000,"text":"검증"}\n', encoding="utf-8")
    review["records"][1]["asr_evidence"] = {
        "status": "COMPLETE",
        "timeline_cache_path": timeline.as_posix(),
        "timeline_sha256": hashlib.sha256(timeline.read_bytes()).hexdigest(),
        "timeline_segment_count": 1,
    }

    merged = merge_asr_into_transcript_manifest(transcripts, review)

    assert merged["status"] == "COMPLETE"
    assert merged["counts"] == {
        "long_form": 2,
        "transcript_available": 2,
        "not_available": 0,
    }
    assert merged["records"][1]["asr_status"] == "COMPLETE"
    assert merged["records"][1]["normalized_timeline_status"] == "AVAILABLE"


def test_completed_asr_is_reused_only_when_timeline_checksum_matches(tmp_path: Path) -> None:
    timeline = tmp_path / "timeline.jsonl"
    timeline.write_text('{"start_ms":0,"duration_ms":1000,"text":"검증"}\n', encoding="utf-8")
    record = {
        "asr_evidence": {
            "status": "COMPLETE",
            "timeline_cache_path": timeline.as_posix(),
            "timeline_sha256": hashlib.sha256(timeline.read_bytes()).hexdigest(),
        }
    }

    assert _verified_complete_asr_evidence(record) == record["asr_evidence"]
    record["asr_evidence"]["timeline_sha256"] = "0" * 64
    assert _verified_complete_asr_evidence(record) is None


def test_frame_extraction_accepts_video_with_no_scene_threshold_hits(
    monkeypatch,
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "fps=1/10" in command:
            overview_dir = tmp_path / "frames" / "overview"
            (overview_dir / "000001.jpg").write_bytes(b"overview")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("scripts.collect_siho_video_review_assets._run", fake_run)

    overview, scenes = _extract_frames(
        ffmpeg="ffmpeg",
        video_path=tmp_path / "video.mp4",
        frame_dir=tmp_path / "frames",
        overview_interval_seconds=10,
        scene_threshold=0.25,
    )

    assert len(overview) == 1
    assert scenes == []
    assert len(commands) == 2
    assert commands[1][-3:] == ["-f", "null", "-"]
