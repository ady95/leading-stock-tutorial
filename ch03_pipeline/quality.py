"""03-4 수집한 일봉·분봉의 품질 검사

    python -m ch03_pipeline.quality
    python -m ch03_pipeline.quality --db 다른/경로/leading_stock.duckdb
"""

import argparse

from ch03_pipeline.db import DB_PATH, connect

CHECKS = {
    "1. 규모": """
        SELECT count(*) AS minute_rows, count(DISTINCT date) AS n_days,
               count(DISTINCT code) AS n_codes, min(date) AS first_day, max(date) AS last_day
        FROM minute_bars""",

    "2. 종목·일별 봉 개수": """
        WITH c AS (SELECT date, code, count(*) AS n FROM minute_bars GROUP BY ALL)
        SELECT count(*) AS stock_days, min(n) AS min_n, quantile_cont(n, 0.1) AS p10,
               median(n) AS median_n, max(n) AS max_n,
               count(*) FILTER (WHERE n = 381) AS n381, count(*) FILTER (WHERE n < 100) AS under100
        FROM c""",

    "3. 첫 봉이 개장 시각보다 늦은 종목 비율이 높은 날": """
        WITH c AS (SELECT date, code, min(time) AS first_t FROM minute_bars GROUP BY ALL)
        SELECT date, count(*) AS n_codes, mode(first_t) AS usual_first,
               round(avg((first_t > mode_first)::int), 3) AS late_ratio
        FROM (SELECT *, mode(first_t) OVER (PARTITION BY date) AS mode_first FROM c)
        GROUP BY date HAVING avg((first_t > mode_first)::int) > 0.1 OR mode(first_t) <> '090000'
        ORDER BY date""",

    "4. 가격 정합성 위반": """
        SELECT count(*) FILTER (WHERE high < low) AS high_lt_low,
               count(*) FILTER (WHERE open > high OR open < low) AS open_out,
               count(*) FILTER (WHERE close > high OR close < low) AS close_out,
               count(*) FILTER (WHERE open <= 0 OR close <= 0) AS non_positive
        FROM minute_bars""",

    "5. 누적 거래대금이 줄어든 봉": """
        SELECT count(*) AS decreases FROM (
            SELECT acc_value - lag(acc_value) OVER (PARTITION BY date, code ORDER BY time) AS diff
            FROM minute_bars) WHERE diff < 0""",

    "6. 분봉 마지막 누적 거래대금 ÷ 일봉 거래대금": """
        WITH m AS (SELECT date, code, max(acc_value) AS minute_value FROM minute_bars GROUP BY ALL)
        SELECT count(*) AS stock_days,
               round(median(minute_value / d.value), 4) AS median_ratio,
               round(quantile_cont(minute_value / d.value, 0.05), 4) AS p5,
               count(*) FILTER (WHERE minute_value < 0.5 * d.value) AS under_half
        FROM m JOIN daily_bars d USING (date, code) WHERE d.value > 0""",

    "7. 일봉 거래대금의 절반 넘게 정규장 밖에서 거래된 사례 (상위 5)": """
        WITH m AS (SELECT date, code, max(acc_value) AS minute_value FROM minute_bars GROUP BY ALL)
        SELECT d.date, d.code, s.name, round(m.minute_value / 1e8) AS minute_eok,
               round(d.value / 1e8) AS daily_eok
        FROM m JOIN daily_bars d USING (date, code) JOIN stock_master s USING (code)
        WHERE m.minute_value < 0.5 * d.value ORDER BY d.value - m.minute_value DESC LIMIT 5""",

    "8. 일봉 거래량이 0인 날 (거래정지 등)": """
        SELECT count(*) AS zero_volume_days, count(DISTINCT code) AS n_codes
        FROM daily_bars WHERE volume = 0""",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    con = connect(args.db, read_only=True)
    for title, sql in CHECKS.items():
        print(f"[{title}]")
        print(con.execute(sql).df().to_string(index=False), end="\n\n")


if __name__ == "__main__":
    main()
