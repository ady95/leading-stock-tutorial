"""03-2 과거 1분봉 수집 — 날짜별 거래대금 상위 N종목의 정규장 1분봉을 모은다.

    python -m ch03_pipeline.minutes --top 300             # daily_bars가 있는 모든 날짜
    python -m ch03_pipeline.minutes --top 3 --days 2      # 최근 2일 × 상위 3종목 (시험용)
"""

import argparse
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ch03_pipeline.db import connect
from common.kis import KIS

PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
OPEN, CLOSE = "090000", "153000"
# 정규장 시간이 다른 날 (수능일은 개장·마감이 1시간 늦고, 새해 첫 거래일은 10시 개장). 해마다 확인해 추가한다
SESSIONS = {"20251113": ("100000", "163000"), "20260102": ("100000", "153000")}


def fetch_minutes(kis, date, code):
    """장 마감 시각부터 거꾸로 120행씩 받으며, 그 날짜의 개장 봉에 닿으면 멈춘다."""
    day = date.strftime("%Y%m%d")
    open_t, close_t = SESSIONS.get(day, (OPEN, CLOSE))
    hour, bars = close_t, {}
    for _ in range(8):
        body = kis.get(PATH, "FHKST03010230", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": hour, "FID_INPUT_DATE_1": day,
            "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": ""})
        # 120행이 다 차면 전날 봉까지 섞여 오므로 그 날짜만 남긴다.
        # 데이터가 없는 날짜는 오류 대신 빈 행 하나가 오므로 여기서 걸러진다.
        page = [r for r in body["output2"] if r.get("stck_bsop_date") == day]
        if not page:
            break
        for r in page:
            bars[r["stck_cntg_hour"]] = r
        earliest = min(r["stck_cntg_hour"] for r in page)
        if earliest <= open_t:
            break
        hour = (dt.datetime.strptime(earliest, "%H%M%S") - dt.timedelta(minutes=1)).strftime("%H%M%S")
    return date, code, [r for t, r in sorted(bars.items()) if open_t <= t <= close_t]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=300, help="날짜별 거래대금 상위 N종목")
    ap.add_argument("--days", type=int, help="최근 N거래일만")
    args = ap.parse_args()

    con, kis = connect(), KIS()
    targets = con.execute("""
        WITH ranked AS (
            SELECT date, code, row_number() OVER (PARTITION BY date ORDER BY value DESC) AS rk
            FROM daily_bars WHERE value > 0),
        days AS (
            SELECT DISTINCT date FROM daily_bars ORDER BY date DESC LIMIT ?)
        SELECT r.date, r.code FROM ranked r
        LEFT JOIN minute_done d USING (date, code)
        WHERE r.rk <= ? AND d.code IS NULL AND r.date IN (SELECT date FROM days)
        ORDER BY r.date, r.rk""", [args.days or 100000, args.top]).fetchall()
    print(f"수집 대상 {len(targets)}건 (날짜 × 종목, {kis.env})")

    workers = 8 if kis.env == "real" else 1
    with ThreadPoolExecutor(workers) as ex:
        for date, code, bars in ex.map(lambda t: fetch_minutes(kis, *t), targets):
            if bars:
                df = pd.DataFrame([{
                    "date": date, "code": code, "time": r["stck_cntg_hour"],
                    "open": int(r["stck_oprc"]), "high": int(r["stck_hgpr"]),
                    "low": int(r["stck_lwpr"]), "close": int(r["stck_prpr"]),
                    "volume": int(r["cntg_vol"]), "acc_value": int(r["acml_tr_pbmn"])} for r in bars])
                con.execute("INSERT OR REPLACE INTO minute_bars SELECT * FROM df")
            con.execute("INSERT OR REPLACE INTO minute_done VALUES (?, ?, ?)", [date, code, len(bars)])
            print(f"  {date} {code}: {len(bars)}봉")


if __name__ == "__main__":
    main()
