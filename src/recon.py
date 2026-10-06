"""Trade, position, and cash reconciliation.

Trade matching runs in two passes, the way most ops teams do it:
  1. Match on our client reference when the broker sends it back
  2. For anything left over, match on trade details (date, symbol, side, quantity, price within tolerance)
Matched pairs are then compared field by field to find breaks.
"""
import pandas as pd

PRICE_TOL = 0.005    # dollars per share; rounding differences below this are not breaks
MONEY_TOL = 0.01     # dollars


def load(data_dir):
    p = lambda f: data_dir / f
    internal = pd.read_csv(p("internal_trades.csv"), parse_dates=["trade_date", "settle_date"])
    broker = pd.read_csv(p("broker_confirms.csv"), parse_dates=["trade_date", "settle_date"])
    start = pd.read_csv(p("start_positions.csv"))
    cust = pd.read_csv(p("custodian_positions.csv"))
    bank = pd.read_csv(p("bank_statement.csv"), parse_dates=["date"])
    meta = dict(line.split("=") for line in p("meta.txt").read_text().split())
    return internal, broker, start, cust, bank, float(meta["start_cash"]), pd.Timestamp(meta["as_of"])


def match_trades(internal, broker):
    """Returns one row per internal trade or broker confirm, with a status and break list."""
    b = broker.copy()
    # Duplicate confirms: same client ref or same details, keep the first
    key = ["client_ref", "trade_date", "symbol", "side", "quantity", "price"]
    dup_mask = b.duplicated(subset=key, keep="first")
    dups = b[dup_mask]
    b = b[~dup_mask]

    # Pass 1: client reference
    m1 = internal.merge(b.dropna(subset=["client_ref"]), on="client_ref", suffixes=("", "_brk"))
    m1["match_method"] = "reference"
    used_int, used_brk = set(m1.trade_id), set(m1.broker_ref)

    # Pass 2: trade details, for leftovers
    li = internal[~internal.trade_id.isin(used_int)]
    lb = b[~b.broker_ref.isin(used_brk)]
    cand = li.merge(lb, on=["trade_date", "symbol", "side", "quantity"], suffixes=("", "_brk"))
    cand = cand[(cand.price - cand.price_brk).abs() <= PRICE_TOL]
    cand = cand.drop_duplicates("trade_id").drop_duplicates("broker_ref")
    for c in ["trade_date", "symbol", "side", "quantity"]:
        cand[c + "_brk"] = cand[c]
    cand["match_method"] = "details"
    matched = pd.concat([m1, cand], ignore_index=True)
    used_int |= set(cand.trade_id)
    used_brk |= set(cand.broker_ref)

    # Compare matched pairs field by field
    def breaks(r):
        out = []
        if r.side != r.side_brk: out.append("SIDE")
        if r.quantity != r.quantity_brk: out.append("QUANTITY")
        if abs(r.price - r.price_brk) > PRICE_TOL: out.append("PRICE")
        if r.settle_date != r.settle_date_brk: out.append("SETTLE_DATE")
        if abs(r.commission - r.commission_brk) > MONEY_TOL: out.append("COMMISSION")
        if abs(r.net_amount - r.net_amount_brk) > MONEY_TOL: out.append("NET_AMOUNT")
        return out

    matched["breaks"] = matched.apply(breaks, axis=1)
    matched["status"] = matched.apply(
        lambda r: "BREAK" if r.breaks else ("MATCHED (within tolerance)" if r.price != r.price_brk else "MATCHED"), axis=1)
    matched["cash_impact"] = matched.net_amount_brk - matched.net_amount   # broker (truth) minus ours

    cols = ["status", "breaks", "match_method", "trade_id", "broker_ref", "client_ref", "trade_date", "symbol",
            "side", "side_brk", "quantity", "quantity_brk", "price", "price_brk", "settle_date", "settle_date_brk",
            "commission", "commission_brk", "net_amount", "net_amount_brk", "cash_impact"]
    out = [matched[cols]]

    miss_b = internal[~internal.trade_id.isin(used_int)].assign(
        status="BREAK", breaks=[["MISSING_AT_BROKER"]] * (~internal.trade_id.isin(used_int)).sum(),
        match_method="", cash_impact=lambda d: -d.net_amount)
    miss_i = b[~b.broker_ref.isin(used_brk)].rename(columns={c: c + "_brk" for c in ["side", "quantity", "price", "settle_date", "commission", "net_amount"]})
    miss_i = miss_i.assign(status="BREAK", breaks=[["MISSING_INTERNAL"]] * len(miss_i), match_method="",
                           cash_impact=miss_i.net_amount_brk)
    dups = dups.rename(columns={c: c + "_brk" for c in ["side", "quantity", "price", "settle_date", "commission", "net_amount"]})
    dup = dups.assign(status="DUPLICATE", breaks=[["DUPLICATE_CONFIRM"]] * len(dups), match_method="", cash_impact=0.0)
    out += [miss_b, miss_i, dup]
    result = pd.concat(out, ignore_index=True)
    return result.reindex(columns=cols)


def reconcile_positions(internal, start, cust, as_of):
    settled = internal[internal.settle_date <= as_of]
    signed = settled.assign(q=settled.quantity.where(settled.side == "BUY", -settled.quantity)).groupby("symbol").q.sum()
    ours = start.set_index("symbol").quantity.add(signed, fill_value=0).astype(int)
    theirs = cust.set_index("symbol").quantity
    df = pd.DataFrame({"internal": ours, "custodian": theirs}).fillna(0).astype(int)
    df["difference"] = df.custodian - df.internal
    df["status"] = df.difference.map(lambda x: "MATCHED" if x == 0 else "BREAK")
    return df.reset_index().rename(columns={"index": "symbol"})


def reconcile_cash(internal, broker, bank, start_cash, as_of):
    """Returns internal cash, bank cash, and bank items that aren't trade settlements at all."""
    settled = internal[internal.settle_date <= as_of]
    ours = start_cash + settled.net_amount.sum()
    theirs = start_cash + bank[bank.date <= as_of].amount.sum()
    # Bank items that match no broker settlement are non trade cash (dividends, fees, interest)
    street = broker.drop_duplicates(subset=["client_ref", "trade_date", "symbol", "side", "quantity", "price"])
    exp = street.assign(net_amount=street.net_amount.round(2)).groupby(["settle_date", "net_amount"]).size()
    unmatched = []
    seen = {}
    for r in bank.itertuples():
        k = (r.date, round(r.amount, 2))
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > exp.get(k, 0):
            unmatched.append(r)
    return ours, theirs, pd.DataFrame(unmatched).drop(columns="Index", errors="ignore")


def explain_positions(pos, trades):
    """Tie each position break back to the trade breaks that explain it."""
    notes = {}
    for sym in pos[pos.status == "BREAK"].symbol:
        t = trades[(trades.symbol == sym) & (trades.status != "MATCHED")]
        expected = 0
        for r in t.itertuples():
            b = r.breaks
            sign_b = 1 if r.side_brk == "BUY" else -1
            sign_i = 1 if r.side == "BUY" else -1
            if "MISSING_AT_BROKER" in b: expected -= sign_i * r.quantity
            elif "MISSING_INTERNAL" in b: expected += sign_b * r.quantity_brk
            elif "QUANTITY" in b or "SIDE" in b: expected += sign_b * r.quantity_brk - sign_i * r.quantity
        notes[sym] = expected
    return notes
