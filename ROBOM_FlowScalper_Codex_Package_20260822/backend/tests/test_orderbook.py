"""snapshot/delta, 중복, gap, reset과 교차 호가 차단을 검증한다."""

import json
from decimal import Decimal
from pathlib import Path
from random import Random

import pytest

from backend.app.orderbook import BinanceOrderBook, BybitOrderBook, SequenceGap
from backend.app.orderbook.books import InvalidBook, LocalOrderBook


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


def test_top_depth_keeps_exact_price_order_without_full_book_sort_contract() -> None:
    book = LocalOrderBook(
        bids={Decimal(str(price)): Decimal("1") for price in range(1, 1_001)},
        asks={Decimal(str(price)): Decimal("2") for price in range(1_001, 2_001)},
        sequence_valid=True,
        stale=False,
    )

    bids, asks = book.top(20)

    assert [price for price, _ in bids] == [Decimal(str(price)) for price in range(1_000, 980, -1)]
    assert [price for price, _ in asks] == [Decimal(str(price)) for price in range(1_001, 1_021)]
    bids_50, asks_50 = book.top(50)
    assert [price for price, _ in bids_50] == [
        Decimal(str(price)) for price in range(1_000, 950, -1)
    ]
    assert [price for price, _ in asks_50] == [
        Decimal(str(price)) for price in range(1_001, 1_051)
    ]
    assert book.top(0) == ([], [])


def test_top_cache_matches_full_sort_through_add_update_and_remove_sequence() -> None:
    random = Random(12)
    book = LocalOrderBook(
        bids={Decimal(str(price)): Decimal("1") for price in range(800, 1_000)},
        asks={Decimal(str(price)): Decimal("1") for price in range(1_001, 1_201)},
        sequence_valid=True,
        stale=False,
    )
    book.top(20)

    for _ in range(500):
        bid_price = Decimal(str(random.randrange(700, 1_000)))
        ask_price = Decimal(str(random.randrange(1_001, 1_301)))
        bid_quantity = "0" if random.random() < 0.18 else str(random.randrange(1, 10))
        ask_quantity = "0" if random.random() < 0.18 else str(random.randrange(1, 10))
        book._apply_levels([[bid_price, bid_quantity]], [[ask_price, ask_quantity]])

        bids, asks = book.top(20)
        expected_bids = sorted(book.bids, reverse=True)[:20]
        expected_asks = sorted(book.asks)[:20]
        assert [price for price, _ in bids] == expected_bids
        assert [price for price, _ in asks] == expected_asks
