# SIHO 장문 영상별 공개규칙 증거등급과 미검증 항목을 빠짐없이 표로 만든다.

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DEFAULT_INDEX = Path("evidence/SIHO_VIDEO_INDEX.json")
DEFAULT_TRANSCRIPTS = Path("evidence/SIHO_TRANSCRIPT_MANIFEST.json")
DEFAULT_REVIEW_ASSETS = Path("evidence/SIHO_FRAME_EVIDENCE_MANIFEST.json")
DEFAULT_OUTPUT = Path("docs/research/SIHO_VIDEO_EVIDENCE_MATRIX_KO.md")

RULE_FIELDS = (
    ("시장", "UNKNOWN"),
    ("종목", "UNKNOWN"),
    ("LONG/SHORT", "UNKNOWN"),
    ("추세 timeframe", "UNKNOWN"),
    ("setup timeframe", "UNKNOWN"),
    ("trigger timeframe", "UNKNOWN"),
    ("indicator", "UNKNOWN"),
    ("indicator parameter", "UNKNOWN"),
    ("매수 조건", "UNKNOWN"),
    ("매도/숏 조건", "UNKNOWN"),
    ("초기 손절", "UNKNOWN"),
    ("익절", "UNKNOWN"),
    ("부분익절", "UNKNOWN"),
    ("trailing activation", "UNKNOWN"),
    ("trailing distance/rate", "UNKNOWN"),
    ("추세 소멸 종료", "UNKNOWN"),
    ("최대보유", "UNKNOWN"),
    ("재진입", "UNKNOWN"),
    ("cooldown", "UNKNOWN"),
    ("position sizing", "UNKNOWN"),
    ("leverage 언급", "UNKNOWN"),
    ("수수료 고려 여부", "UNKNOWN"),
    ("변경 전 전략", "UNKNOWN"),
    ("변경 후 전략", "UNKNOWN"),
    ("핵심 timestamp", "NOT_RUN"),
    ("증거등급", "UNKNOWN"),
    ("모호성", "TIMELINE_AND_FRAME_REVIEW_NOT_RUN"),
    ("채택 여부", "NO"),
)


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _cell(value: object) -> str:
    if value is None or value == "":
        return "UNKNOWN"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "없음"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _transcript_records(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records = manifest.get("records", [])
    return {
        str(record["video_id"]): record
        for record in records
        if isinstance(records, list)
        if isinstance(record, dict) and isinstance(record.get("video_id"), str)
    }


def build_matrix(
    index: Mapping[str, Any],
    transcript_manifest: Mapping[str, Any],
    review_asset_manifest: Mapping[str, Any] | None = None,
) -> str:
    videos = index.get("videos", [])
    long_form = [
        item
        for item in videos
        if isinstance(videos, list)
        if isinstance(item, dict) and item.get("content_kind") == "LONG_FORM"
    ]
    transcripts = _transcript_records(transcript_manifest)
    reviews = _transcript_records(review_asset_manifest or {})
    required_scope = index.get("selection_scope", {}).get("required_full_review", [])
    required_scope_ids = required_scope if isinstance(required_scope, list) else []
    reviewed_ids = {
        video_id
        for video_id, review in reviews.items()
        if review.get("full_video_review_status") == "COMPLETE"
        and review.get("timestamp_evidence_status") == "COMPLETE"
    }
    matrix_status = (
        "COMPLETE"
        if required_scope_ids
        and set(required_scope_ids) == reviewed_ids.intersection(required_scope_ids)
        else "NOT_RUN_OR_PARTIAL"
    )
    lines = [
        "# SIHO 공개 장문영상 전략 증거표",
        "",
        f"`MATRIX_STATUS = {matrix_status}`",
        "",
        "이 문서는 영상 제목이나 마케팅 성과를 전략 증거로 사용하지 않는다. "
        "자막·ASR timeline과 frame을 처음부터 끝까지 검토하기 전에는 규칙을 "
        "`UNKNOWN`으로 유지한다. 현재 공개전략은 별도 문서에서 "
        "`CURRENT_STRATEGY = UNCONFIRMED`로 관리한다.",
        "",
        "## 범위와 현재 상태",
        "",
        f"- 공식 channel ID는 `{_cell(index.get('channel_id'))}`다.",
        f"- 공개 장문영상은 `{len(long_form)}`개다.",
        f"- index 상태는 `{_cell(index.get('index_status'))}`다.",
        f"- transcript manifest 상태는 `{_cell(transcript_manifest.get('status'))}`다.",
        f"- frame asset manifest 상태는 `{_cell((review_asset_manifest or {}).get('status'))}`다.",
        f"- 전체 검토 필수 범위는 `{len(required_scope_ids)}`개다.",
        "- timestamp 증거까지 완료된 범위는 "
        f"`{len(reviewed_ids.intersection(required_scope_ids))}`개다.",
        "- Shorts는 별도 색인하며 수치 규칙의 단독 근거로 사용하지 않는다.",
        "- 원문 transcript·대량 frame·원본 영상은 Git에 넣지 않는다.",
        "",
        "## 영상별 증거",
        "",
    ]
    for number, item in enumerate(long_form, start=1):
        video_id = str(item.get("video_id"))
        transcript = transcripts.get(video_id, {})
        review = reviews.get(video_id, {})
        hydrated = item.get("metadata_status") == "WATCH_METADATA_HYDRATED"
        keyword_hits = item.get("strategy_keyword_hits", []) if hydrated else []
        if keyword_hits:
            relevance = "KEYWORD_HIT_ONLY_NOT_RULE_PROOF"
        elif hydrated:
            relevance = "PENDING_TIMELINE_REVIEW"
        else:
            relevance = "NOT_RUN"
        metadata_fields = (
            ("순번", f"{number:02d}"),
            ("video_id", video_id),
            ("제목", item.get("title")),
            ("업로드 시각", item.get("upload_timestamp")),
            ("길이", item.get("length_seconds")),
            ("설명란 checksum", item.get("description_sha256")),
            ("자막 종류", transcript.get("caption_kinds", [])),
            ("공개 자막 상태", transcript.get("public_caption_status", "NOT_RUN")),
            ("ASR 상태", transcript.get("asr_status", "NOT_RUN")),
            ("검토 자산 수집", review.get("asset_collection_status", "NOT_RUN")),
            (
                "overview frame 수",
                review.get("overview_frame_extraction", {}).get("count", 0),
            ),
            (
                "scene frame 수",
                review.get("scene_frame_extraction", {}).get("count", 0),
            ),
            ("timeline 검토", review.get("timeline_review_status", "NOT_RUN")),
            ("frame 검토", review.get("frame_review_status", "NOT_RUN")),
            ("전체 영상 검토", review.get("full_video_review_status", "NOT_RUN")),
            ("timestamp 증거", review.get("timestamp_evidence_status", "NOT_RUN")),
            ("전략 관련 여부", relevance),
            ("검색어 적중", keyword_hits),
        )
        lines.extend(
            (
                f"### {number:02d}. {_cell(item.get('title'))}",
                "",
                "| 항목 | 값 |",
                "|---|---|",
            )
        )
        for label, value in (*metadata_fields, *RULE_FIELDS):
            lines.append(f"| {label} | {_cell(value)} |")
        lines.extend(("", "---", ""))
    return "\n".join(lines).rstrip() + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    parser.add_argument("--review-assets", type=Path, default=DEFAULT_REVIEW_ASSETS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    review_assets = _read_object(args.review_assets) if args.review_assets.exists() else {}
    output = build_matrix(
        _read_object(args.index),
        _read_object(args.transcripts),
        review_assets,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")
    print(json.dumps({"output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
