"""08-3·08-4·08-5 LightGBM Ranker 학습과 평가

    python -m ch08_lightgbm.train
    python -m ch08_lightgbm.train --label fwd60
    python -m ch08_lightgbm.train --train-end 2026-09-15 --valid-end 2026-09-30    # 샘플 데이터(60일)

같은 (날짜, 시각)의 후보들을 한 그룹으로 묶어 "이후 수익률 순위"를 학습한다.
학습·검증·시험은 날짜 순으로 나눈다 (무작위로 섞지 않는다).
"""

import argparse

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import ndcg_score

from ch03_pipeline.db import ROOT
from ch08_lightgbm.dataset import FEATURES, OUT

TRAIN_END, VALID_END = "2026-06-30", "2026-07-31"   # 그 뒤(2026-08-01 ~)가 시험 기간
N_GRADES = 5
TOP_K = 10
PARAMS = dict(objective="lambdarank", n_estimators=1000, learning_rate=0.03, num_leaves=31,
              min_child_samples=100, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
              random_state=42, verbose=-1)


def add_grade(df, label):
    """그룹(날짜·시각) 안에서 정답 수익률의 순위를 0~4 등급으로. 4가 가장 많이 오른 20%."""
    pct = df.groupby(["date", "t"])[label].rank(pct=True)
    return df.assign(grade=np.minimum((pct * N_GRADES).astype(int), N_GRADES - 1))


def groups(df):
    """LGBMRanker는 같은 그룹의 행이 붙어 있어야 하고, 그룹 크기 목록을 받는다."""
    return df.groupby(["date", "t"], sort=False).size().to_numpy()


def top_k_returns(df, col, label, k=TOP_K):
    """그룹마다 col 상위 k종목을 고른 뒤, 그 종목들의 정답 수익률."""
    top = df.sort_values(["date", "t", col, "acc_value"], ascending=[True, True, False, False])
    return top.groupby(["date", "t"]).head(k)


def evaluate(test, label):
    rows, by_day = [], {}
    for name, col in (("LightGBM", "pred"), ("Leader Score", "leader_score"),
                      ("상승률", "ret"), ("거래대금", "acc_value")):
        sel = top_k_returns(test, col, label)
        by_day[name] = sel.groupby("date")[label].mean()
        ndcg = np.mean([ndcg_score([g.grade.to_numpy()], [g[col].to_numpy()], k=TOP_K)
                        for _, g in test.groupby(["date", "t"])])
        rows.append({"방식": name, "NDCG@10": ndcg, "평균": sel[label].mean(),
                     "중앙값": sel[label].median(), "시장을 이긴 비율": (sel[label] > 0).mean()})
    rows.append({"방식": "후보 전체", "NDCG@10": np.nan, "평균": test[label].mean(),
                 "중앙값": test[label].median(), "시장을 이긴 비율": (test[label] > 0).mean()})
    return pd.DataFrame(rows), pd.DataFrame(by_day)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="fwd30", choices=["fwd30", "fwd60"])
    ap.add_argument("--data", default=OUT)
    ap.add_argument("--train-end", default=TRAIN_END, help="이 날짜까지 학습")
    ap.add_argument("--valid-end", default=VALID_END, help="이 날짜까지 검증(조기 종료), 그 뒤는 시험")
    args = ap.parse_args()

    df = pd.read_parquet(args.data).dropna(subset=[args.label])
    df = add_grade(df, args.label).sort_values(["date", "t", "code"]).reset_index(drop=True)
    train = df[df.date <= args.train_end]
    valid = df[(df.date > args.train_end) & (df.date <= args.valid_end)]
    test = df[df.date > args.valid_end].copy()
    print(f"학습 {train.date.nunique()}일 {len(train):,}행 / 검증 {valid.date.nunique()}일 / 시험 {test.date.nunique()}일 "
          f"({test.date.min()} ~ {test.date.max()}), 특성 {len(FEATURES)}개, 정답 {args.label}")

    model = lgb.LGBMRanker(**PARAMS)
    model.fit(train[FEATURES], train.grade, group=groups(train),
              eval_set=[(valid[FEATURES], valid.grade)], eval_group=[groups(valid)], eval_at=[TOP_K],
              callbacks=[lgb.early_stopping(100, verbose=False)])
    print(f"조기 종료: {model.best_iteration_}번째 트리에서 검증 NDCG@{TOP_K} "
          f"{model.best_score_['valid_0'][f'ndcg@{TOP_K}']:.4f}\n")

    test["pred"] = model.predict(test[FEATURES], num_iteration=model.best_iteration_)
    # 전체 후보와, 선택 편향을 걷어 낸 "5일 연속 수집 종목"을 함께 평가한다 (08-5)
    for scope, part in (("전체 후보", test), ("5일 연속 수집 종목 — 선택 편향 점검", test[test.persistent])):
        table, by_day = evaluate(part, args.label)
        print(f"[시험 기간 {scope}: 그룹별 TOP {TOP_K}의 이후 수익률 (시장 대비)]")
        print(table.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
        for other in ("Leader Score", "상승률", "거래대금"):
            d = (by_day["LightGBM"] - by_day[other]).dropna()
            print(f"  LightGBM − {other}: 일평균 {d.mean() * 100:+.3f}%p, LightGBM이 나은 날 {(d > 0).mean():.1%} ({len(d)}일)")
        print()

    imp = pd.Series(model.booster_.feature_importance("gain"), index=FEATURES).sort_values(ascending=False)
    print("\n[특성 중요도 (gain 비중)]")
    print((imp / imp.sum()).round(3).to_string())
    model.booster_.save_model(str(ROOT / "data" / f"lgbm_ranker_{args.label}.txt"))


if __name__ == "__main__":
    main()
