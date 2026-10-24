"""10-4 정형 데이터 모델과 + VLM 비교 — 기준 점수의 상위 10종목을 VLM 점수로 다시 줄 세운다.

    python -m ch10_vlm.compare                 # 시험 기간 전체 (VLM 판단은 outputs/vlm_scores.parquet에 쌓인다)
    python -m ch10_vlm.compare --days 5        # 앞의 5일만 (시험용)

기준 점수: 6장 Leader Score, 8장 LightGBM(data/lgbm_ranker_fwd30.txt).
"""

import argparse
from concurrent.futures import ThreadPoolExecutor

import lightgbm as lgb
import pandas as pd

from ch03_pipeline.db import DB_PATH, ROOT, connect
from ch08_lightgbm.dataset import FEATURES
from ch10_vlm.cases import theme_members
from ch10_vlm.chart import ai_chart, load
from ch10_vlm.judge import judge

TIMES = ["100000", "110000", "133000", "143000"]
BASE_K, TOP_K = 10, 5
CACHE = ROOT / "outputs" / "vlm_scores.parquet"


def score_charts(con, groups, model=None, workers=8):
    """그룹(날짜·시각)마다 차트를 그리고 VLM 점수를 받는다. 이미 받은 것은 건너뛴다."""
    done = pd.read_parquet(CACHE) if CACHE.exists() else pd.DataFrame(columns=["date", "t", "code"])
    have = set(zip(done.date, done.t, done.code))
    rows = []
    with ThreadPoolExecutor(workers) as ex:
        for i, ((date, t), g) in enumerate(groups, 1):
            todo = g[[(date, t, c) not in have for c in g.code]]
            if todo.empty:
                continue
            members = theme_members(con, date, t)
            charts = []
            for r in todo.itertuples():                       # 그리기는 한 스레드에서 (matplotlib)
                s, bench, theme, _ = load(con, date, t, r.code, r.market, members.get(r.code, []))
                charts.append((r.code, ai_chart(s, bench, theme, t)))
            for code, (out, usage) in zip([c for c, _ in charts], ex.map(lambda x: judge(x[1], model), charts)):
                rows.append({"date": date, "t": t, "code": code, **out.model_dump(),
                             "in_tokens": usage.input_tokens, "out_tokens": usage.output_tokens})
            if i % 10 == 0:
                pd.concat([done, pd.DataFrame(rows)]).to_parquet(CACHE)
                print(f"  {i}/{len(groups)} 그룹, 판단 {len(done) + len(rows):,}건", flush=True)
    out = pd.concat([done, pd.DataFrame(rows)], ignore_index=True)
    out.to_parquet(CACHE)
    return out


def pick(df, base, how):
    """그룹마다 기준 점수 상위 10 → TOP 5. how: 기준 그대로 / VLM 재정렬 / VLM 거르기(2점 이하 제외)."""
    top = df.sort_values(["date", "t", base], ascending=[True, True, False]).groupby(["date", "t"]).head(BASE_K)
    if how == "VLM 재정렬":
        top = top.assign(rank=top.groupby(["date", "t"]).cumcount())
        top = top.sort_values(["date", "t", "score", "rank"], ascending=[True, True, False, True])
    elif how == "VLM 거르기":
        top = top[top.score > 2]
    return top.groupby(["date", "t"]).head(TOP_K)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-03")
    ap.add_argument("--days", type=int, help="앞의 N일만")
    ap.add_argument("--model")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    df = pd.read_parquet(ROOT / "data" / "ml_dataset.parquet")
    df = df[(df.date >= args.start) & df.t.isin(TIMES)].dropna(subset=["fwd30"]).copy()
    if args.days:
        df = df[df.date.isin(sorted(df.date.unique())[:args.days])]
    booster = lgb.Booster(model_file=str(ROOT / "data" / "lgbm_ranker_fwd30.txt"))    # 8장에서 저장한 모델
    df["lgbm"] = booster.predict(df[FEATURES])

    # 판단할 종목: 두 기준 점수의 상위 10 (후보 전체, 5일 연속 수집 종목 각각)
    need = pd.concat([part.sort_values(b, ascending=False).groupby(["date", "t"]).head(BASE_K)
                      for part in (df, df[df.persistent]) for b in ("leader_score", "lgbm")])
    need = need.drop_duplicates(["date", "t", "code"])
    con = connect(args.db, read_only=True)
    scores = score_charts(con, list(need.groupby(["date", "t"])), args.model)
    df = df.merge(scores[["date", "t", "code", "score", "trend", "pattern"]], on=["date", "t", "code"], how="left")
    print(f"시험 {df.date.nunique()}일 × 시각 {len(TIMES)}개, VLM 판단 {len(scores):,}건 "
          f"(입력 {int(scores.in_tokens.sum()):,} 출력 {int(scores.out_tokens.sum()):,}토큰)\n")

    for scope, part in (("후보 전체", df), ("5일 연속 수집 종목", df[df.persistent])):
        print(f"[{scope}: 기준 점수 상위 {BASE_K} 중 TOP {TOP_K}의 이후 30분 (시장 대비)]")
        for base, label in (("leader_score", "Leader Score"), ("lgbm", "LightGBM")):
            ref = pick(part, base, "기준 그대로").groupby("date").fwd30.mean()
            for how in ("기준 그대로", "VLM 재정렬", "VLM 거르기"):
                sel = pick(part, base, how)
                d = (sel.groupby("date").fwd30.mean() - ref).dropna()
                extra = "" if how == "기준 그대로" else f", 기준보다 나은 날 {(d > 0).mean():.1%} (같은 날 {(d == 0).mean():.1%})"
                print(f"  {label:<12} {how:<8} 평균 {sel.fwd30.mean():+.4f}, 시장을 이긴 비율 "
                      f"{(sel.fwd30 > 0).mean():.1%}{extra}")
        print()
    judged = df.dropna(subset=["score"])
    print("[VLM 점수별 이후 30분 (판단한 종목 전체)]")
    print(judged.groupby("score").fwd30.agg(["count", "mean", "median"]).to_string(float_format=lambda v: f"{v:+.4f}"))


if __name__ == "__main__":
    main()
