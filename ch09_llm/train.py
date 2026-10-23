"""09-5 LightGBM + LLM 특성 결합 — Leader Score 상위 30 후보 안에서 순위를 다시 매긴다.

    python -m ch09_llm.train

같은 데이터·같은 분할로 네 가지를 비교한다.
  Leader Score(5지표) / Leader Score + 뉴스(6지표) / LightGBM(8장 특성) / LightGBM + 뉴스 수 / LightGBM + 뉴스 수 + LLM
"""

import argparse

import lightgbm as lgb
import pandas as pd

from ch06_leader_score.score import WEIGHTS, pct_rank
from ch08_lightgbm.dataset import FEATURES
from ch08_lightgbm.train import PARAMS, TRAIN_END, VALID_END, add_grade, groups, top_k_returns
from ch09_llm.dataset import OUT
from ch09_llm.features import LLM_FEATURES, NEWS_FEATURES

TOP_K = 5                  # 30 후보 중 상위 5
MODELS = {
    "LightGBM": FEATURES,
    "LightGBM + 뉴스 수": FEATURES + NEWS_FEATURES,
    "LightGBM + 뉴스 수 + LLM": FEATURES + NEWS_FEATURES + LLM_FEATURES,
}


def add_leader_news(df):
    """6장 Leader Score에 뉴스·공시 지표(가중치 10%)를 되살린다.
    5지표 점수는 나머지 90%를 나눠 가진 값이므로, 0.9배 하고 뉴스 백분위의 10%를 더하면 6지표 점수다.
    뉴스 백분위는 그룹(같은 시각의 30 후보) 안에서 잰다."""
    w = WEIGHTS["news"]
    s_news = df.groupby(["date", "t"]).news_score.transform(lambda s: pct_rank(s, method="min"))
    return df.assign(leader_news=(1 - w) * df.leader_score + w * s_news)


def fit(train, valid, cols):
    model = lgb.LGBMRanker(**PARAMS)
    model.fit(train[cols], train.grade, group=groups(train),
              eval_set=[(valid[cols], valid.grade)], eval_group=[groups(valid)], eval_at=[TOP_K],
              callbacks=[lgb.early_stopping(100, verbose=False)])
    return model


def summary(test, cols, label):
    rows, by_day = [], {}
    for name, col in cols.items():
        sel = top_k_returns(test, col, label, k=TOP_K)
        by_day[name] = sel.groupby("date")[label].mean()
        rows.append({"방식": name, "평균": sel[label].mean(), "중앙값": sel[label].median(),
                     "시장을 이긴 비율": (sel[label] > 0).mean()})
    rows.append({"방식": "후보 30 전체", "평균": test[label].mean(), "중앙값": test[label].median(),
                 "시장을 이긴 비율": (test[label] > 0).mean()})
    return pd.DataFrame(rows), pd.DataFrame(by_day)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="fwd30", choices=["fwd30", "fwd60"])
    ap.add_argument("--data", default=OUT)
    ap.add_argument("--train-end", default=TRAIN_END)
    ap.add_argument("--valid-end", default=VALID_END)
    args = ap.parse_args()

    df = pd.read_parquet(args.data).dropna(subset=[args.label])
    df = add_leader_news(add_grade(df, args.label)).sort_values(["date", "t", "code"]).reset_index(drop=True)
    train = df[df.date <= args.train_end]
    valid = df[(df.date > args.train_end) & (df.date <= args.valid_end)]
    test = df[df.date > args.valid_end].copy()
    print(f"학습 {train.date.nunique()}일 {len(train):,}행 / 검증 {valid.date.nunique()}일 / "
          f"시험 {test.date.nunique()}일 ({test.date.min()} ~ {test.date.max()}), 정답 {args.label}")

    cols = {"Leader Score": "leader_score", "Leader Score + 뉴스": "leader_news"}
    for name, feats in MODELS.items():
        model = fit(train, valid, feats)
        test[name] = model.predict(test[feats], num_iteration=model.best_iteration_)
        cols[name] = name
        print(f"  {name}: 특성 {len(feats)}개, {model.best_iteration_}번째 트리에서 조기 종료")
        if name.endswith("LLM"):
            imp = pd.Series(model.booster_.feature_importance("gain"), index=feats)
            share = (imp / imp.sum())[NEWS_FEATURES + LLM_FEATURES]
            print("    뉴스 특성의 gain 비중:", ", ".join(f"{k} {v:.3f}" for k, v in share.items()))

    for scope, part in (("후보 30 전체", test), ("5일 연속 수집 종목", test[test.persistent])):
        table, by_day = summary(part, cols, args.label)
        print(f"\n[시험 기간 {scope}: 그룹별 TOP {TOP_K}의 이후 수익률 (시장 대비)]")
        print(table.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
        for a, b in (("Leader Score + 뉴스", "Leader Score"), ("LightGBM + 뉴스 수", "LightGBM"),
                     ("LightGBM + 뉴스 수 + LLM", "LightGBM + 뉴스 수")):
            d = (by_day[a] - by_day[b]).dropna()
            print(f"  {a} − {b}: 일평균 {d.mean() * 100:+.3f}%p, 나은 날 {(d > 0).mean():.1%} "
                  f"(같은 날 {(d == 0).mean():.1%}, {len(d)}일)")


if __name__ == "__main__":
    main()
