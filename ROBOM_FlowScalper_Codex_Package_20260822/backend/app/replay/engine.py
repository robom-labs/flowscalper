"""버전·시드·설정·이벤트를 고정해 PAPER 결정 경로를 재현한다."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


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


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _write_deterministic(archive: zipfile.ZipFile, name: str, content: str) -> None:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, content.encode())
