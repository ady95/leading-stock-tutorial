"""10-1 판독 정확도 — 같은 시점을 사람용 차트와 AI 전용 차트(수치 표기 없음·있음)로 그려, VLM이 읽은 값을 실제 값과 맞춰 본다.

    python -m ch10_vlm.read_test --n 100
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from ch03_pipeline.db import DB_PATH, ROOT, connect
from ch09_llm.dataset import leader_top
from ch10_vlm.cases import theme_members
from ch10_vlm.chart import ai_chart, human_chart, load
from ch10_vlm.vlm import ask_image

TIMES = ["100000", "110000", "133000"]
SYSTEM = "너는 주식 차트를 읽는다. 차트에 보이는 것만 답하고, 차트로 알 수 없으면 '알수없음'으로 답한다."
QUESTION = """이 차트의 마지막 시점(가장 오른쪽 봉) 기준으로 답하라.
- now_ret: 전일 종가 대비 등락률(%)
- from_high: 당일 고점 대비 현재 위치(%, 0 이하)
- last30: 최근 30분 동안의 방향 (상승, 하락, 횡보)
- above_vwap: 현재가가 VWAP보다 위인가 (위, 아래, 알수없음)
- vs_market: 시장 대비 오늘의 강도 (강함, 비슷, 약함, 알수없음)"""


class Read(BaseModel):
    now_ret: float
    from_high: float
    last30: Literal["상승", "하락", "횡보"]
    above_vwap: Literal["위", "아래", "알수없음"]
    vs_market: Literal["강함", "비슷", "약함", "알수없음"]


def truth(s, bench):
    """차트를 그린 데이터에서 직접 계산한 정답. 방향·강도의 경계는 ±0.5%p, ±1%p."""
    now, ago = s.ret.iloc[-1], s.ret.iloc[-31] if len(s) > 30 else s.ret.iloc[0]
    rs = now - bench.ret.iloc[-1]
    return {"now_ret": now * 100, "from_high": (s.close.iloc[-1] / s.high.max() - 1) * 100,
            "last30": "상승" if now - ago > 0.005 else "하락" if now - ago < -0.005 else "횡보",
            "above_vwap": "위" if now > s.vwap_ret.iloc[-1] else "아래",
            "vs_market": "강함" if rs > 0.01 else "약함" if rs < -0.01 else "비슷"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--start", default="2026-08-03")
    ap.add_argument("--model")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    con = connect(args.db, read_only=True)
    ml = pd.read_parquet(ROOT / "data" / "ml_dataset.parquet", columns=["date", "t", "code", "market", "leader_score"])
    ml = leader_top(ml[(ml.date >= args.start) & ml.t.isin(TIMES)])
    cases = ml.sample(args.n, random_state=0).sort_values(["date", "t"])

    jobs, members = [], {}
    for r in cases.itertuples():
        key = (r.date, r.t)
        if key not in members:
            members[key] = theme_members(con, r.date, r.t)
        s, bench, theme, prev = load(con, r.date, r.t, r.code, r.market, members[key].get(r.code, []))
        jobs.append((r, truth(s, bench), {"사람용": human_chart(s, r.code, prev),
                                          "AI 전용": ai_chart(s, bench, theme, r.t, annotate=False),
                                          "AI 전용 + 수치": ai_chart(s, bench, theme, r.t)}))

    def run(job):
        r, true, charts = job
        out = []
        for style, png in charts.items():
            got, _ = ask_image(SYSTEM, QUESTION, png, Read, model=args.model)
            out.append({"date": r.date, "t": r.t, "code": r.code, "style": style,
                        **{f"true_{k}": v for k, v in true.items()}, **got.model_dump()})
        return out

    with ThreadPoolExecutor(6) as ex:
        rows = [x for out in ex.map(run, jobs) for x in out]
    df = pd.DataFrame(rows)
    df.to_parquet(ROOT / "outputs" / "read_test.parquet")
    summary = df.groupby("style").apply(lambda g: pd.Series({
        "등락률 오차(%p, 중앙값)": (g.now_ret - g.true_now_ret).abs().median(),
        "고점 대비 오차(%p, 중앙값)": (g.from_high - g.true_from_high).abs().median(),
        "최근 30분 방향 정답률": (g.last30 == g.true_last30).mean(),
        "VWAP 위·아래 정답률": (g.above_vwap == g.true_above_vwap).mean(),
        "VWAP 알수없음": (g.above_vwap == "알수없음").mean(),
        "시장 대비 정답률": (g.vs_market == g.true_vs_market).mean(),
        "시장 대비 알수없음": (g.vs_market == "알수없음").mean()}), include_groups=False)
    print(f"{len(jobs)}개 시점 × 차트 {len(jobs[0][2])}종\n")
    print(summary.T.to_string(float_format=lambda v: f"{v:.3f}"))


if __name__ == "__main__":
    main()
