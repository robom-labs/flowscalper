# SIHO 공개영상 색인의 YouTube renderer 해석과 증거 분류를 검증한다.

from __future__ import annotations

from datetime import UTC, datetime

from scripts.research_siho_video_index import (
    _caption_json_url,
    _caption_summary,
    _caption_timeline,
    _duration_seconds,
    _first_continuation,
    _last_12_month_long_form_ids,
    _required_full_review_ids,
    _shorts_tab_items,
    _strategy_keyword_hits,
    _timeline_jsonl,
    _video_tab_items,
    _youtube_video_ids_from_text,
    build_description_manifest,
    build_transcript_manifest,
)


def test_duration_parser_handles_minute_and_hour_labels() -> None:
    assert _duration_seconds("2:16") == 136
    assert _duration_seconds("1:28:49") == 5_329
    assert _duration_seconds(None) is None
    assert _duration_seconds("실시간") is None


def test_video_tab_item_uses_channel_tab_as_long_form_classification() -> None:
    payload = {
        "contents": [
            {
                "lockupViewModel": {
                    "contentId": "video-1",
                    "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
                    "metadata": {
                        "lockupMetadataViewModel": {
                            "title": {"content": "공개 장문 영상"},
                            "metadata": {
                                "contentMetadataViewModel": {
                                    "metadataRows": [
                                        {
                                            "metadataParts": [
                                                {"text": {"content": "조회수 3회"}},
                                                {"text": {"content": "2일 전"}},
                                            ]
                                        }
                                    ]
                                }
                            },
                        }
                    },
                    "contentImage": {
                        "thumbnailViewModel": {
                            "overlays": [
                                {
                                    "thumbnailBadgeViewModel": {
                                        "text": "26:03",
                                    }
                                }
                            ]
                        }
                    },
                }
            }
        ]
    }

    items = _video_tab_items(payload)

    assert items == [
        {
            "video_id": "video-1",
            "url": "https://www.youtube.com/watch?v=video-1",
            "source_tab": "videos",
            "content_kind": "LONG_FORM",
            "title": "공개 장문 영상",
            "duration_text": "26:03",
            "length_seconds": 1_563,
            "views_text": "조회수 3회",
            "published_text": "2일 전",
            "metadata_status": "CHANNEL_INDEX_ONLY",
        }
    ]


def test_shorts_item_is_index_only_and_not_a_numeric_rule_source() -> None:
    payload = {
        "shortsLockupViewModel": {
            "onTap": {
                "innertubeCommand": {
                    "reelWatchEndpoint": {"videoId": "short-1"},
                }
            },
            "overlayMetadata": {
                "primaryText": {"content": "요약 #Shorts"},
                "secondaryText": {"content": "조회수 10회"},
            },
        }
    }

    assert _shorts_tab_items(payload) == [
        {
            "video_id": "short-1",
            "url": "https://www.youtube.com/shorts/short-1",
            "source_tab": "shorts",
            "content_kind": "SHORTS",
            "title": "요약 #Shorts",
            "duration_text": None,
            "length_seconds": None,
            "views_text": "조회수 10회",
            "published_text": None,
            "metadata_status": "CHANNEL_INDEX_ONLY",
        }
    ]


def test_continuation_and_caption_metadata_exclude_caption_download_urls() -> None:
    continuation = {
        "continuationItemRenderer": {
            "continuationEndpoint": {"continuationCommand": {"token": "public-continuation"}}
        }
    }
    player = {
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {
                        "languageCode": "ko",
                        "kind": "asr",
                        "baseUrl": "https://example.invalid/private-to-evidence",
                    },
                    {"languageCode": "en", "baseUrl": "https://example.invalid/en"},
                ]
            }
        }
    }

    assert _first_continuation(continuation) == "public-continuation"
    assert _caption_summary(player) == {
        "caption_track_count": 2,
        "caption_languages": ["ko", "en"],
        "caption_kinds": ["asr", "manual"],
    }


def test_caption_url_replaces_existing_format_without_persisting_it() -> None:
    url = _caption_json_url("https://www.youtube.com/api/timedtext?v=video&fmt=srv3&lang=ko")

    assert "fmt=json3" in url
    assert "fmt=srv3" not in url


