"""11-1 시점별 검증(Walk-forward) — 시험 달마다 그 앞의 기간으로 다시 학습한다.

    python -m ch11_backtest.walkforward      # data/wf_predictions.parquet

데이터: 09-5의 학습 데이터 (Leader Score 상위 30 후보, 2026-04-01 ~, 뉴스·LLM 특성 포함).
시험 달 M: 학습 = 2026-04-01 ~ (M−2달 말), 검증 = M−1달 (조기 종료), 시험 = M.
LightGBM은 시드마다 결과가 흔들리므로(09-5), 시드 5개의 그룹 내 순위를 평균한다.
"""

import argparse

import lightgbm as lgb
import pandas as pd

from ch03_pipeline.db import ROOT
from ch08_lightgbm.dataset import FEATURES
from ch08_lightgbm.train import PARAMS, add_grade, groups
from ch09_llm.dataset import OUT as NEWS_DATASET
from ch09_llm.features import LLM_FEATURES, NEWS_FEATURES

TEST_MONTHS = ["2026-07", "2026-08", "2026-09", "2026-10"]
SEEDS = [1, 2, 3, 4, 5]
TOP_K = 5
MODELS = {"lgbm": FEATURES, "lgbm_llm": FEATURES + NEWS_FEATURES + LLM_FEATURES}
OUT = ROOT / "data" / "wf_predictions.parquet"


def folds(df, months=TEST_MONTHS):
    """(시험 달, 학습, 검증, 시험) 묶음. 날짜 순서만으로 나눈다."""
    month = df.date.str[:7]
    for m in months:
        valid_m = (pd.Period(m) - 1).strftime("%Y-%m")
        yield m, df[month < valid_m], df[month == valid_m], df[month == m]


def fit_predict(train, valid, test, cols, seed):
    model = lgb.LGBMRanker(**{**PARAMS, "random_state": seed})
    model.fit(train[cols], train.grade, group=groups(train),
              eval_set=[(valid[cols], valid.grade)], eval_group=[groups(valid)], eval_at=[TOP_K],
              callbacks=[lgb.early_stopping(100, verbose=False)])
    pred = pd.Series(model.predict(test[cols], num_iteration=model.best_iteration_), index=test.index)
    return pred.groupby([test.date, test.t]).rank(pct=True), model.best_iteration_   # 그룹 안 백분위


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=NEWS_DATASET)
    ap.add_argument("--label", default="fwd30")
    args = ap.parse_args()

    df = pd.read_parquet(args.data).dropna(subset=[args.label])
    df = add_grade(df, args.label).sort_values(["date", "t", "code"]).reset_index(drop=True)
    out = []
    for m, train, valid, test in folds(df):
        res = test[["date", "t", "code"]].assign(month=m)
        for name, cols in MODELS.items():
            preds, iters = zip(*(fit_predict(train, valid, test, cols, s) for s in SEEDS))
            res[name] = pd.concat(preds, axis=1).mean(axis=1)                 # 시드 5개의 순위 평균
            print(f"{m}: 학습 {train.date.nunique()}일 {len(train):,}행, 검증 {valid.date.nunique()}일, "
                  f"시험 {test.date.nunique()}일 | {name} 조기 종료 트리 {list(iters)}", flush=True)
        out.append(res)
    pd.concat(out).to_parquet(OUT)
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
