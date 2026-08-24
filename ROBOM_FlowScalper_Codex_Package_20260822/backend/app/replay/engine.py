"""버전·시드·설정·이벤트를 고정해 PAPER 결정 경로를 재현한다."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ReplayIntegrityError(RuntimeError):
    """녹화 번들과 재생 결과의 checksum 불일치를 차단한다."""


@dataclass(frozen=True, slots=True)
class ReplayResult:
    checksum: str
    event_count: int
    decision_path: tuple[str, ...]
    final_state: str
    strategy_version: str
    seed: int


@dataclass(frozen=True, slots=True)
class MarketReplayDigest:
    checksum: str
    event_count: int
    first_ts_ms: int | None
    last_ts_ms: int | None
    event_type_counts: dict[str, int]
    symbol_counts: dict[str, int]
    decision_path: tuple[str, ...]
    final_state: str
    strategy_version: str
    seed: int


class ReplayEngine:
    """순서화된 이벤트를 정렬해 같은 결정 경로와 checksum을 만든다."""

    def replay(
        self,
        events: Sequence[Mapping[str, object]],
        *,
        config: Mapping[str, object],
        strategy_version: str,
        seed: int,
    ) -> ReplayResult:
        ordered = sorted(
            (_normalize_event(event) for event in events),
            key=lambda event: (event["sequence"], event["ts_ms"]),
        )
        sequences = [int(str(event["sequence"])) for event in ordered]
        if sequences != list(range(1, len(ordered) + 1)):
            raise ReplayIntegrityError("리플레이 이벤트 sequence가 연속적이지 않습니다.")
        decision_path = tuple(
            f"{event['event_type']}:{event['reason_code']}"
            for event in ordered
            if event["event_type"] in {"DECISION", "ORDER", "FILL", "EXIT"}
        )
        final_state = str(ordered[-1]["state"]) if ordered else "EMPTY"
        material = {
            "schema_version": 1,
            "strategy_version": strategy_version,
            "seed": seed,
            "config": dict(config),
            "events": ordered,
            "decision_path": decision_path,
            "final_state": final_state,
        }
        checksum = hashlib.sha256(_canonical_json(material).encode()).hexdigest()
        return ReplayResult(
            checksum=checksum,
            event_count=len(ordered),
            decision_path=decision_path,
            final_state=final_state,
            strategy_version=strategy_version,
            seed=seed,
        )

    def replay_market_path(
        self,
        events: Sequence[Mapping[str, object]],
        *,
        config: Mapping[str, object],
        strategy_version: str,
        seed: int,
        decision_path: Sequence[str],
        final_state: str,
        cooperative_yield: Callable[[], None] | None = None,
    ) -> MarketReplayDigest:
        """저장된 공개시장 이벤트와 재처리 결정 경로를 하나의 checksum으로 묶는다."""

        ordered_events = sorted(
            events,
            key=lambda event: (
                int(str(event["venue_ts_ms"])),
                int(str(event["receive_monotonic_ns"])),
                str(event["event_id"]),
            ),
        )
        if cooperative_yield is not None:
            cooperative_yield()
        event_ids: set[str] = set()
        event_type_counts: dict[str, int] = {}
        symbol_counts: dict[str, int] = {}
        event_stream_digest = hashlib.sha256()
        for index, source_event in enumerate(ordered_events, start=1):
            event = _normalize_market_event(source_event)
            event_id = str(event["event_id"])
            if event_id in event_ids:
                raise ReplayIntegrityError("시장 리플레이에 중복 event_id가 있습니다.")
            event_ids.add(event_id)
            event_type = str(event["event_type"])
            symbol = str(event["symbol"])
            event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
            _length_prefixed_update(event_stream_digest, _canonical_json(event).encode())
            if cooperative_yield is not None and index % 128 == 0:
                cooperative_yield()
        normalized_path = tuple(str(item) for item in decision_path)
        decision_path_digest = hashlib.sha256()
        for item in normalized_path:
            _length_prefixed_update(decision_path_digest, item.encode())
        if cooperative_yield is not None:
            cooperative_yield()
        material = {
            "schema_version": 3,
            "strategy_version": strategy_version,
            "seed": seed,
            "config": dict(config),
            "event_count": len(ordered_events),
            "event_stream_checksum": event_stream_digest.hexdigest(),
            "event_type_counts": dict(sorted(event_type_counts.items())),
            "symbol_counts": dict(sorted(symbol_counts.items())),
            "decision_path_count": len(normalized_path),
            "decision_path_checksum": decision_path_digest.hexdigest(),
            "final_state": final_state,
        }
        checksum = hashlib.sha256(_canonical_json(material).encode()).hexdigest()
        return MarketReplayDigest(
            checksum=checksum,
            event_count=len(ordered_events),
            first_ts_ms=(
                int(str(ordered_events[0]["venue_ts_ms"])) if ordered_events else None
            ),
            last_ts_ms=(
                int(str(ordered_events[-1]["venue_ts_ms"])) if ordered_events else None
            ),
            event_type_counts=dict(sorted(event_type_counts.items())),
            symbol_counts=dict(sorted(symbol_counts.items())),
            decision_path=normalized_path,
            final_state=final_state,
            strategy_version=strategy_version,
            seed=seed,
        )

    def write_bundle(
        self,
        destination: Path,
        events: Sequence[Mapping[str, object]],
        *,
        config: Mapping[str, object],
        strategy_version: str,
        seed: int,
    ) -> ReplayResult:
        result = self.replay(
            events,
            config=config,
            strategy_version=strategy_version,
            seed=seed,
        )
        manifest = {
            "schema_version": 1,
            "checksum": result.checksum,
            "strategy_version": strategy_version,
            "seed": seed,
            "config": dict(config),
        }
        event_text = "\n".join(_canonical_json(_normalize_event(event)) for event in events)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write_deterministic(archive, "manifest.json", _canonical_json(manifest))
            _write_deterministic(archive, "events.jsonl", event_text)
        return result

    def replay_bundle(self, bundle: Path) -> ReplayResult:
        with zipfile.ZipFile(bundle) as archive:
            manifest_value = json.loads(archive.read("manifest.json"))
            event_lines = archive.read("events.jsonl").decode().splitlines()
        if not isinstance(manifest_value, dict):
            raise ReplayIntegrityError("리플레이 manifest는 객체여야 합니다.")
        events = [json.loads(line) for line in event_lines if line]
        config = manifest_value.get("config")
        if not isinstance(config, dict):
            raise ReplayIntegrityError("리플레이 config가 없거나 잘못됐습니다.")
        result = self.replay(
            events,
            config=config,
            strategy_version=str(manifest_value["strategy_version"]),
            seed=int(str(manifest_value["seed"])),
        )
        if result.checksum != manifest_value.get("checksum"):
            raise ReplayIntegrityError("리플레이 결과 checksum이 기록과 다릅니다.")
        return result


def _normalize_event(event: Mapping[str, object]) -> dict[str, object]:
    return {
        "sequence": int(str(event["sequence"])),
        "ts_ms": int(str(event["ts_ms"])),
        "event_type": str(event["event_type"]),
        "state": str(event["state"]),
        "reason_code": str(event.get("reason_code", "NONE")),
        "payload": event.get("payload", {}),
    }


def _normalize_market_event(event: Mapping[str, object]) -> dict[str, object]:
    return {
        "event_id": str(event["event_id"]),
        "run_id": str(event["run_id"]),
        "venue": str(event["venue"]),
        "symbol": str(event["symbol"]),
        "event_type": str(event["event_type"]),
        "venue_ts_ms": int(str(event["venue_ts_ms"])),
        "transaction_ts_ms": (
            int(str(event["transaction_ts_ms"]))
            if event.get("transaction_ts_ms") is not None
            else None
        ),
        "receive_monotonic_ns": int(str(event["receive_monotonic_ns"])),
        "sequence_start": event.get("sequence_start"),
        "sequence_end": event.get("sequence_end"),
        "previous_sequence_end": event.get("previous_sequence_end"),
        "payload_version": str(event.get("payload_version", "1")),
        "quality": event.get("quality", {}),
        "data": event.get("data", {}),
    }


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _length_prefixed_update(digest: Any, value: bytes) -> None:
    """가변 길이 필드를 모호성 없이 스트리밍 checksum에 추가한다."""

    digest.update(len(value).to_bytes(8, byteorder="big", signed=False))
    digest.update(value)


def _write_deterministic(archive: zipfile.ZipFile, name: str, content: str) -> None:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, content.encode())
