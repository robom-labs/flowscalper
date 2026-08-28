# SIHO 영상 증거표가 전체 장문 범위와 필수 규칙 필드를 정직하게 보존하는지 검증한다.

from __future__ import annotations

from scripts.export_siho_evidence_matrix import RULE_FIELDS, build_matrix


def test_matrix_tracks_every_long_form_and_keeps_unreviewed_rules_unknown() -> None:
    index = {
        "channel_id": "channel",
        "index_status": "COMPLETE_IDS_PARTIAL_METADATA",
        "videos": [
            {
                "video_id": "long-1",
                "content_kind": "LONG_FORM",
                "title": "전략 영상",
                "metadata_status": "CHANNEL_INDEX_ONLY",
            },
            {
                "video_id": "short-1",
                "content_kind": "SHORTS",
                "title": "요약",
                "metadata_status": "CHANNEL_INDEX_ONLY",
            },
        ],
    }
    transcript_manifest = {
        "status": "NOT_RUN_OR_PARTIAL",
        "records": [
            {
                "video_id": "long-1",
                "public_caption_status": "NOT_RUN",
                "asr_status": "NOT_RUN",
                "timeline_review_status": "NOT_RUN",
                "frame_review_status": "NOT_RUN",
            }
        ],
    }

    matrix = build_matrix(index, transcript_manifest)

    assert "long-1" in matrix
    assert "short-1" not in matrix
    assert "CURRENT_STRATEGY = UNCONFIRMED" in matrix
    assert "MATRIX_STATUS = NOT_RUN_OR_PARTIAL" in matrix
    for label, default in RULE_FIELDS:
        assert f"| {label} | {default} |" in matrix


def test_matrix_only_marks_complete_for_exact_review_scope_with_timestamps() -> None:
    index = {
        "channel_id": "channel",
        "index_status": "COMPLETE_METADATA",
        "selection_scope": {"required_full_review": ["long-1"]},
        "videos": [
            {
                "video_id": "long-1",
                "content_kind": "LONG_FORM",
                "title": "검토 영상",
                "metadata_status": "WATCH_METADATA_HYDRATED",
            }
        ],
    }
    transcripts = {
        "status": "COMPLETE",
        "records": [
            {
                "video_id": "long-1",
                "public_caption_status": "COLLECTED",
                "asr_status": "NOT_REQUIRED_PUBLIC_CAPTION_AVAILABLE",
            }
        ],
    }
    reviews = {
        "status": "REVIEW_COMPLETE",
        "records": [
            {
                "video_id": "long-1",
                "asset_collection_status": "COMPLETE",
                "full_video_review_status": "COMPLETE",
                "timestamp_evidence_status": "COMPLETE",
            }
        ],
    }

    matrix = build_matrix(index, transcripts, reviews)

    assert "MATRIX_STATUS = COMPLETE" in matrix
    assert "| 전체 영상 검토 | COMPLETE |" in matrix
    assert "| timestamp 증거 | COMPLETE |" in matrix
