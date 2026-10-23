"""04-2 상대강도의 기준 — 지수를 따라가는 ETF의 일봉·1분봉 수집

지수 분봉 API는 당일 것만 주므로, 과거 장중 시장 흐름은 지수 ETF의 1분봉으로 대신한다.

    python -m ch04_candidates.benchmark            # daily_bars에 있는 모든 날짜
"""

import datetime as dt

import pandas as pd

from ch03_pipeline.daily import fetch_daily, to_frame
from ch03_pipeline.db import connect
from ch03_pipeline.minutes import SAFE_AFTER, fetch_minutes
from common.kis import KIS

BENCHMARKS = {"KOSPI": "069500", "KOSDAQ": "229200"}   # KODEX 200, KODEX 코스닥150


def to_minute_frame(date, code, bars):
    return pd.DataFrame([{
        "date": date, "code": code, "time": r["stck_cntg_hour"],
        "open": int(r["stck_oprc"]), "high": int(r["stck_hgpr"]), "low": int(r["stck_lwpr"]),
        "close": int(r["stck_prpr"]), "volume": int(r["cntg_vol"]),
        "acc_value": int(r["acml_tr_pbmn"])} for r in bars])


def main():
    con, kis = connect(), KIS()
    first, last = con.execute("SELECT min(date), max(date) FROM daily_bars").fetchone()
    for market, code in BENCHMARKS.items():
        # 일봉: 종목 일봉과 같은 기간 (전일 종가를 구하려고 하루 앞까지 포함되어 있다)
        df = to_frame(code, fetch_daily(kis, code, first.strftime("%Y%m%d"), dt.date.today().strftime("%Y%m%d")))
        con.execute("INSERT OR REPLACE INTO benchmark_daily SELECT * FROM df")

        # 1분봉: 아직 받지 않은 날짜만
        days = [d for (d,) in con.execute("""
            SELECT DISTINCT date FROM daily_bars
            WHERE date NOT IN (SELECT date FROM benchmark_done WHERE code = ?) ORDER BY date""", [code]).fetchall()]
        if dt.datetime.now().strftime("%H%M%S") < SAFE_AFTER:
            days = [d for d in days if d < dt.date.today()]       # 오늘 분봉은 장이 끝난 뒤에
        for day in days:
            _, _, bars = fetch_minutes(kis, day, code)
            if bars:
                mf = to_minute_frame(day, code, bars)
                con.execute("INSERT OR REPLACE INTO benchmark_minute SELECT * FROM mf")
            con.execute("INSERT OR REPLACE INTO benchmark_done VALUES (?, ?, ?)", [day, code, len(bars)])
        print(f"{market} {code}: 일봉 {len(df)}행, 분봉 {len(days)}일 수집")

    print(con.execute("""
        SELECT code, count(*) AS n_days, count(*) FILTER (WHERE rows > 0) AS with_bars, min(date), max(date)
        FROM benchmark_done GROUP BY code ORDER BY code""").df().to_string(index=False))


if __name__ == "__main__":
    main()
