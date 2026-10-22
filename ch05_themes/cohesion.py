"""05-2 같은 테마 종목은 함께 움직이나 — 일간 수익률 상관으로 테마의 결속력을 잰다.

    python -m ch05_themes.cohesion
"""

import argparse
import itertools

import numpy as np
import pandas as pd

from ch03_pipeline.db import DB_PATH, connect

MIN_DAYS = 200          # 1년 중 이만큼 거래된 종목만
MIN_THEME_SIZE = 5      # 결속력을 잴 테마의 최소 종목 수
RANDOM_PAIRS = 200_000


def excess_returns(con):
    """종목별 일간 수익률에서 같은 시장 기준 ETF의 수익률을 뺀 표 (행: 날짜, 열: 종목)."""
    r = con.execute("""
        WITH b AS (SELECT date, code, close, volume,
                          lag(close) OVER (PARTITION BY code ORDER BY date) AS prev FROM daily_bars)
        SELECT b.date, b.code, s.market, b.close / b.prev - 1 AS ret
        FROM b JOIN stock_master s USING (code) WHERE b.prev > 0 AND b.volume > 0""").df()
    bench = con.execute("""
        WITH b AS (SELECT date, code, close, lag(close) OVER (PARTITION BY code ORDER BY date) AS prev
                   FROM benchmark_daily)
        SELECT date, CASE code WHEN '069500' THEN 'KOSPI' ELSE 'KOSDAQ' END AS market,
               close / prev - 1 AS bench_ret FROM b WHERE prev > 0""").df()
    r = r.merge(bench, on=["date", "market"])
    r["excess"] = r.ret - r.bench_ret
    table = {k: r.pivot(index="date", columns="code", values=k) for k in ("ret", "excess")}
    keep = table["ret"].count() >= MIN_DAYS
    return {k: v.loc[:, keep] for k, v in table.items()}


def pair_corr(corr, pairs):
    idx = {c: i for i, c in enumerate(corr.columns)}
    v = np.array([corr.values[idx[a], idx[b]] for a, b in pairs])
    return v[~np.isnan(v)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    con = connect(args.db, read_only=True)

    tables = excess_returns(con)
    themes = con.execute("SELECT theme_code, theme_name, code FROM theme_stock").df()
    themes = themes[themes.code.isin(tables["ret"].columns)]
    same = {p for _, g in themes.groupby("theme_code") for p in itertools.combinations(sorted(g.code), 2)}

    rng = np.random.default_rng(0)
    codes = list(tables["ret"].columns)
    i, j = rng.integers(0, len(codes), RANDOM_PAIRS), rng.integers(0, len(codes), RANDOM_PAIRS)
    rand = [(codes[a], codes[b]) for a, b in zip(i, j) if a != b]

    print(f"종목 {len(codes)}개, 같은 테마 쌍 {len(same):,}개, 무작위 쌍 {len(rand):,}개")
    for key, label in (("ret", "일간 수익률"), ("excess", "시장 대비 초과수익률")):
        corr = tables[key].corr(min_periods=150)
        print(f"[{label}] 같은 테마 쌍 {np.median(pair_corr(corr, same)):.3f} / "
              f"무작위 쌍 {np.median(pair_corr(corr, rand)):.3f} (상관 중앙값)")

    corr = tables["excess"].corr(min_periods=150)
    rows = [(name, len(g), np.median(pair_corr(corr, itertools.combinations(sorted(g.code), 2))))
            for (_, name), g in themes.groupby(["theme_code", "theme_name"]) if len(g) >= MIN_THEME_SIZE]
    t = pd.DataFrame(rows, columns=["theme", "n", "cohesion"]).sort_values("cohesion", ascending=False)
    print(f"\n테마 {len(t)}개의 결속력(초과수익률 상관 중앙값) — 전체 중앙값 {t.cohesion.median():.3f}")
    print(t.head(5).round(3).to_string(index=False))
    print(t.tail(4).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
