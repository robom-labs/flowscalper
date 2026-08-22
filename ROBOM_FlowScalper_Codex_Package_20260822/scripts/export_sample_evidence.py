"""완료된 fixture PAPER 거래를 재현 가능한 다섯 종류 증거로 내보낸다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.exports.run_exporter import RunExporter
from backend.app.replay.engine import ReplayEngine
from backend.app.storage.sqlite import SQLiteLedger

EVENT_TYPES = {
    "OBSERVING": "MARKET",
    "ARMED": "DECISION",
    "ENTRY_PENDING": "ORDER",
    "PROTECTED": "FILL",
    "CLOSED": "EXIT",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    ledger = SQLiteLedger(arguments.database)
    try:
        recovered = ledger.recover_latest(recovered_ts_ms=0)
        if recovered is None:
            raise RuntimeError("내보낼 열린 Run이 없습니다.")
        trades = ledger.list_trades(recovered.run_id)
        if len(trades) != 1:
            raise RuntimeError(f"증거 Run의 완료 거래는 1건이어야 합니다: {len(trades)}")
        transitions = ledger.list_transitions(recovered.run_id)
        events = [
            {
                "sequence": row["sequence"],
                "ts_ms": row["ts_ms"],
                "event_type": EVENT_TYPES.get(str(row["state"]), "MARKET"),
                "state": row["state"],
                "reason_code": row["payload"].get("reason_code", "NONE"),
                "payload": row["payload"],
            }
            for row in transitions
        ]
        config = {
            "mode": "FIXTURE_OFFLINE",
            "fee_model": "recorded",
            "latency_model": "recorded",
            "risk_per_trade": "0.10%",
        }
        paths = RunExporter(ledger).export_run(
            arguments.output,
            run_id=recovered.run_id,
            config=config,
            events=events,
            logs=[{"level": "INFO", "message": "OFFLINE FIXTURE PAPER evidence"}],
            strategy_version="LSA_REVERSAL_V1",
            seed=20260822,
        )
        replay = ReplayEngine().replay_bundle(paths[3])
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "run_id": recovered.run_id,
                    "trade": trades[0],
                    "transition_count": len(transitions),
                    "replay_checksum": replay.checksum,
                    "replay_path": list(replay.decision_path),
                    "export_paths": [str(path) for path in paths],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
