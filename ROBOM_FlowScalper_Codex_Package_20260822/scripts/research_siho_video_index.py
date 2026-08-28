# SIHO 공개 YouTube 채널의 영상·Shorts 목록과 재현 가능한 메타데이터 증거를 수집한다.

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.request
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_CHANNEL_URL = "https://www.youtube.com/@siholab"
DEFAULT_CHANNEL_ID = "UC7Z6zXw5q1vou0DgPZ80GBA"
DEFAULT_REQUESTED_VIDEO_ID = "1mJDNm4Yko4"
DEFAULT_OUTPUT = Path("evidence/SIHO_VIDEO_INDEX.json")
DEFAULT_DESCRIPTION_OUTPUT = Path("evidence/SIHO_DESCRIPTION_ARCHIVE_MANIFEST.json")
DEFAULT_TRANSCRIPT_OUTPUT = Path("evidence/SIHO_TRANSCRIPT_MANIFEST.json")
DEFAULT_DESCRIPTION_CACHE_DIR = Path("data/research/siho/descriptions")
DEFAULT_TRANSCRIPT_CACHE_DIR = Path("data/research/siho/transcripts")
DEFAULT_TIMEOUT_SECONDS = 30.0
STRATEGY_KEYWORDS = (
    "자동매매",
    "매수",
    "매도",
    "진입",
    "청산",
    "추세",
    "단타",
    "스캘핑",
    "돌파",
    "눌림",
    "손절",
    "익절",
    "트레일링",
    "trail",
    "stop",
    "전략",
    "indicator",
    "tradingview",
    "binance",
    "bybit",
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
YOUTUBE_VIDEO_ID_PATTERN = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^\s#]*&)?v=|shorts/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fetch(
    url: str,
    *,
    timeout_seconds: float,
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> bytes:
    request_headers = {
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "User-Agent": USER_AGENT,
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=body, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        return response.read()


def _json_after_marker(source: str, markers: Iterable[str]) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for marker in markers:
        marker_index = source.find(marker)
        if marker_index < 0:
            continue
        object_index = source.find("{", marker_index)
        if object_index < 0:
            continue
        value, _ = decoder.raw_decode(source[object_index:])
        if isinstance(value, dict):
            return value
    raise ValueError(f"JSON marker not found: {tuple(markers)}")


def _ytcfg(source: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    merged: dict[str, Any] = {}
    cursor = 0
    while True:
        marker_index = source.find("ytcfg.set(", cursor)
        if marker_index < 0:
            break
        object_index = source.find("{", marker_index)
        if object_index < 0:
            break
        try:
            value, consumed = decoder.raw_decode(source[object_index:])
        except json.JSONDecodeError:
            cursor = object_index + 1
            continue
        if isinstance(value, dict):
            merged.update(value)
        cursor = object_index + consumed
    return merged


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _first_continuation(value: Any) -> str | None:
    for node in _walk(value):
        command = (
            node.get("continuationItemRenderer", {})
            .get("continuationEndpoint", {})
            .get("continuationCommand", {})
        )
        token = command.get("token")
        if isinstance(token, str) and token:
            return token
    return None


def _content_strings(value: Any) -> list[str]:
    values: list[str] = []
    for node in _walk(value):
        content = node.get("content")
        if isinstance(content, str):
            values.append(content)
    return values


def _duration_seconds(duration_text: str | None) -> int | None:
    if not duration_text:
        return None
    parts = duration_text.split(":")
    if not all(part.isdigit() for part in parts) or not 2 <= len(parts) <= 3:
        return None
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _video_tab_items(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for node in _walk(value):
        lockup = node.get("lockupViewModel")
        if not isinstance(lockup, dict):
            continue
        video_id = lockup.get("contentId")
        if lockup.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO" or not isinstance(
            video_id, str
        ):
            continue
        metadata = lockup.get("metadata", {}).get("lockupMetadataViewModel", {})
        title = metadata.get("title", {}).get("content")
        metadata_rows = (
            metadata.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
        )
        metadata_bits: list[str] = []
        for row in metadata_rows:
            for part in row.get("metadataParts", []):
                metadata_bits.extend(_content_strings(part.get("text", {})))
        duration_labels: list[str] = []
        for nested in _walk(lockup.get("contentImage", {})):
            badge = nested.get("thumbnailBadgeViewModel")
            if isinstance(badge, dict) and isinstance(badge.get("text"), str):
                duration_labels.append(badge["text"])
        duration_text = duration_labels[0] if duration_labels else None
        items.append(
            {
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "source_tab": "videos",
                "content_kind": "LONG_FORM",
                "title": title,
                "duration_text": duration_text,
                "length_seconds": _duration_seconds(duration_text),
                "views_text": metadata_bits[0] if metadata_bits else None,
                "published_text": metadata_bits[1] if len(metadata_bits) > 1 else None,
                "metadata_status": "CHANNEL_INDEX_ONLY",
            }
        )
    return _unique_items(items)


def _shorts_tab_items(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for node in _walk(value):
        lockup = node.get("shortsLockupViewModel")
        if not isinstance(lockup, dict):
            continue
        endpoint = lockup.get("onTap", {}).get("innertubeCommand", {}).get("reelWatchEndpoint", {})
        video_id = endpoint.get("videoId")
        if not isinstance(video_id, str):
            continue
        overlay = lockup.get("overlayMetadata", {})
        items.append(
            {
                "video_id": video_id,
                "url": f"https://www.youtube.com/shorts/{video_id}",
                "source_tab": "shorts",
                "content_kind": "SHORTS",
                "title": overlay.get("primaryText", {}).get("content"),
                "duration_text": None,
                "length_seconds": None,
                "views_text": overlay.get("secondaryText", {}).get("content"),
                "published_text": None,
                "metadata_status": "CHANNEL_INDEX_ONLY",
            }
        )
    return _unique_items(items)


def _unique_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        video_id = item.get("video_id")
        if isinstance(video_id, str) and video_id not in unique:
            unique[video_id] = item
    return list(unique.values())


def _continuation_page(
    *,
    api_key: str,
    client_version: str,
    continuation: str,
    timeout_seconds: float,
) -> tuple[dict[str, Any], bytes]:
    body = json.dumps(
        {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": client_version,
                    "gl": "KR",
                    "hl": "ko",
                }
            },
            "continuation": continuation,
        },
        separators=(",", ":"),
    ).encode()
    payload = _fetch(
        f"https://www.youtube.com/youtubei/v1/browse?key={api_key}",
        timeout_seconds=timeout_seconds,
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-YouTube-Client-Name": "1",
            "X-YouTube-Client-Version": client_version,
        },
    )
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Unexpected YouTube continuation response")
    return value, payload


def _collect_tab(
    channel_url: str,
    tab: str,
    *,
    timeout_seconds: float,
    maximum_pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    url = f"{channel_url.rstrip('/')}/{tab}"
    payload = _fetch(url, timeout_seconds=timeout_seconds)
    source = payload.decode("utf-8", "replace")
    initial_data = _json_after_marker(
        source,
        ("var ytInitialData = ", 'window["ytInitialData"] = '),
    )
    config = _ytcfg(source)
    api_key = config.get("INNERTUBE_API_KEY")
    client_version = config.get("INNERTUBE_CLIENT_VERSION")
    if not isinstance(api_key, str) or not isinstance(client_version, str):
        raise ValueError("YouTube public browser configuration is incomplete")

    extractor = _video_tab_items if tab == "videos" else _shorts_tab_items
    items = extractor(initial_data)
    source_pages = [{"url": url, "sha256": _sha256(payload), "page": 1}]
    continuation = _first_continuation(initial_data)
    seen_tokens: set[str] = set()
    page_number = 1
    while continuation and continuation not in seen_tokens:
        if page_number >= maximum_pages:
            raise RuntimeError(f"Maximum page limit reached for {tab}: {maximum_pages}")
        seen_tokens.add(continuation)
        page_number += 1
        page, page_payload = _continuation_page(
            api_key=api_key,
            client_version=client_version,
            continuation=continuation,
            timeout_seconds=timeout_seconds,
        )
        items.extend(extractor(page))
        source_pages.append(
            {
                "url": "https://www.youtube.com/youtubei/v1/browse",
                "sha256": _sha256(page_payload),
                "page": page_number,
            }
        )
        continuation = _first_continuation(page)
    return _unique_items(items), source_pages


def _player_response(source: str) -> dict[str, Any]:
    return _json_after_marker(
        source,
        (
            "var ytInitialPlayerResponse = ",
            "ytInitialPlayerResponse = ",
        ),
    )


def _caption_summary(player: Mapping[str, Any]) -> dict[str, Any]:
    renderer = player.get("captions", {}).get("playerCaptionsTracklistRenderer", {})
    tracks = renderer.get("captionTracks", [])
    languages: list[str] = []
    kinds: list[str] = []
    for track in tracks if isinstance(tracks, list) else []:
        if not isinstance(track, dict):
            continue
        language_code = track.get("languageCode")
        if isinstance(language_code, str):
            languages.append(language_code)
        kind = track.get("kind", "manual")
        if isinstance(kind, str):
            kinds.append(kind)
    return {
        "caption_track_count": len(tracks) if isinstance(tracks, list) else 0,
        "caption_languages": list(dict.fromkeys(languages)),
        "caption_kinds": list(dict.fromkeys(kinds)),
    }


def _caption_tracks(player: Mapping[str, Any]) -> list[dict[str, str]]:
    renderer = player.get("captions", {}).get("playerCaptionsTracklistRenderer", {})
    raw_tracks = renderer.get("captionTracks", [])
    tracks: list[dict[str, str]] = []
    for track in raw_tracks if isinstance(raw_tracks, list) else []:
        if not isinstance(track, dict):
            continue
        base_url = track.get("baseUrl")
        language_code = track.get("languageCode")
        if not isinstance(base_url, str) or not isinstance(language_code, str):
            continue
        name = " ".join(_content_strings(track.get("name", {}))).strip()
        kind = track.get("kind") if isinstance(track.get("kind"), str) else "manual"
        tracks.append(
            {
                "base_url": base_url,
                "language_code": language_code,
                "kind": kind,
                "name": name,
            }
        )
    return tracks


def _caption_json_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["fmt"] = "json3"
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _youtube_video_ids_from_text(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return list(dict.fromkeys(YOUTUBE_VIDEO_ID_PATTERN.findall(value)))


def _safe_token(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return cleaned or "unknown"


def _caption_timeline(payload: bytes) -> list[dict[str, Any]]:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("Caption JSON3 payload must be an object")
    events = parsed.get("events", [])
    if not isinstance(events, list):
        raise ValueError("Caption JSON3 events must be a list")
    timeline: list[dict[str, Any]] = []
    previous_start_ms = -1
    for event in events:
        if not isinstance(event, dict):
            continue
        raw_start = event.get("tStartMs")
        raw_duration = event.get("dDurationMs", 0)
        if not str(raw_start).isdigit() or not str(raw_duration).isdigit():
            continue
        start_ms = int(raw_start)
        duration_ms = int(raw_duration)
        if start_ms < previous_start_ms:
            raise ValueError("Caption timeline is not chronological")
        segments = event.get("segs", [])
        if not isinstance(segments, list):
            continue
        text = (
            "".join(
                str(segment.get("utf8", "")) for segment in segments if isinstance(segment, dict)
            )
            .replace("\n", " ")
            .strip()
        )
        if not text:
            continue
        timeline.append(
            {
                "start_ms": start_ms,
                "duration_ms": duration_ms,
                "text": text,
            }
        )
        previous_start_ms = start_ms
    return timeline


def _timeline_jsonl(timeline: Iterable[Mapping[str, Any]]) -> bytes:
    return (
        "\n".join(
            json.dumps(dict(segment), ensure_ascii=False, separators=(",", ":"))
            for segment in timeline
        )
        + "\n"
    ).encode()


def _collect_caption_evidence(
    player: Mapping[str, Any],
    *,
    video_id: str,
    transcript_cache_dir: Path,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for track_number, track in enumerate(_caption_tracks(player), start=1):
        language = track["language_code"]
        kind = track["kind"]
        record: dict[str, Any] = {
            "track_number": track_number,
            "language_code": language,
            "kind": kind,
            "name": track["name"],
            "source": "YOUTUBE_PUBLIC_CAPTION_TRACK",
            "caption_download_url_persisted": False,
            "git_tracked": False,
        }
        try:
            payload = _fetch(
                _caption_json_url(track["base_url"]),
                timeout_seconds=timeout_seconds,
            )
            filename = ".".join(
                (
                    _safe_token(video_id),
                    f"{track_number:02d}",
                    _safe_token(language),
                    _safe_token(kind),
                    "json3",
                )
            )
            target = transcript_cache_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            timeline = _caption_timeline(payload)
            timeline_payload = _timeline_jsonl(timeline)
            timeline_target = target.with_suffix(".timeline.jsonl")
            timeline_target.write_bytes(timeline_payload)
            record.update(
                {
                    "collection_status": "COLLECTED",
                    "sha256": _sha256(payload),
                    "bytes": len(payload),
                    "research_cache_path": target.as_posix(),
                    "timeline_status": "NORMALIZED",
                    "timeline_sha256": _sha256(timeline_payload),
                    "timeline_bytes": len(timeline_payload),
                    "timeline_segment_count": len(timeline),
                    "timeline_first_start_ms": (timeline[0]["start_ms"] if timeline else None),
                    "timeline_last_end_ms": (
                        timeline[-1]["start_ms"] + timeline[-1]["duration_ms"] if timeline else None
                    ),
                    "timeline_cache_path": timeline_target.as_posix(),
                }
            )
        except Exception as exc:  # noqa: BLE001 - 한 track 실패가 다른 증거를 숨기면 안 된다.
            record.update(
                {
                    "collection_status": "FETCH_FAILED",
                    "error_type": type(exc).__name__,
                }
            )
        evidence.append(record)
    return evidence


def _strategy_keyword_hits(*values: object) -> list[str]:
    haystack = "\n".join(str(value) for value in values if value).casefold()
    return [keyword for keyword in STRATEGY_KEYWORDS if keyword.casefold() in haystack]


def _hydrate_item(
    item: dict[str, Any],
    *,
    expected_channel_id: str,
    timeout_seconds: float,
    collect_captions: bool,
    description_cache_dir: Path,
    transcript_cache_dir: Path,
) -> dict[str, Any]:
    video_id = item["video_id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    payload = _fetch(url, timeout_seconds=timeout_seconds)
    player = _player_response(payload.decode("utf-8", "replace"))
    video_details = player.get("videoDetails", {})
    microformat = player.get("microformat", {}).get("playerMicroformatRenderer", {})
    actual_video_id = video_details.get("videoId")
    channel_id = video_details.get("channelId")
    if actual_video_id != video_id:
        raise ValueError(f"Video identity mismatch: requested={video_id} actual={actual_video_id}")
    if channel_id != expected_channel_id:
        raise ValueError(
            f"Channel identity mismatch for {video_id}: expected={expected_channel_id} "
            f"actual={channel_id}"
        )
    description = video_details.get("shortDescription", "")
    description_payload = str(description).encode()
    description_target = description_cache_dir / f"{_safe_token(video_id)}.txt"
    description_target.parent.mkdir(parents=True, exist_ok=True)
    description_target.write_bytes(description_payload)
    raw_length = video_details.get("lengthSeconds")
    hydrated = dict(item)
    hydrated.update(
        {
            "canonical_watch_url": f"https://www.youtube.com/watch?v={video_id}",
            "title": video_details.get("title") or item.get("title"),
            "channel_id": channel_id,
            "channel_title": video_details.get("author"),
            "length_seconds": int(raw_length) if str(raw_length).isdigit() else None,
            "upload_timestamp": microformat.get("uploadDate"),
            "publish_timestamp": microformat.get("publishDate"),
            "playability_status": player.get("playabilityStatus", {}).get("status"),
            "description_present": bool(description),
            "description_sha256": _sha256(description_payload),
            "description_bytes": len(description_payload),
            "description_cache_path": description_target.as_posix(),
            "description_linked_video_ids": _youtube_video_ids_from_text(description),
            "description_text_git_tracked": False,
            "watch_html_sha256": _sha256(payload),
            "metadata_status": "WATCH_METADATA_HYDRATED",
            "strategy_keyword_hits": _strategy_keyword_hits(
                video_details.get("title") or item.get("title"), description
            ),
            "description_mentions_previous_video": any(
                phrase in str(description).casefold()
                for phrase in ("이전 영상", "앞 영상", "previous video")
            ),
            "description_mentions_strategy_change": any(
                phrase in str(description).casefold()
                for phrase in ("전략 변경", "전략 수정", "최근 변경", "이번 버전")
            ),
            **_caption_summary(player),
        }
    )
    hydrated["caption_evidence"] = (
        _collect_caption_evidence(
            player,
            video_id=video_id,
            transcript_cache_dir=transcript_cache_dir,
            timeout_seconds=timeout_seconds,
        )
        if collect_captions
        else []
    )
    return hydrated


def _parse_public_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _last_12_month_long_form_ids(items: Iterable[Mapping[str, Any]], now: datetime) -> list[str]:
    cutoff = now - timedelta(days=365)
    selected: list[str] = []
    for item in items:
        if item.get("content_kind") != "LONG_FORM":
            continue
        uploaded = _parse_public_timestamp(item.get("upload_timestamp"))
        video_id = item.get("video_id")
        if uploaded is not None and uploaded >= cutoff and isinstance(video_id, str):
            selected.append(video_id)
    return selected


def _required_full_review_ids(
    items: Iterable[Mapping[str, Any]],
    *,
    latest_long_form_30: Iterable[str],
    last_12_months: Iterable[str],
    strategy_keyword_history: Iterable[str],
    description_linked_video_ids: Iterable[str],
) -> list[str]:
    long_form_order = [
        str(item["video_id"])
        for item in items
        if item.get("content_kind") == "LONG_FORM" and isinstance(item.get("video_id"), str)
    ]
    channel_long_form = set(long_form_order)
    required = {
        *latest_long_form_30,
        *last_12_months,
        *strategy_keyword_history,
        *description_linked_video_ids,
    }
    if DEFAULT_REQUESTED_VIDEO_ID in channel_long_form:
        required.add(DEFAULT_REQUESTED_VIDEO_ID)
    return [video_id for video_id in long_form_order if video_id in required]


def build_description_manifest(index: Mapping[str, Any]) -> dict[str, Any]:
    videos = index.get("videos", [])
    records: list[dict[str, Any]] = []
    for item in videos if isinstance(videos, list) else []:
        if not isinstance(item, dict):
            continue
        hydrated = item.get("metadata_status") == "WATCH_METADATA_HYDRATED"
        records.append(
            {
                "video_id": item.get("video_id"),
                "content_kind": item.get("content_kind"),
                "upload_timestamp": item.get("upload_timestamp"),
                "inspection_status": "INSPECTED" if hydrated else "NOT_RUN",
                "description_present": item.get("description_present") if hydrated else None,
                "description_sha256": item.get("description_sha256") if hydrated else None,
                "description_bytes": item.get("description_bytes") if hydrated else None,
                "research_cache_path": (item.get("description_cache_path") if hydrated else None),
                "linked_video_ids": (
                    item.get("description_linked_video_ids", []) if hydrated else []
                ),
                "strategy_keyword_hits": item.get("strategy_keyword_hits", []) if hydrated else [],
                "description_text_git_tracked": False,
            }
        )
    inspected = sum(record["inspection_status"] == "INSPECTED" for record in records)
    return {
        "schema_version": 1,
        "collected_ts_utc": index.get("collected_ts_utc"),
        "channel_id": index.get("channel_id"),
        "status": "COMPLETE" if records and inspected == len(records) else "PARTIAL_NOT_RUN",
        "counts": {
            "total": len(records),
            "inspected": inspected,
            "not_run": len(records) - inspected,
        },
        "raw_description_text_committed": False,
        "records": records,
        "claims_boundary": {
            "description_checksum_proves_rule_meaning": False,
            "marketing_performance_is_verified": False,
        },
    }


def build_transcript_manifest(index: Mapping[str, Any]) -> dict[str, Any]:
    videos = index.get("videos", [])
    records: list[dict[str, Any]] = []
    for item in videos if isinstance(videos, list) else []:
        if not isinstance(item, dict) or item.get("content_kind") != "LONG_FORM":
            continue
        hydrated = item.get("metadata_status") == "WATCH_METADATA_HYDRATED"
        track_count = item.get("caption_track_count") if hydrated else None
        evidence = item.get("caption_evidence", []) if hydrated else []
        collected = [
            record
            for record in evidence
            if isinstance(record, dict) and record.get("collection_status") == "COLLECTED"
        ]
        normalized = [
            record
            for record in collected
            if record.get("timeline_status") == "NORMALIZED"
            and isinstance(record.get("timeline_sha256"), str)
            and record.get("timeline_segment_count", 0) > 0
        ]
        if not hydrated:
            caption_status = "NOT_RUN"
            asr_status = "NOT_RUN"
        elif collected:
            caption_status = "COLLECTED"
            asr_status = "NOT_REQUIRED_PUBLIC_CAPTION_AVAILABLE"
        elif track_count == 0:
            caption_status = "UNAVAILABLE"
            asr_status = "NOT_RUN_REQUIRED"
        else:
            caption_status = "FETCH_FAILED"
            asr_status = "NOT_RUN_REQUIRED"
        records.append(
            {
                "video_id": item.get("video_id"),
                "upload_timestamp": item.get("upload_timestamp"),
                "caption_track_count": track_count,
                "caption_languages": item.get("caption_languages", []) if hydrated else [],
                "caption_kinds": item.get("caption_kinds", []) if hydrated else [],
                "public_caption_status": caption_status,
                "caption_evidence": evidence,
                "normalized_timeline_status": ("AVAILABLE" if normalized else "NOT_AVAILABLE"),
                "normalized_timeline_tracks": len(normalized),
                "asr_status": asr_status,
                "asr_required": bool(hydrated and not collected),
                "timeline_review_status": "NOT_RUN",
                "frame_review_status": "NOT_RUN",
                "full_video_review_status": "NOT_RUN",
                "timestamp_evidence_status": "NOT_RUN",
                "transcript_text_git_tracked": False,
            }
        )
    ready = sum(
        record["public_caption_status"] == "COLLECTED" or record["asr_status"] == "COMPLETE"
        for record in records
    )
    return {
        "schema_version": 1,
        "collected_ts_utc": index.get("collected_ts_utc"),
        "channel_id": index.get("channel_id"),
        "status": "COMPLETE" if records and ready == len(records) else "NOT_RUN_OR_PARTIAL",
        "counts": {
            "long_form": len(records),
            "transcript_available": ready,
            "not_available": len(records) - ready,
        },
        "raw_transcripts_committed": False,
        "records": records,
        "claims_boundary": {
            "metadata_only_is_full_video_review": False,
            "caption_collection_is_full_video_review": False,
            "frame_extraction_is_frame_review": False,
            "timeline_review_is_complete": False,
            "exact_public_rules_are_verified": False,
        },
    }


def build_index(
    *,
    channel_url: str,
    channel_id: str,
    hydrate: bool,
    request_delay_seconds: float,
    timeout_seconds: float,
    maximum_pages: int,
    collect_captions: bool = False,
    description_cache_dir: Path = DEFAULT_DESCRIPTION_CACHE_DIR,
    transcript_cache_dir: Path = DEFAULT_TRANSCRIPT_CACHE_DIR,
) -> dict[str, Any]:
    long_form, video_pages = _collect_tab(
        channel_url,
        "videos",
        timeout_seconds=timeout_seconds,
        maximum_pages=maximum_pages,
    )
    shorts, short_pages = _collect_tab(
        channel_url,
        "shorts",
        timeout_seconds=timeout_seconds,
        maximum_pages=maximum_pages,
    )
    long_form_ids = {item["video_id"] for item in long_form}
    short_ids = {item["video_id"] for item in shorts}
    overlap = sorted(long_form_ids & short_ids)
    if overlap:
        raise ValueError(f"Channel tabs contain duplicate classifications: {overlap}")

    items = [*long_form, *shorts]
    if hydrate:
        hydrated: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            hydrated.append(
                _hydrate_item(
                    item,
                    expected_channel_id=channel_id,
                    timeout_seconds=timeout_seconds,
                    collect_captions=collect_captions,
                    description_cache_dir=description_cache_dir,
                    transcript_cache_dir=transcript_cache_dir,
                )
            )
            if request_delay_seconds > 0 and index + 1 < len(items):
                time.sleep(request_delay_seconds)
        items = hydrated

    latest_long_form_30 = [
        item["video_id"] for item in items if item["content_kind"] == "LONG_FORM"
    ][:30]
    now = datetime.now(tz=UTC)
    last_12_months = _last_12_month_long_form_ids(items, now) if hydrate else []
    keyword_history = (
        [
            item["video_id"]
            for item in items
            if item.get("content_kind") == "LONG_FORM" and item.get("strategy_keyword_hits")
        ]
        if hydrate
        else []
    )
    description_linked = (
        list(
            dict.fromkeys(
                linked_video_id
                for item in items
                if item.get("content_kind") == "LONG_FORM"
                for linked_video_id in item.get("description_linked_video_ids", [])
            )
        )
        if hydrate
        else []
    )
    required_full_review = (
        _required_full_review_ids(
            items,
            latest_long_form_30=latest_long_form_30,
            last_12_months=last_12_months,
            strategy_keyword_history=keyword_history,
            description_linked_video_ids=description_linked,
        )
        if hydrate
        else []
    )
    return {
        "schema_version": 1,
        "collected_ts_utc": _utc_now(),
        "channel_id": channel_id,
        "channel_handle": "@siholab",
        "channel_title": "Siho LAB",
        "channel_url": channel_url.rstrip("/"),
        "collection_method": "youtube_public_channel_tabs_and_public_watch_metadata",
        "youtube_public_browser_key_persisted": False,
        "index_status": "COMPLETE_METADATA" if hydrate else "COMPLETE_IDS_PARTIAL_METADATA",
        "public_tab_inventory": ["videos", "shorts"],
        "counts": {
            "all_public_items": len(items),
            "long_form": len(long_form),
            "shorts": len(shorts),
            "watch_metadata_hydrated": sum(
                item["metadata_status"] == "WATCH_METADATA_HYDRATED" for item in items
            ),
        },
        "selection_scope": {
            "latest_long_form_30": latest_long_form_30,
            "all_long_form_last_12_months": (
                "PENDING_EXACT_UPLOAD_TIMESTAMPS" if not hydrate else last_12_months
            ),
            "strategy_keyword_history": (
                "PENDING_DESCRIPTION_AND_TRANSCRIPT_INDEX" if not hydrate else keyword_history
            ),
            "description_linked_video_ids": (
                "PENDING_DESCRIPTION_INDEX" if not hydrate else description_linked
            ),
            "required_full_review": (
                "PENDING_HYDRATED_SCOPE" if not hydrate else required_full_review
            ),
            "required_full_review_count": (None if not hydrate else len(required_full_review)),
            "shorts_policy": "INDEX_ONLY_NOT_SOLE_NUMERIC_RULE_EVIDENCE",
        },
        "source_pages": [*video_pages, *short_pages],
        "videos": items,
        "claims_boundary": {
            "titles_and_channel_tabs_are_strategy_proof": False,
            "marketing_performance_is_verified": False,
            "profitability_is_verified": False,
            "exact_public_rules_are_verified": False,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-url", default=DEFAULT_CHANNEL_URL)
    parser.add_argument("--channel-id", default=DEFAULT_CHANNEL_ID)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--from-existing-index",
        action="store_true",
        help="네트워크 수집 없이 기존 index에서 설명·자막 manifest만 다시 만든다.",
    )
    parser.add_argument("--hydrate", action="store_true")
    parser.add_argument("--collect-captions", action="store_true")
    parser.add_argument("--description-output", type=Path, default=DEFAULT_DESCRIPTION_OUTPUT)
    parser.add_argument("--transcript-output", type=Path, default=DEFAULT_TRANSCRIPT_OUTPUT)
    parser.add_argument("--description-cache-dir", type=Path, default=DEFAULT_DESCRIPTION_CACHE_DIR)
    parser.add_argument("--transcript-cache-dir", type=Path, default=DEFAULT_TRANSCRIPT_CACHE_DIR)
    parser.add_argument("--request-delay-seconds", type=float, default=0.25)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--maximum-pages", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.from_existing_index:
        report = json.loads(args.output.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("Existing SIHO index must be a JSON object")
    else:
        report = build_index(
            channel_url=args.channel_url,
            channel_id=args.channel_id,
            hydrate=args.hydrate,
            request_delay_seconds=args.request_delay_seconds,
            timeout_seconds=args.timeout_seconds,
            maximum_pages=args.maximum_pages,
            collect_captions=args.collect_captions,
            description_cache_dir=args.description_cache_dir,
            transcript_cache_dir=args.transcript_cache_dir,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    description_manifest = build_description_manifest(report)
    transcript_manifest = build_transcript_manifest(report)
    for output, payload in (
        (args.description_output, description_manifest),
        (args.transcript_output, transcript_manifest),
    ):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "description_output": str(args.description_output),
                "transcript_output": str(args.transcript_output),
                **report["counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
