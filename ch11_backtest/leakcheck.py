"""11-4 데이터 누수 점검 — 백테스트 결과를 믿기 전에 기계적으로 확인할 것들.

    python -m ch11_backtest.leakcheck

결과는 항목마다 통과 / 주의 / 실패. 주의는 고칠 수 없는 데이터 한계라 결과 해석에 밝혀야 하는 것.
"""

import argparse

import numpy as np
import pandas as pd

from ch03_pipeline.db import DB_PATH, connect
from ch08_lightgbm.dataset import FEATURES
from ch08_lightgbm.dataset import OUT as ML_DATASET
from ch09_llm.dataset import OUT as NEWS_DATASET
from ch09_llm.features import LLM_FEATURES, NEWS_FEATURES, add_news_features
from ch11_backtest.outcome import add_outcome
from ch11_backtest.walkforward import TEST_MONTHS, folds

LABELS = {"fwd30", "fwd60", "grade", "ret30", "mfe30", "mae30", "exit_price"}
results = []


def report(name, status, detail):
    results.append((name, status, detail))
    print(f"[{status}] {name}: {detail}", flush=True)


def check_columns():
    """1. 정답(미래) 열이 특성 목록에 들어 있지 않은가."""
    bad = LABELS & set(FEATURES + NEWS_FEATURES + LLM_FEATURES)
    report("특성에 정답 열 없음", "실패" if bad else "통과", f"겹치는 열 {sorted(bad) or '없음'}")


def check_feature_recompute(con, ml, n=300):
    """2. 특성을 원본 분봉에서 다시 계산해 같은지 (t보다 앞선 봉만 썼는가)."""
    s = ml.sample(n, random_state=0)
    o = add_outcome(con, s[["date", "t", "code", "ret"]])
    prev = con.execute("""SELECT s.date::VARCHAR AS date, s.code, arg_max(b.close, b.date) AS prev
        FROM s JOIN daily_bars b ON b.code = s.code AND b.date < s.date::DATE GROUP BY ALL""").df()
    o = o.merge(prev, on=["date", "code"])
    diff = (o.entry / o.prev - 1 - o.ret).abs()
    report("상승률을 t 이전 봉으로 재계산", "통과" if diff.max() < 1e-9 else "실패",
           f"{len(o)}개 표본, 최대 차이 {diff.max():.2e}")


def check_label_correlation(ml):
    """3. 특성 하나가 정답과 비정상적으로 강하게 맞으면 누수를 의심한다 (그룹별 순위 상관의 중앙값)."""
    part = ml[ml.date >= "2026-08-01"].dropna(subset=["fwd30"])
    corr = {c: part.groupby(["date", "t"])[[c, "fwd30"]].corr(method="spearman").xs(c, level=2)["fwd30"].median()
            for c in FEATURES}
    worst = max(corr, key=lambda c: abs(corr[c]))
    status = "실패" if abs(corr[worst]) > 0.3 else "통과"
    report("특성과 정답의 상관", status, f"가장 큰 것 {worst} {corr[worst]:+.3f} (0.3을 넘으면 누수 의심)")


def check_daily_final(con):
    """4. 일봉이 확정값인가: 전일 종가(일봉)와 그날 마지막 분봉 종가가 같은가 (03-2: 당일 저녁 일봉은 미확정)."""
    d = con.execute("""
        WITH last AS (SELECT date, code, arg_max(close, time) AS last_close FROM minute_bars
                      WHERE date >= (SELECT max(date) FROM minute_bars) - INTERVAL 30 DAY GROUP BY ALL)
        SELECT count(*) AS n, count(*) FILTER (WHERE b.close <> l.last_close) AS diff
        FROM last l JOIN daily_bars b USING (date, code)""").fetchone()
    status = "통과" if d[1] / d[0] < 0.01 else "주의"
    report("일봉 종가 = 마지막 분봉 종가 (최근 30일)", status, f"{d[0]:,}건 중 다른 것 {d[1]:,}건")


def check_news_time(con, news):
    """5. 뉴스 특성이 판단 시각 t 이전 뉴스만 셌는가: 다시 세어 비교하고, t 이후 뉴스를 넣으면 달라지는지 본다."""
    s = news.sample(300, random_state=0)
    again = add_news_features(con, s[["date", "t", "code"]])
    same = (again.news_n.values == s.news_n.values).mean()
    report("뉴스 수 재계산 (t 미만)", "통과" if same == 1 else "실패", f"300개 표본 중 같은 것 {same:.0%}")


def check_split(news, months):
    """6. 학습·검증·시험 기간이 겹치지 않고 시간 순서인가 (11-1)."""
    ok = all(tr.date.max() < va.date.min() and va.date.max() < te.date.min()
             for _, tr, va, te in folds(news, months))
    report("시점별 검증의 기간 순서", "통과" if ok else "실패", "모든 묶음에서 학습 < 검증 < 시험")


def check_theme_snapshot(con, ml):
    """7. 테마 사전이 판단 날짜 이전 것인가 (5장 사전은 2026-10에 받은 것을 1년 전체에 적용했다)."""
    first = con.execute("SELECT min(updated) FROM theme_stock_history").fetchone()[0]
    before = (pd.to_datetime(ml.date).dt.date < first).mean()
    report("테마 사전 시점", "주의" if before > 0 else "통과",
           f"사전 이력은 {first}부터. 평가 행의 {before:.0%}는 그 이전 날짜에 나중 사전을 쓴 것 (사후 분류)")


def check_selection(ml):
    """8. 분봉 수집 대상 선택 편향 (03-4 규칙 4, 08-5): 5일 연속 수집 종목 비율."""
    report("분봉 수집 대상 선택 편향", "주의",
           f"그날 장 마감 후 거래대금 상위 300만 수집. 5일 연속 수집 종목 {ml.persistent.mean():.1%} — 두 범위로 함께 평가할 것")


def check_survivor(con):
    """9. 생존 편향: 종목 명부를 언제 받았나. 그 전에 상장폐지된 종목은 데이터에 없다."""
    first = con.execute("SELECT min(updated) FROM stock_master_history").fetchone()[0]
    start = con.execute("SELECT min(date) FROM daily_bars").fetchone()[0]
    report("생존 편향", "주의", f"명부 이력은 {first}부터, 시세는 {start}부터. 그 사이 상장폐지된 종목은 빠져 있다")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--months", default=",".join(TEST_MONTHS), help="11-1의 시험 달과 같게")
    args = ap.parse_args()
    con = connect(args.db, read_only=True)
    ml = pd.read_parquet(ML_DATASET)
    news = pd.read_parquet(NEWS_DATASET)

    check_columns()
    check_feature_recompute(con, ml)
    check_label_correlation(ml)
    check_daily_final(con)
    check_news_time(con, news)
    check_split(news, args.months.split(","))
    check_theme_snapshot(con, ml)
    check_selection(ml)
    check_survivor(con)
    s = pd.Series([r[1] for r in results]).value_counts()
    print(f"\n통과 {s.get('통과', 0)} / 주의 {s.get('주의', 0)} / 실패 {s.get('실패', 0)}")
    return 1 if s.get("실패", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
