"""10-3 VLM 차트 판단 — AI 전용 차트를 보고 앞으로 30분의 강도를 1~5점으로 매긴다.

    python -m ch10_vlm.judge --date 2026-10-21 --time 103000 --code 122640 --repeat 3
    python -m ch10_vlm.judge --date 2026-10-21 --time 103000 --repeat 3          # 그 시각 Leader Score 1위 (샘플 데이터)
"""

import argparse
from typing import Literal

from pydantic import BaseModel

from ch03_pipeline.db import DB_PATH, connect
from ch10_vlm.cases import pick_stock
from ch10_vlm.chart import ai_chart, load
from ch10_vlm.vlm import ask_image

SYSTEM = """너는 장중 주식 차트를 보고 앞으로 30분을 판단한다. 차트에 보이는 것만 근거로 삼는다.
차트에는 종목명과 날짜가 없다. 어떤 종목인지 추측하지 않는다."""
QUESTION = """차트의 선: STOCK(종목, 전일 종가 대비 %), STOCK VWAP, MARKET(시장 ETF), THEME AVG(같은 테마 종목 평균).
아래 막대는 5분 거래대금, 빨간 세로선 NOW가 지금이다. NOW 오른쪽은 아직 오지 않은 시간이다.
- trend: 최근 흐름 (상승 지속, 횡보, 되돌림)
- vwap: 지금 가격이 VWAP의 위인가 아래인가
- vs_market: 오늘 시장 대비 강도 (강함, 비슷, 약함)
- vs_theme: 테마 평균 대비 강도 (강함, 비슷, 약함, 테마선 없음)
- pattern: 눈에 띄는 모양을 짧은 이름으로 (예: 고점 재돌파 시도, 장대 양봉 후 눌림, 계단식 상승)
- score: 앞으로 30분 동안 이 종목이 시장보다 강할 가능성. 1(매우 낮음)~5(매우 높음), 판단이 어려우면 3
- reason: 한 문장"""


class Judge(BaseModel):
    trend: Literal["상승 지속", "횡보", "되돌림"]
    vwap: Literal["위", "아래"]
    vs_market: Literal["강함", "비슷", "약함"]
    vs_theme: Literal["강함", "비슷", "약함", "테마선 없음"]
    pattern: str
    score: Literal[1, 2, 3, 4, 5]
    reason: str


def judge(png, model=None):
    return ask_image(SYSTEM, QUESTION, png, Judge, model=model)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--time", default="103000")
    ap.add_argument("--code", help="생략하면 그 시각 Leader Score 1위 종목")
    ap.add_argument("--repeat", type=int, default=1, help="같은 차트를 몇 번 판단할지 (일관성 확인)")
    ap.add_argument("--model")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    con = connect(args.db, read_only=True)
    code, market, members = pick_stock(con, args.date, args.time, args.code)
    s, bench, theme, _ = load(con, args.date, args.time, code, market, members)
    print(f"{args.date} {args.time[:2]}:{args.time[2:4]} {code} 차트")
    png = ai_chart(s, bench, theme, args.time)
    for i in range(args.repeat):
        out, usage = judge(png, args.model)
        print(f"{i + 1}. 점수 {out.score} | {out.trend} · VWAP {out.vwap} · 시장 대비 {out.vs_market} · "
              f"테마 대비 {out.vs_theme} | {out.pattern} — {out.reason}")
    print(f"(입력 {usage.input_tokens:,}토큰, 출력 {usage.output_tokens:,}토큰)")


if __name__ == "__main__":
    main()
