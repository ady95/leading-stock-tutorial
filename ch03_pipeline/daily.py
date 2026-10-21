"""03-2 일봉 수집 — 주권(ST) 종목의 일봉을 기간만큼 모은다. 중단해도 이어서 받는다.

    python -m ch03_pipeline.daily --start 20251001             # 전 종목
    python -m ch03_pipeline.daily --start 20251001 --limit 5   # 앞 5종목만 (모의투자 시험용)
"""

import argparse
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ch03_pipeline.db import connect
from common.kis import KIS

PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"


def fetch_daily(kis, code, start, end):
    """일봉 API는 한 번에 100행까지 주므로, 가장 오래된 날짜 앞으로 끝 날짜를 당기며 반복한다."""
    rows = []
    while True:
        body = kis.get(PATH, "FHKST03010100", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
        page = [r for r in body["output2"] if r.get("stck_bsop_date")]
        rows += page
        if len(page) < 100:
            return rows
        oldest = dt.datetime.strptime(page[-1]["stck_bsop_date"], "%Y%m%d").date()
        end = (oldest - dt.timedelta(days=1)).strftime("%Y%m%d")
        if end < start:
            return rows


def to_frame(code, rows):
    return pd.DataFrame([{
        "date": dt.datetime.strptime(r["stck_bsop_date"], "%Y%m%d").date(), "code": code,
        "open": int(r["stck_oprc"]), "high": int(r["stck_hgpr"]), "low": int(r["stck_lwpr"]),
        "close": int(r["stck_clpr"]), "volume": int(r["acml_vol"]),
        "value": int(r["acml_tr_pbmn"])} for r in rows]).drop_duplicates(["date", "code"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="시작일 YYYYMMDD")
    ap.add_argument("--end", default=dt.date.today().strftime("%Y%m%d"))
    ap.add_argument("--limit", type=int, help="앞에서부터 N종목만")
    args = ap.parse_args()

    con, kis = connect(), KIS()
    codes = [c for (c,) in con.execute("""
        SELECT code FROM stock_master
        WHERE group_code = 'ST' AND NOT is_spac
          AND code NOT IN (SELECT code FROM daily_done)
        ORDER BY code""").fetchall()][:args.limit]
    print(f"수집 대상 {len(codes)}종목 ({kis.env})")

    workers = 8 if kis.env == "real" else 1
    with ThreadPoolExecutor(workers) as ex:
        jobs = ex.map(lambda c: (c, fetch_daily(kis, c, args.start, args.end)), codes)
        for i, (code, rows) in enumerate(jobs, 1):
            if rows:
                df = to_frame(code, rows)
                con.execute("INSERT OR REPLACE INTO daily_bars SELECT * FROM df")
            con.execute("INSERT OR REPLACE INTO daily_done VALUES (?, ?)", [code, len(rows)])
            if i % 100 == 0 or i == len(codes):
                print(f"  {i}/{len(codes)}")

    print(con.execute(
        "SELECT count(*) AS rows, count(DISTINCT code) AS codes, min(date), max(date) FROM daily_bars"
    ).df().to_string(index=False))


if __name__ == "__main__":
    main()
