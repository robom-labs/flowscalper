"""거래 CSV·Run JSON·HTML·리플레이 ZIP·진단 로그를 한 번에 내보낸다."""

from __future__ import annotations

import csv
import html
import io
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path

from backend.app.analytics.reports import TradeAnalytics
from backend.app.replay.engine import ReplayEngine
from backend.app.storage.sqlite import SQLiteLedger


class RunExporter:
    def __init__(self, ledger: SQLiteLedger) -> None:
        self._ledger = ledger
        self._analytics = TradeAnalytics()
        self._replay = ReplayEngine()

    def export_run(
        self,
        destination: Path,
        *,
        run_id: str,
        config: Mapping[str, object],
        events: Sequence[Mapping[str, object]],
        logs: Sequence[Mapping[str, object]],
        strategy_version: str,
        seed: int,
    ) -> tuple[Path, ...]:
        trades = self._ledger.list_trades(run_id)
        report = self._analytics.report(trades, starting_equity=Decimal("1000"))
        destination.mkdir(parents=True, exist_ok=True)
        trade_path = destination / f"{run_id}-trades.csv"
        summary_path = destination / f"{run_id}-summary.json"
        html_path = destination / f"{run_id}-report.html"
        replay_path = destination / f"{run_id}-replay.zip"
        log_path = destination / f"{run_id}-diagnostics.jsonl"
        _write_csv(trade_path, trades)
        summary_path.write_text(
            json.dumps(
                {"run_id": run_id, "sample_type": "PAPER", "report": report},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        html_path.write_text(_html_report(run_id, report), encoding="utf-8")
        self._replay.write_bundle(
            replay_path,
            events,
            config=config,
            strategy_version=strategy_version,
            seed=seed,
        )
        log_path.write_text(
            "\n".join(
                json.dumps(log, ensure_ascii=False, sort_keys=True, default=str) for log in logs
            )
            + ("\n" if logs else ""),
            encoding="utf-8",
        )
        return trade_path, summary_path, html_path, replay_path, log_path


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in fields})
    path.write_text(buffer.getvalue(), encoding="utf-8")


def _csv_value(value: object) -> object:
    if isinstance(value, dict | list | tuple):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return value


def _html_report(run_id: str, report: Mapping[str, object]) -> str:
    rows = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in report.items()
        if key != "contributions"
    )
    return (
        '<!doctype html><html lang="ko"><meta charset="utf-8">'
        f"<title>{html.escape(run_id)} PAPER report</title>"
        "<style>body{font-family:system-ui;max-width:900px;margin:40px auto;background:#07151c;"
        "color:#dce9ee}table{border-collapse:collapse;width:100%}th,td{padding:10px;"
        "border:1px solid #24404d;text-align:left}</style>"
        f"<h1>{html.escape(run_id)} PAPER 성과</h1><p>실제 주문이 아닌 연구 표본입니다.</p>"
        f"<table>{rows}</table></html>"
    )
