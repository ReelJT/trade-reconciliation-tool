"""Creates realistic sample files for one fund account, with known breaks planted in them.

Files written to data/:
  internal_trades.csv     what the fund booked in its own system (like an OMS / IBOR)
  broker_confirms.csv     what the executing broker says happened
  start_positions.csv     holdings at the start of the period (agreed by both sides)
  custodian_positions.csv holdings the custodian reports at the end of the period
  bank_statement.csv      cash movements the bank reports
  planted_breaks.csv      the answer key: every break deliberately put in the data
"""
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
SYMBOLS = {"AAPL": 230, "MSFT": 430, "NVDA": 120, "JPM": 210, "XOM": 115,
           "JNJ": 160, "AMZN": 185, "KO": 68, "BAC": 42, "CAT": 360}
START, END = pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-30")
START_CASH = 50_000_000.00
COMM_PER_SHARE = 0.01


def settle(d):                       # US equities settle T+1 business day
    return d + pd.offsets.BDay(1)


def net(side, qty, price, comm):    # cash impact: buys cost money, sells bring it in
    gross = qty * price
    return round(-(gross + comm) if side == "BUY" else gross - comm, 2)


def make(seed=11):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(START, END - pd.offsets.BDay(1))
    rows = []
    for i in range(180):
        sym = rng.choice(list(SYMBOLS))
        side = rng.choice(["BUY", "SELL"], p=[0.55, 0.45])
        qty = int(rng.integers(1, 40)) * 100
        px = round(SYMBOLS[sym] * (1 + rng.normal(0, 0.02)), 4)
        d = rng.choice(days)
        rows.append(dict(trade_id=f"T{i+1:04d}", client_ref=f"CR{i+1:05d}", trade_date=d,
                         settle_date=settle(d), account="FUND01", symbol=sym, side=side,
                         quantity=qty, price=px, commission=round(qty * COMM_PER_SHARE, 2)))
    truth = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
    truth["net_amount"] = [net(*r) for r in truth[["side", "quantity", "price", "commission"]].itertuples(index=False)]

    internal = truth.copy()
    broker = truth.copy()
    broker["broker_ref"] = [f"BRK{9000+i}" for i in range(len(broker))]
    planted = []

    def plant(kind, tid, note):
        planted.append(dict(break_type=kind, trade_id=tid, note=note))

    idx = rng.permutation(len(truth))
    take = iter(idx)

    # 1. Broker never confirmed two trades (fund booked them, broker has no record)
    for _ in range(2):
        i = next(take); tid = truth.at[i, "trade_id"]
        broker = broker[broker.trade_id != tid]; plant("MISSING_AT_BROKER", tid, "No broker confirm received")
    # 2. Fund never booked a trade the broker did execute
    i = next(take); tid = truth.at[i, "trade_id"]
    internal = internal[internal.trade_id != tid]
    broker.loc[broker.trade_id == tid, "client_ref"] = None
    plant("MISSING_INTERNAL", tid, "Executed at broker but never booked internally")
    # 3. Quantity typos in the fund's booking
    for _ in range(2):
        i = next(take); tid = truth.at[i, "trade_id"]
        internal.loc[internal.trade_id == tid, "quantity"] += 100
        plant("QUANTITY_BREAK", tid, "Booked 100 shares too many internally")
    # 4. Real price breaks, and harmless rounding differences
    for _ in range(2):
        i = next(take); tid = truth.at[i, "trade_id"]
        internal.loc[internal.trade_id == tid, "price"] = round(truth.at[i, "price"] + 0.25, 4)
        plant("PRICE_BREAK", tid, "Internal price off by $0.25")
    for _ in range(3):
        i = next(take); tid = truth.at[i, "trade_id"]
        broker.loc[broker.trade_id == tid, "price"] = round(truth.at[i, "price"], 2)
        plant("WITHIN_TOLERANCE", tid, "Broker rounded price to 2 decimals (should NOT be a break)")
    # 5. Side booked backwards
    i = next(take); tid = truth.at[i, "trade_id"]
    internal.loc[internal.trade_id == tid, "side"] = "SELL" if truth.at[i, "side"] == "BUY" else "BUY"
    plant("SIDE_BREAK", tid, "Booked as the wrong side internally")
    # 6. Wrong settlement date
    i = next(take); tid = truth.at[i, "trade_id"]
    internal.loc[internal.trade_id == tid, "settle_date"] = truth.at[i, "settle_date"] + pd.offsets.BDay(1)
    plant("SETTLE_DATE_BREAK", tid, "Internal settle date is T+2 instead of T+1")
    # 7. Commission booked wrong
    i = next(take); tid = truth.at[i, "trade_id"]
    internal.loc[internal.trade_id == tid, "commission"] = round(truth.at[i, "commission"] * 2, 2)
    plant("COMMISSION_BREAK", tid, "Commission double counted internally")
    # 8. Broker sends a confirm without our reference (must match on trade details)
    for _ in range(4):
        i = next(take); tid = truth.at[i, "trade_id"]
        broker.loc[broker.trade_id == tid, "client_ref"] = None
        plant("NO_REFERENCE", tid, "Broker confirm missing client ref (should still match)")
    # 9. Broker sends the same confirm twice
    i = next(take); tid = truth.at[i, "trade_id"]
    dup = broker[broker.trade_id == tid].copy(); dup["broker_ref"] = "BRK_DUP1"
    broker = pd.concat([broker, dup])
    plant("DUPLICATE_CONFIRM", tid, "Same confirm received twice")

    # Recompute the fund's net amounts after its booking errors. The broker's net amounts stay
    # as executed (a rounded display price doesn't change the cash that actually moved).
    internal["net_amount"] = [net(*r) for r in internal[["side", "quantity", "price", "commission"]].itertuples(index=False)]

    # What actually happened in the market: every trade except the ones the broker never executed
    missing = {p["trade_id"] for p in planted if p["break_type"] == "MISSING_AT_BROKER"}
    street = truth[~truth.trade_id.isin(missing)]

    # Starting positions, agreed by everyone
    start_pos = pd.DataFrame({"symbol": list(SYMBOLS), "quantity": [int(rng.integers(50, 200)) * 100 for _ in SYMBOLS]})

    # Custodian holds what really happened: true trades + a corporate action the fund missed
    settled = street[street.settle_date <= END]
    signed = settled.assign(q=np.where(settled.side == "BUY", settled.quantity, -settled.quantity)).groupby("symbol").q.sum()
    cust = start_pos.set_index("symbol").quantity.add(signed, fill_value=0)
    split_sym = "NVDA"
    cust[split_sym] = cust[split_sym] * 2      # 2 for 1 split effective 2026-09-22, not booked internally
    plant("CORPORATE_ACTION", "", f"{split_sym} 2 for 1 split not booked internally")
    cust = cust.astype(int).reset_index().rename(columns={"index": "symbol"})
    cust.columns = ["symbol", "quantity"]

    # Bank: true settled cash + a dividend the fund didn't book
    bank = settled[["settle_date", "trade_id", "symbol", "side", "net_amount"]].copy()
    bank["description"] = bank.apply(lambda r: f"{r.side} {r.symbol} settlement", axis=1)
    bank = bank.rename(columns={"settle_date": "date", "net_amount": "amount"})[["date", "description", "amount"]]
    div = pd.DataFrame([{"date": pd.Timestamp("2026-09-15"), "description": "KO cash dividend", "amount": 4_250.00}])
    bank = pd.concat([bank, div]).sort_values("date")
    plant("UNBOOKED_CASH", "", "KO dividend of $4,250.00 received at bank, not booked internally")

    DATA.mkdir(exist_ok=True)
    cols = ["trade_id", "client_ref", "trade_date", "settle_date", "account", "symbol", "side", "quantity", "price", "commission", "net_amount"]
    internal[cols].to_csv(DATA / "internal_trades.csv", index=False, date_format="%Y-%m-%d")
    bcols = ["broker_ref", "client_ref", "trade_date", "settle_date", "symbol", "side", "quantity", "price", "commission", "net_amount"]
    broker.sample(frac=1, random_state=1)[bcols].to_csv(DATA / "broker_confirms.csv", index=False, date_format="%Y-%m-%d")
    start_pos.to_csv(DATA / "start_positions.csv", index=False)
    cust.to_csv(DATA / "custodian_positions.csv", index=False)
    bank.to_csv(DATA / "bank_statement.csv", index=False, date_format="%Y-%m-%d")
    pd.DataFrame(planted).to_csv(DATA / "planted_breaks.csv", index=False)
    (DATA / "meta.txt").write_text(f"start_cash={START_CASH}\nas_of={END.date()}\n")
    print(f"Wrote sample data to {DATA}/ ({len(internal)} internal trades, {len(broker)} broker confirms)")


if __name__ == "__main__":
    make()