def test_caption_timeline_is_chronological_and_has_reproducible_jsonl() -> None:
    payload = (
        b'{"events":['
        b'{"tStartMs":"10","dDurationMs":"20","segs":[{"utf8":"first"}]},'
        b'{"tStartMs":"40","dDurationMs":"15","segs":[{"utf8":"second"}]}'
        b"]}"
    )

    timeline = _caption_timeline(payload)

    assert timeline == [
        {"start_ms": 10, "duration_ms": 20, "text": "first"},
        {"start_ms": 40, "duration_ms": 15, "text": "second"},
    ]
    assert _timeline_jsonl(timeline) == (
        b'{"start_ms":10,"duration_ms":20,"text":"first"}\n'
        b'{"start_ms":40,"duration_ms":15,"text":"second"}\n'
    )


def test_description_youtube_links_are_unique_and_exact() -> None:
    description = (
        "https://youtu.be/1mJDNm4Yko4?t=3 "
        "https://www.youtube.com/watch?v=cCLI_ge6Tzg&feature=share "
        "https://youtu.be/1mJDNm4Yko4"
    )

    assert _youtube_video_ids_from_text(description) == [
        "1mJDNm4Yko4",
        "cCLI_ge6Tzg",
    ]


def test_strategy_keywords_and_exact_recent_scope_are_deterministic() -> None:
    assert _strategy_keyword_hits("EMA 추세 진입", "TRAIL stop") == [
        "진입",
        "추세",
        "trail",
        "stop",
    ]
    items = [
        {
            "video_id": "recent",
            "content_kind": "LONG_FORM",
            "upload_timestamp": "2026-01-01T00:00:00Z",
        },
        {
            "video_id": "old",
            "content_kind": "LONG_FORM",
            "upload_timestamp": "2024-01-01T00:00:00Z",
        },
        {
            "video_id": "short",
            "content_kind": "SHORTS",
            "upload_timestamp": "2026-01-01T00:00:00Z",
        },
    ]

    assert _last_12_month_long_form_ids(items, datetime(2026, 8, 28, tzinfo=UTC)) == ["recent"]


def test_required_full_review_scope_uses_union_in_channel_order() -> None:
    items = [
        {"video_id": "first-video", "content_kind": "LONG_FORM"},
        {"video_id": "1mJDNm4Yko4", "content_kind": "LONG_FORM"},
        {"video_id": "third-video", "content_kind": "LONG_FORM"},
        {"video_id": "short-video", "content_kind": "SHORTS"},
    ]

    assert _required_full_review_ids(
        items,
        latest_long_form_30=["third-video"],
        last_12_months=["first-video"],
        strategy_keyword_history=[],
        description_linked_video_ids=["outside-id"],
    ) == ["first-video", "1mJDNm4Yko4", "third-video"]


def test_manifests_keep_uncollected_research_explicitly_not_run() -> None:
    index = {
        "collected_ts_utc": "2026-08-28T00:00:00Z",
        "channel_id": "channel",
        "videos": [
            {
                "video_id": "long",
                "content_kind": "LONG_FORM",
                "metadata_status": "CHANNEL_INDEX_ONLY",
            },
            {
                "video_id": "short",
                "content_kind": "SHORTS",
                "metadata_status": "CHANNEL_INDEX_ONLY",
            },
        ],
    }

    descriptions = build_description_manifest(index)
    transcripts = build_transcript_manifest(index)

    assert descriptions["status"] == "PARTIAL_NOT_RUN"
    assert descriptions["counts"] == {"total": 2, "inspected": 0, "not_run": 2}
    assert transcripts["status"] == "NOT_RUN_OR_PARTIAL"
    assert transcripts["records"][0]["public_caption_status"] == "NOT_RUN"
    assert transcripts["records"][0]["asr_required"] is False
    assert transcripts["records"][0]["timeline_review_status"] == "NOT_RUN"
    assert transcripts["records"][0]["full_video_review_status"] == "NOT_RUN"
    assert transcripts["claims_boundary"]["caption_collection_is_full_video_review"] is False
