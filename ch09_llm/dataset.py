"""09-5 학습 데이터 — 8장 학습 데이터에서 Leader Score 상위 30 후보만 남기고 뉴스·LLM 특성을 붙인다.

    python -m ch09_llm.dataset --targets     # 1) LLM으로 분류할 (날짜, 종목) 목록 → data/news_targets.parquet
    python -m ch09_llm.classify --start 2026-04-01 --targets data/news_targets.parquet   # 2) 분류
    python -m ch09_llm.dataset               # 3) 특성 붙이기 → data/ml_dataset_news.parquet
"""

import argparse

import pandas as pd

from ch03_pipeline.db import DB_PATH, ROOT, connect
from ch08_lightgbm.dataset import OUT as ML_DATASET
from ch09_llm.features import add_news_features

START = "2026-04-01"       # LLM 비용 때문에 기간을 줄인다 (학습 4~6월, 검증 7월, 시험 8월~)
LEADER_TOP = 30            # 그룹(날짜·시각)마다 Leader Score 상위 30 후보
TARGETS = ROOT / "data" / "news_targets.parquet"
OUT = ROOT / "data" / "ml_dataset_news.parquet"


def leader_top(df, n=LEADER_TOP):
    rk = df.groupby(["date", "t"]).leader_score.rank(ascending=False, method="first")
    return df[rk <= n].copy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", action="store_true", help="분류할 (날짜, 종목) 목록만 만든다")
    ap.add_argument("--start", default=START)
    ap.add_argument("--data", default=ML_DATASET)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    df = pd.read_parquet(args.data)
    df = leader_top(df[df.date >= args.start])
    if args.targets:
        t = df[["date", "code"]].drop_duplicates()
        t.to_parquet(TARGETS)
        print(f"{t.date.nunique()}일, (날짜, 종목) {len(t):,}개 → {TARGETS}")
        return

    con = connect(args.db, read_only=True)
    out = add_news_features(con, df)
    out.to_parquet(OUT)
    print(f"{out.date.nunique()}일 {len(out):,}행 → {OUT}")
    print(f"t 이전 관련 뉴스가 1건 이상인 행 {(out.news_n > 0).mean():.1%}, "
          f"주인공 뉴스(1차 조건) {(out.own_n > 0).mean():.1%}, 호재 {(out.good_n > 0).mean():.1%}, "
          f"악재 {(out.bad_n > 0).mean():.1%}, 분류가 빠진 주인공 뉴스 {out.unlabeled_n.sum():,}건")


if __name__ == "__main__":
    main()
