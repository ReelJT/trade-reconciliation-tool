"""Checks that the reconciliation finds every planted break and nothing extra."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd

from generate_data import make, DATA
from recon import load, match_trades, reconcile_positions, reconcile_cash

make()
internal, broker, start, cust, bank, start_cash, as_of = load(DATA)
planted = pd.read_csv(DATA / "planted_breaks.csv")
trades = match_trades(internal, broker)


def planted_ids(kind):
    return set(planted[planted.break_type == kind].trade_id)


def found(kind):
    return set(trades[trades.breaks.map(lambda b: kind in b)].trade_id.dropna())


def test_missing_at_broker():
    assert found("MISSING_AT_BROKER") == planted_ids("MISSING_AT_BROKER")


def test_quantity_price_side_settle_commission():
    for planted_kind, kind in [("QUANTITY_BREAK", "QUANTITY"), ("PRICE_BREAK", "PRICE"), ("SIDE_BREAK", "SIDE"),
                               ("SETTLE_DATE_BREAK", "SETTLE_DATE"), ("COMMISSION_BREAK", "COMMISSION")]:
        assert found(kind) == planted_ids(planted_kind), kind


def test_rounding_is_not_a_break():
    ok = trades[trades.trade_id.isin(planted_ids("WITHIN_TOLERANCE"))]
    assert set(ok.status) == {"MATCHED (within tolerance)"}


def test_confirms_without_reference_still_match():
    m = trades[trades.trade_id.isin(planted_ids("NO_REFERENCE"))]
    assert set(m.match_method) == {"details"} and set(m.status) == {"MATCHED"}


def test_one_missing_internal_and_one_duplicate():
    assert (trades.breaks.map(lambda b: "MISSING_INTERNAL" in b)).sum() == 1
    assert (trades.status == "DUPLICATE").sum() == 1


def test_no_false_breaks():
    expected = sum(len(planted_ids(k)) for k in ["MISSING_AT_BROKER", "MISSING_INTERNAL", "QUANTITY_BREAK",
                                                  "PRICE_BREAK", "SIDE_BREAK", "SETTLE_DATE_BREAK", "COMMISSION_BREAK"])
    assert (trades.status == "BREAK").sum() == expected


def test_cash_ties_out_to_the_dividend():
    ours, theirs, unmatched = reconcile_cash(internal, broker, bank, start_cash, as_of)
    explained = trades[trades.status == "BREAK"].cash_impact.sum()
    assert round(theirs - ours - explained, 2) == 4250.00
    assert list(unmatched.description) == ["KO cash dividend"]
