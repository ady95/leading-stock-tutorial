"""07-1 장중 리플레이 — 저장된 1분봉으로 하루를 5분 간격으로 다시 재생한다.

시점 t마다 04장 지표와 6장 Leader Score를 계산한다. t보다 앞선 봉만 쓰는 규칙(03-4)은 그대로다.

    python -m ch07_realtime.replay --date 2026-10-21
"""

import argparse
import datetime as dt

import pandas as pd

from ch03_pipeline.db import DB_PATH, connect
from ch03_pipeline.minutes import CLOSE, OPEN, SESSIONS
from ch04_candidates.features import BASELINE_DAYS, MIN_BAR_RATIO, MIN_DAY_VALUE, MIN_PRICE
from ch06_leader_score.score import add_intraday, load_themes, score_frame

STEP_MINUTES = 5

# 시각 grid의 t마다, 종목별로 t보다 앞선 마지막 봉의 누적값을 ASOF 조인으로 붙인다
GRID_SQL = """
WITH bars AS (
    SELECT code, time, close AS price, acc_value, high, low,
           sum(volume) OVER w AS acc_volume,
           max(high)   OVER w AS day_high,
           count(*)    OVER w AS n_bars
    FROM minute_bars WHERE date = $date
    WINDOW w AS (PARTITION BY code ORDER BY time ROWS UNBOUNDED PRECEDING)),
grid AS (
    SELECT c.code, g.t FROM (SELECT DISTINCT code FROM bars) c CROSS JOIN (SELECT unnest($grid) AS t) g)
SELECT g.t, b.code, b.price, b.acc_value, b.acc_volume, b.day_high, b.n_bars
FROM grid g ASOF JOIN bars b ON g.code = b.code AND g.t > b.time
"""

# 지난 20거래일의 시간대 비중: grid의 t마다 "하루 거래대금 중 t까지 거래된 비율"의 중앙값
FRAC_SQL = """
WITH past AS (SELECT DISTINCT date FROM minute_bars WHERE date < $date ORDER BY date DESC LIMIT $n),
bars AS (SELECT date, code, time, acc_value,
                max(acc_value) OVER (PARTITION BY date, code) AS day_total
         FROM minute_bars WHERE date IN (SELECT date FROM past)),
grid AS (SELECT d.date, d.code, g.t
         FROM (SELECT DISTINCT date, code FROM bars) d CROSS JOIN (SELECT unnest($grid) AS t) g)
SELECT g.t, median(b.acc_value / b.day_total) AS frac_t
FROM grid g ASOF JOIN bars b ON g.date = b.date AND g.code = b.code AND g.t > b.time
WHERE b.day_total > 0 GROUP BY g.t
"""

HIST_SQL = """
SELECT code, arg_max(close, date) AS prev_close, median(value) FILTER (WHERE date >= $base_from) AS med_value
FROM daily_bars WHERE date < $date GROUP BY code
"""

BENCH_SQL = """
WITH bars AS (SELECT code, time, close FROM benchmark_minute WHERE date = $date),
grid AS (SELECT c.code, g.t FROM (SELECT DISTINCT code FROM bars) c CROSS JOIN (SELECT unnest($grid) AS t) g),
prev AS (SELECT code, arg_max(close, date) AS prev FROM benchmark_daily WHERE date < $date GROUP BY code)
SELECT g.t, CASE g.code WHEN '069500' THEN 'KOSPI' ELSE 'KOSDAQ' END AS market, b.close / p.prev - 1 AS bench_ret
FROM grid g ASOF JOIN bars b ON g.code = b.code AND g.t > b.time JOIN prev p ON p.code = g.code
"""


def time_grid(date, step=STEP_MINUTES):
    """개장 step분 뒤부터 장 마감 동시호가 직전까지 step분 간격의 시각들 (HHMMSS)."""
    open_t, close_t = SESSIONS.get(date.replace("-", ""), (OPEN, CLOSE))
    start = dt.datetime.strptime(open_t, "%H%M%S")
    end = dt.datetime.strptime(close_t, "%H%M%S") - dt.timedelta(minutes=10)   # 15:20 (동시호가 시작)
    times, t = [], start + dt.timedelta(minutes=step)
    while t <= end:
        times.append(t.strftime("%H%M%S"))
        t += dt.timedelta(minutes=step)
    return times, start


def day_indicators(con, date, step=STEP_MINUTES):
    """하루의 모든 grid 시점에 대해 04장 지표와 유동성 필터를 한 번에 계산한다."""
    d = dt.date.fromisoformat(date)
    grid, start = time_grid(date, step)
    base_from = con.execute(
        "SELECT min(date) FROM (SELECT DISTINCT date FROM daily_bars WHERE date < ? ORDER BY date DESC LIMIT ?)",
        [d, BASELINE_DAYS]).fetchone()[0]
    now = con.execute(GRID_SQL, {"date": d, "grid": grid}).df()
    frac = con.execute(FRAC_SQL, {"date": d, "grid": grid, "n": BASELINE_DAYS}).df()
    hist = con.execute(HIST_SQL, {"date": d, "base_from": base_from}).df()
    bench = con.execute(BENCH_SQL, {"date": d, "grid": grid}).df()
    master = con.execute("SELECT code, name, market FROM stock_master").df()

    x = now.merge(hist, on="code").merge(master, on="code").merge(frac, on="t", how="left")
    x = x.merge(bench, on=["t", "market"], how="left")
    x = x[x.prev_close > 0]
    x["ret"] = x.price / x.prev_close - 1
    x["value_share"] = x.acc_value / x.groupby("t").acc_value.transform("sum")
    x["value_surge"] = x.acc_value / (x.med_value.where(x.med_value > 0) * x.frac_t)
    x["rel_strength"] = x.ret - x.bench_ret
    x["is_pref"] = x.code.str[-1] != "0"

    minutes = x.t.map(lambda t: (dt.datetime.strptime(t, "%H%M%S") - start).seconds // 60)
    x["is_candidate"] = (~x.is_pref & (x.price >= MIN_PRICE) & (x.n_bars >= MIN_BAR_RATIO * minutes)
                         & (x.acc_value >= MIN_DAY_VALUE * x.frac_t))
    return add_intraday(x)


def replay_day(con, date, step=STEP_MINUTES, themes=None):
    """grid 시점마다 Leader Score를 계산해 하나의 표로 돌려준다."""
    themes = load_themes(con) if themes is None else themes
    x = day_indicators(con, date, step)
    frames = []
    for t, g in x[x.is_candidate].groupby("t"):
        scored, _ = score_frame(g, themes)
        scored["rank"] = range(1, len(scored) + 1)
        frames.append(scored)
    return pd.concat(frames, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--step", type=int, default=STEP_MINUTES)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    r = replay_day(connect(args.db, read_only=True), args.date, args.step)
    top1 = r[r["rank"] == 1].sort_values("t")
    print(f"{args.date} {args.step}분 간격 리플레이 — 시점 {top1.t.nunique()}개\n")
    print("시각   1위 종목          점수   등락률   주도 테마")
    for row in top1.itertuples():
        theme = row.theme_name if row.theme_score > 0 else "-"
        print(f"{row.t[:2]}:{row.t[2:4]}  {row.name:<14} {row.leader_score:.3f} {row.ret * 100:+6.2f}%  {theme}")


if __name__ == "__main__":
    main()
