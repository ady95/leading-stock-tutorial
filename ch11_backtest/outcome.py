"""11-2 선정 이후 결과 — 판단 시각 t에 사서 30분 뒤 판다고 할 때의 수익률과 MFE·MAE.

진입가는 t보다 앞선 마지막 봉의 종가, 청산가는 t+30분보다 앞선 마지막 봉의 종가다 (8장 정답과 같은 규칙).
MFE·MAE는 보유 구간(t 이상 ~ t+30분 미만)의 봉 고가·저가로 잰다.

    from ch11_backtest.outcome import add_outcome
    df = add_outcome(con, df)      # df: date, t, code 열
"""

import datetime as dt

import pandas as pd

HOLD_MIN = 30

OUTCOME_SQL = """
WITH r AS (SELECT DISTINCT date::DATE AS date, t, t_end, code FROM rows),
bars AS (
    SELECT m.date, m.code, m.time, m.close, m.high, m.low FROM minute_bars m
    WHERE m.date IN (SELECT DISTINCT date FROM r) AND m.code IN (SELECT DISTINCT code FROM r)),
entry AS (SELECT r.*, b.close AS entry FROM r ASOF JOIN bars b
          ON r.date = b.date AND r.code = b.code AND r.t > b.time),
exit AS (SELECT r.date, r.t, r.code, b.close AS exit_price FROM r ASOF JOIN bars b
         ON r.date = b.date AND r.code = b.code AND r.t_end > b.time),
hold AS (SELECT r.date, r.t, r.code, max(b.high) AS hi, min(b.low) AS lo
         FROM r JOIN bars b ON r.date = b.date AND r.code = b.code AND b.time >= r.t AND b.time < r.t_end
         GROUP BY ALL)
SELECT e.date, e.t, e.code, e.entry, x.exit_price,
       x.exit_price / e.entry - 1 AS ret30,          -- 30분 보유 수익률 (시장 대비 아님)
       h.hi / e.entry - 1 AS mfe30,                   -- 보유 중 가장 유리했던 순간 (최대 순행폭)
       h.lo / e.entry - 1 AS mae30                    -- 보유 중 가장 불리했던 순간 (최대 역행폭)
FROM entry e JOIN exit x USING (date, t, code) LEFT JOIN hold h USING (date, t, code)
"""


def plus_minutes(t, minutes):
    return (dt.datetime.strptime(t, "%H%M%S") + dt.timedelta(minutes=minutes)).strftime("%H%M%S")


def add_outcome(con, df, hold=HOLD_MIN):
    rows = df[["date", "t", "code"]].drop_duplicates().copy()
    rows["t_end"] = rows.t.map(lambda t: plus_minutes(t, hold))
    out = con.execute(OUTCOME_SQL).df()        # rows는 위의 DataFrame
    out["date"] = out.date.astype(str)
    return df.merge(out, on=["date", "t", "code"], how="left")
