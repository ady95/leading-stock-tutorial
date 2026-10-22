"""03-5 재현용 샘플 데이터를 DuckDB로 불러오기

    python -m ch03_pipeline.load_sample                 # data/leading_stock.duckdb 에 불러온다
    python -m ch03_pipeline.load_sample --replace       # 이미 데이터가 있으면 지우고 다시
"""

import argparse
from pathlib import Path

from ch03_pipeline.db import DB_PATH, ROOT, connect

SAMPLE_DIR = ROOT / "sample_data"
TABLES = ["stock_master", "daily_bars", "minute_bars"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--replace", action="store_true", help="기존 데이터를 지우고 불러온다")
    args = ap.parse_args()

    con = connect(args.db)
    existing = con.execute("SELECT count(*) FROM minute_bars").fetchone()[0]
    if existing and not args.replace:
        raise SystemExit(f"{args.db}에 이미 분봉 {existing:,}행이 있습니다. 덮어쓰려면 --replace를 붙이세요.")

    for table in TABLES + ["daily_done", "minute_done", "stock_master_history"]:
        con.execute(f"DELETE FROM {table}")
    for table in TABLES:
        path = (SAMPLE_DIR / f"{table}.parquet").as_posix()
        con.execute(f"INSERT INTO {table} SELECT * FROM read_parquet('{path}')")

    # 수집 스크립트가 이어서 받을 수 있도록 완료 표시도 채운다
    con.execute("INSERT INTO daily_done SELECT code, count(*) FROM daily_bars GROUP BY code")
    con.execute("INSERT INTO minute_done SELECT date, code, count(*) FROM minute_bars GROUP BY date, code")
    con.execute("""INSERT INTO stock_master_history
        SELECT updated, code, name, market, group_code, is_spac, is_halted FROM stock_master""")

    print(con.execute("""
        SELECT 'daily_bars' AS tbl, count(*) AS n_rows, min(date) AS first_day, max(date) AS last_day FROM daily_bars
        UNION ALL
        SELECT 'minute_bars', count(*), min(date), max(date) FROM minute_bars""").df().to_string(index=False))


if __name__ == "__main__":
    main()
