# Trade Reconciliation

A Python and SQL tool that reconciles a fund's trades, positions, and cash against its broker, custodian, and bank, the daily control at the center of middle and back office operations.

The sample data is made up but realistic: one fund account, about 180 US equity trades over September 2026, with 17 problems deliberately planted in it. The tool finds all of them and ties every break back to its cause.

## Why reconciliation matters

Every trade is recorded in at least three places: the fund's own system, the broker that executed it, and the custodian and bank that hold the shares and cash. If those records disagree, the fund's NAV can be wrong, trades can fail to settle, and clients get bad reports. Operations teams reconcile every day to catch problems before they spread.

Where breaks come from in the trade lifecycle:

```
Order → Execution → Booking → Confirmation → Settlement (T+1) → Custody and cash → Reporting
                       │            │              │                   │
                   typos, wrong   missing or    wrong settle       missed corporate
                   side or qty    duplicate     date               actions, unbooked
                                  confirms                         dividends
```

## What it reconciles

**1. Trades: our booking vs the broker's confirm**

Matching runs in two passes, the way ops teams usually do it:
* **By reference:** the broker sends back our client reference
* **By trade details:** when the reference is missing, match on trade date, symbol, side, quantity, and price within $0.005

Matched pairs are compared field by field. Small price rounding differences are flagged as *matched within tolerance*, not breaks.

| Break | What it means |
|---|---|
| QUANTITY, PRICE, SIDE | Booking error on our side or theirs |
| SETTLE_DATE | Wrong settlement date, so cash moves on the wrong day |
| COMMISSION | Fees booked differently |
| MISSING_AT_BROKER | We booked a trade the broker has no record of |
| MISSING_INTERNAL | The broker executed a trade we never booked |
| DUPLICATE_CONFIRM | The broker sent the same confirm twice |

**2. Positions: our holdings vs the custodian's**

Starting positions plus settled trades, compared with the custodian's report. Each position break is tied back to the trade breaks that explain it. Anything left unexplained gets flagged, and the tool spots when the custodian holds a clean multiple of our position, which usually means a stock split we didn't book.

**3. Cash: our cash vs the bank's**

Expected cash from settled trades compared with the bank statement. The difference is split into the part explained by trade breaks and the part that isn't, and bank items that match no trade at all (dividends, fees, interest) are listed separately.

## Results on the sample data

```
TRADE RECONCILIATION (179 internal vs 179 broker)
  MATCHED                     167   (173 by reference, 4 by trade details)
  MATCHED (within tolerance)  3
  BREAK                       10
  DUPLICATE                   1

POSITION RECONCILIATION
  4 of 10 positions match
  5 breaks fully explained by trade breaks
  NVDA: 5,700 shares unexplained, custodian holds exactly 2x → missed 2 for 1 split

CASH RECONCILIATION
  Difference:                 -$884,365.94
  Explained by trade breaks:  -$888,615.94
  Remaining:                  $4,250.00 → KO dividend received at the bank, never booked
```

Every planted problem was found, with no false breaks. The tests in `tests/` check this against the answer key in `data/planted_breaks.csv`.

## Run it

```bash
pip install -r requirements.txt
python run.py              # generate sample data and reconcile
python run.py --no-regen   # reconcile the existing files in data/
python src/run_sql.py      # the same trade and position recon in SQL
python -m pytest tests     # check every planted break is found
```

Reports land in `output/`: `trade_recon.csv`, `position_recon.csv`, and `summary.md`.

## Files

| File | What it is |
|---|---|
| `src/generate_data.py` | Builds the sample files and plants the breaks |
| `src/recon.py` | Matching and reconciliation logic |
| `sql/recon.sql` | The trade and position recon written in SQL |
| `run.py` | Runs everything and prints the report |
| `tests/test_recon.py` | Confirms every planted break is caught |

## Limitations

* US equities only. Bonds, options, futures, and FX each add their own matching rules (accrued interest, multipliers, value dates).
* One account, one broker, one custodian. Real funds reconcile across many.
* Real reconciliation platforms also track break aging, ownership, and resolution notes. This tool stops at finding and explaining breaks.
