"""04-1·04-2·04-3 판단 시점 t의 대장주 후보 지표

모든 값은 날짜 date의 시각 t보다 앞선 봉(time < t)과 전날까지의 일봉만으로 계산한다 (03-4 규칙).

    from ch04_candidates.features import snapshot
    df = snapshot(con, "2026-10-21", "103000")
"""

import datetime as dt

from ch03_pipeline.minutes import OPEN, SESSIONS

MIN_DAY_VALUE = 100e8   # 하루 거래대금 기준 (원). t 시점 기준은 이 값 × 평소 t까지의 비중
MIN_PRICE = 1000        # 동전주 제외
MIN_BAR_RATIO = 0.5     # 개장 후 경과 시간 대비 봉이 이 비율보다 적으면 단일가·저유동성으로 본다
BASELINE_DAYS = 20      # 평소 거래대금·시간대 비중을 잴 과거 거래일 수

SNAPSHOT_SQL = """
WITH
-- 오늘 t 직전까지의 봉 (04-1)
now AS (
    SELECT code, arg_max(close, time) AS price, max(acc_value) AS acc_value, count(*) AS n_bars
    FROM minute_bars WHERE date = $date AND time < $t GROUP BY code),
-- 전날까지의 일봉: 전일 종가, 지난 20일 거래대금 중앙값
hist AS (
    SELECT code,
           arg_max(close, date) AS prev_close,
           median(value) FILTER (WHERE date >= $base_from) AS med_value
    FROM daily_bars WHERE date < $date GROUP BY code),
-- 지난 20거래일 동안 t까지 하루 거래대금의 몇 %가 거래됐나 (시간대 비중)
past_days AS (
    SELECT DISTINCT date FROM minute_bars WHERE date < $date ORDER BY date DESC LIMIT $n_base),
frac AS (
    SELECT median(at_t / day_total) AS frac_t FROM (
        SELECT date, code,
               max(acc_value) FILTER (WHERE time < $t) AS at_t, max(acc_value) AS day_total
        FROM minute_bars WHERE date IN (SELECT date FROM past_days) GROUP BY date, code)
    WHERE day_total > 0),
-- 시장 기준: 지수 ETF의 t 직전 가격과 전일 종가 (04-2)
bench AS (
    SELECT CASE b.code WHEN '069500' THEN 'KOSPI' ELSE 'KOSDAQ' END AS market,
           arg_max(b.close, b.time) / any_value(p.prev) - 1 AS bench_ret
    FROM benchmark_minute b
    JOIN (SELECT code, arg_max(close, date) AS prev FROM benchmark_daily WHERE date < $date GROUP BY code) p USING (code)
    WHERE b.date = $date AND b.time < $t GROUP BY b.code)
SELECT n.code, s.name, s.market, n.price, h.prev_close, n.acc_value, n.n_bars,
       n.price / h.prev_close - 1                              AS ret,          -- 상승률
       n.acc_value / sum(n.acc_value) OVER ()                  AS value_share,  -- 거래대금 집중도
       n.acc_value / (nullif(h.med_value, 0) * f.frac_t)       AS value_surge,  -- 평소 대비 거래대금 (평소 0이면 계산 안 함)
       n.price / h.prev_close - 1 - b.bench_ret                AS rel_strength, -- 시장 대비 상대강도
       b.bench_ret, f.frac_t,
       right(n.code, 1) <> '0'                                 AS is_pref
FROM now n
JOIN hist h USING (code)
JOIN stock_master s USING (code)
CROSS JOIN frac f
LEFT JOIN bench b ON b.market = s.market
WHERE h.prev_close > 0
"""


def elapsed_minutes(date, t):
    """그날 개장 시각부터 t까지 몇 분이 지났나 (수능일 등은 개장 시각이 다르다)."""
    day = date.replace("-", "")
    open_t = SESSIONS.get(day, (OPEN, None))[0]
    start = dt.datetime.strptime(open_t, "%H%M%S")
    return max(1, (dt.datetime.strptime(t, "%H%M%S") - start).seconds // 60)


def snapshot(con, date, t):
    """날짜 date, 시각 t(HHMMSS)의 지표와 유동성 필터 통과 여부를 DataFrame으로 돌려준다."""
    d = dt.date.fromisoformat(date)
    base_from = con.execute(
        "SELECT min(date) FROM (SELECT DISTINCT date FROM daily_bars WHERE date < ? ORDER BY date DESC LIMIT ?)",
        [d, BASELINE_DAYS]).fetchone()[0]
    df = con.execute(SNAPSHOT_SQL, {"date": d, "t": t, "base_from": base_from, "n_base": BASELINE_DAYS}).df()

    # 04-3 유동성 필터
    minutes = elapsed_minutes(date, t)
    df["min_value_t"] = MIN_DAY_VALUE * df["frac_t"]
    df["pass_pref"] = ~df["is_pref"]
    df["pass_price"] = df["price"] >= MIN_PRICE
    df["pass_bars"] = df["n_bars"] >= MIN_BAR_RATIO * minutes
    df["pass_value"] = df["acc_value"] >= df["min_value_t"]
    df["is_candidate"] = df[["pass_pref", "pass_price", "pass_bars", "pass_value"]].all(axis=1)
    return df.sort_values("acc_value", ascending=False).reset_index(drop=True)
