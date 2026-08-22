"""snapshot/delta, 중복, gap, reset과 교차 호가 차단을 검증한다."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.orderbook import BinanceOrderBook, BybitOrderBook, SequenceGap
from backend.app.orderbook.books import InvalidBook


def test_binance_snapshot_delta_duplicate_and_gap_recovery() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "binance_gap_sequence.json").read_text()
    )
    snapshot = fixture["snapshot"]
    first, second, gap = fixture["events"]
    resync = fixture["resync"]
    book = BinanceOrderBook()
    book.reset_snapshot(snapshot["lastUpdateId"], snapshot["bids"], snapshot["asks"])
    assert book.apply_delta(first["U"], first["u"], first["pu"], first["b"], first["a"])
    assert book.apply_delta(second["U"], second["u"], second["pu"], second["b"], second["a"])
    assert not book.apply_delta(102, 102, 101, [], [])
    assert book.top(1) == ([(Decimal("100.0"), Decimal("2.5"))], [(Decimal("101.5"), Decimal("4"))])

    with pytest.raises(SequenceGap):
        book.apply_delta(gap["U"], gap["u"], gap["pu"], gap["b"], gap["a"])
    assert book.stale and not book.sequence_valid

    book.reset_snapshot(resync["lastUpdateId"], resync["bids"], resync["asks"])
    assert not book.stale and book.sequence_valid


def test_bybit_new_snapshot_resets_book() -> None:
    book = BybitOrderBook()
    assert book.apply("snapshot", 10, 100, [["100", "2"]], [["101", "2"]])
    assert book.apply("delta", 11, 101, [["100", "0"], ["99", "3"]], [])
    assert book.apply("snapshot", 1, 200, [["98", "4"]], [["99", "4"]])
    assert book.top(1)[0][0][0] == Decimal("98")


def test_crossed_book_fails_closed() -> None:
    book = BinanceOrderBook()
    with pytest.raises(InvalidBook):
        book.reset_snapshot(1, [["101", "1"]], [["100", "1"]])
    assert book.stale
