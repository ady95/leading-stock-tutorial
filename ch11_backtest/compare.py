"""11-5 여섯 방식 비교 — 같은 후보(Leader Score 상위 30), 같은 시각, 같은 지표로.

    python -m ch11_backtest.walkforward      # 먼저 시점별 검증 예측을 만든다
    python -m ch11_backtest.compare          # VLM 판단은 outputs/vlm_scores.parquet에 쌓인다 (10-4와 공유)
    python -m ch11_backtest.compare --all-times      # VLM 없이 (비용 없음)

방식: 상승률, 거래대금, Leader Score, LightGBM, LightGBM + LLM, LightGBM + VLM(LightGBM 상위 10을 VLM 점수로 재정렬)
"""

import argparse

import numpy as np
import pandas as pd
from sklearn.metrics import ndcg_score

from ch03_pipeline.db import DB_PATH, ROOT, connect
from ch08_lightgbm.train import add_grade
from ch09_llm.dataset import OUT as NEWS_DATASET
from ch10_vlm.compare import score_charts
from ch11_backtest.costs import round_trip_cost
from ch11_backtest.outcome import add_outcome
from ch11_backtest.walkforward import OUT as WF_PRED
from ch11_backtest.walkforward import TOP_K

TIMES = ["100000", "110000", "133000", "143000"]     # VLM을 판단한 시각 (10-4와 같다)
METHODS = {"상승률": "ret", "거래대금": "acc_value", "Leader Score": "leader_score",
           "LightGBM": "lgbm", "LightGBM + LLM": "lgbm_llm", "LightGBM + VLM": "lgbm_vlm"}


def add_vlm_rank(df, scores, base="lgbm", k=10):
    """base 상위 k를 VLM 점수 순으로 다시 세운 순위 점수 (나머지는 base 순서대로 그 아래)."""
    df = df.merge(scores[["date", "t", "code", "score"]], on=["date", "t", "code"], how="left")
    rank = df.groupby(["date", "t"])[base].rank(ascending=False, method="first")
    top = rank <= k
    key = np.where(top, 100 + df.score.fillna(3) - rank / 100, -rank)   # 상위 k는 VLM 점수가 먼저, 같으면 base 순
    return df.assign(lgbm_vlm=key)


def summarize(df, label="fwd30"):
    rows = []
    for name, col in METHODS.items():
        sel = df.sort_values(["date", "t", col, "acc_value"], ascending=[True, True, False, False]) \
                .groupby(["date", "t"]).head(TOP_K)
        ndcg = np.mean([ndcg_score([g.grade.to_numpy()], [g[col].to_numpy()], k=TOP_K)
                        for _, g in df.groupby(["date", "t"])])
        net = sel.ret30 - sel.cost
        rows.append({"방식": name, "NDCG@5": ndcg, "30분 상대": sel.fwd30.mean(), "60분 상대": sel.fwd60.mean(),
                     "시장을 이긴 비율": (sel.fwd30 > 0).mean(), "30분 수익률": sel.ret30.mean(),
                     "MFE": sel.mfe30.mean(), "MAE": sel.mae30.mean(), "비용": sel.cost.mean(),
                     "비용 차감 후": net.mean(), "비용 차감 후 이익 비율": (net > 0).mean()})
    rows.append({"방식": "후보 30 전체", "30분 상대": df.fwd30.mean(), "60분 상대": df.fwd60.mean(),
                 "시장을 이긴 비율": (df.fwd30 > 0).mean(), "30분 수익률": df.ret30.mean(),
                 "MFE": df.mfe30.mean(), "MAE": df.mae30.mean(), "비용": df.cost.mean(),
                 "비용 차감 후": (df.ret30 - df.cost).mean(), "비용 차감 후 이익 비율": (df.ret30 > df.cost).mean()})
    return pd.DataFrame(rows).set_index("방식")


def vs_universe(df, n_boot=5000, seed=0):
    """날짜마다 (TOP 5 평균 − 후보 30 평균)을 구하고, 날짜를 다시 뽑는 부트스트랩으로 95% 구간을 잰다."""
    rng = np.random.default_rng(seed)
    base = df.groupby("date").fwd30.mean()
    rows = []
    for name, col in METHODS.items():
        sel = df.sort_values(["date", "t", col, "acc_value"], ascending=[True, True, False, False])                 .groupby(["date", "t"]).head(TOP_K)
        d = (sel.groupby("date").fwd30.mean() - base).dropna().to_numpy()
        boot = [rng.choice(d, len(d)).mean() for _ in range(n_boot)]
        lo, hi = np.percentile(boot, [2.5, 97.5])
        rows.append({"방식": name, "일평균 차이": d.mean(), "95% 구간 아래": lo, "95% 구간 위": hi,
                     "후보 평균보다 나은 날": (d > 0).mean()})
    return pd.DataFrame(rows).set_index("방식")


def by_month(df):
    out = {}
    for name, col in METHODS.items():
        sel = df.sort_values(["date", "t", col], ascending=[True, True, False]).groupby(["date", "t"]).head(TOP_K)
        out[name] = sel.groupby(sel.date.str[:7]).fwd30.mean()
    return pd.DataFrame(out).T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-times", action="store_true", help="VLM 없이 하루 24개 시각 전체로 비교")
    ap.add_argument("--days", type=int, help="시험 기간의 앞 N일만 (VLM 비용을 줄일 때)")
    ap.add_argument("--model")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    pred = pd.read_parquet(WF_PRED)
    df = pd.read_parquet(NEWS_DATASET).dropna(subset=["fwd30"])
    df = add_grade(df, "fwd30").merge(pred, on=["date", "t", "code"])           # 시험 달(7~10월)만 남는다
    if not args.all_times:
        df = df[df.t.isin(TIMES)]
    if args.days:
        df = df[df.date.isin(sorted(df.date.unique())[:args.days])]
    con = connect(args.db, read_only=True)
    df = add_outcome(con, df)
    df["cost"] = df.entry.map(round_trip_cost)
    if args.all_times:
        METHODS.pop("LightGBM + VLM")
    else:
        need = df.sort_values("lgbm", ascending=False).groupby(["date", "t"]).head(10)
        scores = score_charts(con, list(need.groupby(["date", "t"])), args.model)
        df = add_vlm_rank(df, scores)
    print(f"시험 {df.date.min()} ~ {df.date.max()} ({df.date.nunique()}일), 그룹 {df.groupby(['date', 't']).ngroups}개, "
          f"그룹마다 Leader Score 상위 30 중 TOP {TOP_K}\n")
    fmt = {"NDCG@5": "{:.3f}", "시장을 이긴 비율": "{:.1%}", "비용 차감 후 이익 비율": "{:.1%}"}
    for scope, part in (("후보 30 전체", df), ("5일 연속 수집 종목", df[df.persistent])):
        t = summarize(part)
        print(f"[{scope}]")
        print(t.apply(lambda c: c.map(lambda v: "" if pd.isna(v) else fmt.get(c.name, "{:+.3%}").format(v)))
              .to_string())
        v = vs_universe(part)
        print(f"  후보 30 평균 대비 (날짜별 30분 상대수익률 차이, {part.date.nunique()}일)")
        print(v.apply(lambda c: c.map(("{:.0%}" if c.name.startswith("후보") else "{:+.3%}").format)).to_string())
        print()
    print("[달별 30분 상대수익률 (후보 30 전체)]")
    print(by_month(df).to_string(float_format=lambda v: f"{v:+.3%}"))
    df.to_parquet(ROOT / "outputs" / ("backtest_all_times.parquet" if args.all_times else "backtest.parquet"))


if __name__ == "__main__":
    main()
