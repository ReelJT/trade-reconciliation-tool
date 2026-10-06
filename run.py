"""Run the full reconciliation and write the reports.

    python run.py              # regenerate sample data and reconcile
    python run.py --no-regen   # reconcile existing files in data/
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
import pandas as pd

from generate_data import make, DATA
from recon import load, match_trades, reconcile_positions, reconcile_cash, explain_positions

OUT = Path(__file__).parent / "output"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-regen", action="store_true")
    a = p.parse_args()
    if not a.no_regen:
        make()
    OUT.mkdir(exist_ok=True)
    pd.options.display.width = 220
    pd.options.display.max_columns = 20

    internal, broker, start, cust, bank, start_cash, as_of = load(DATA)

    # 1. Trades
    trades = match_trades(internal, broker)
    trades.to_csv(OUT / "trade_recon.csv", index=False)
    counts = trades.status.value_counts()
    print(f"\n=== TRADE RECONCILIATION ({len(internal)} internal vs {len(broker)} broker) ===")
    for s, n in counts.items():
        print(f"  {s:<28}{n}")
    by_method = trades[trades.match_method != ""].match_method.value_counts()
    print(f"  matched by reference: {by_method.get('reference', 0)}, by trade details: {by_method.get('details', 0)}")
    brk = trades[trades.status != "MATCHED"].copy()
    brk = brk[brk.status != "MATCHED (within tolerance)"]
    brk["breaks"] = brk.breaks.map(", ".join)
    for c in ["quantity", "quantity_brk"]:
        brk[c] = brk[c].astype("Int64")
    print("\nBreaks to investigate:")
    print(brk[["breaks", "trade_id", "broker_ref", "symbol", "side", "side_brk", "quantity", "quantity_brk",
               "price", "price_brk", "cash_impact"]].to_string(index=False))

    # 2. Positions
    pos = reconcile_positions(internal, start, cust, as_of)
    pos.to_csv(OUT / "position_recon.csv", index=False)
    print(f"\n=== POSITION RECONCILIATION as of {as_of.date()} ===")
    expl = explain_positions(pos, trades)
    pb = pos[pos.status == "BREAK"].copy()
    pb["explained_by_trade_breaks"] = pb.symbol.map(expl)
    pb["explained_by_trade_breaks"] = pb.explained_by_trade_breaks.astype(int)
    pb["unexplained"] = pb.difference - pb.explained_by_trade_breaks
    print(f"  {len(pos) - len(pb)} of {len(pos)} positions match")
    print(pb.to_string(index=False))
    for r in pb[pb.unexplained != 0].itertuples():
        ratio = r.custodian / (r.internal + r.explained_by_trade_breaks)
        hint = f" (custodian holds {ratio:.2f}x, looks like a {ratio:.0f} for 1 split)" if abs(ratio - round(ratio)) < 0.01 and round(ratio) > 1 else ""
        print(f"  -> {r.symbol}: {r.unexplained:,} shares not explained by trade breaks{hint}")

    # 3. Cash
    ours, theirs, unmatched = reconcile_cash(internal, broker, bank, start_cash, as_of)
    trade_cash = trades[trades.status == "BREAK"].cash_impact.sum()
    print(f"\n=== CASH RECONCILIATION as of {as_of.date()} ===")
    print(f"  Internal cash:   ${ours:,.2f}")
    print(f"  Bank cash:       ${theirs:,.2f}")
    print(f"  Difference:      ${theirs - ours:,.2f}")
    print(f"  Explained by trade breaks: ${trade_cash:,.2f}")
    print(f"  Remaining:       ${theirs - ours - trade_cash:,.2f}")
    if len(unmatched):
        print("  Non trade cash at the bank, not booked internally:")
        print(unmatched.to_string(index=False))

    # Summary file
    summary = OUT / "summary.md"
    summary.write_text(
        f"# Reconciliation summary, {as_of.date()}\n\n"
        f"* Trades: {counts.get('MATCHED', 0) + counts.get('MATCHED (within tolerance)', 0)} matched, "
        f"{counts.get('BREAK', 0)} breaks, {counts.get('DUPLICATE', 0)} duplicates\n"
        f"* Positions: {len(pos) - len(pb)} of {len(pos)} match\n"
        f"* Cash difference: ${theirs - ours:,.2f} (${trade_cash:,.2f} from trade breaks)\n")
    print(f"\nReports saved to {OUT}/")


if __name__ == "__main__":
    main()
