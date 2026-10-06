"""Loads the CSVs into an in memory SQLite database and runs sql/recon.sql."""
import sqlite3
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA, SQL = ROOT / "data", ROOT / "sql" / "recon.sql"


def main():
    con = sqlite3.connect(":memory:")
    for name in ["internal_trades", "broker_confirms", "start_positions", "custodian_positions"]:
        pd.read_csv(DATA / f"{name}.csv").to_sql(name, con, index=False)
    pd.options.display.width = 200
    for stmt in SQL.read_text().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        title = next((l.split("name:")[1].strip() for l in stmt.splitlines() if "name:" in l), None)
        if title:
            print(f"\n=== {title} ===")
            print(pd.read_sql(stmt, con).to_string(index=False))
        else:
            con.execute(stmt)


if __name__ == "__main__":
    main()
