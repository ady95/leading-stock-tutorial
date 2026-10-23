"""09-3 상승 재료 근거 검증 — LLM이 댄 근거를 원문 제목과 기계적으로 대조한다.

    python -m ch09_llm.evidence --date 2026-10-21 --time 103000              # Leader Score 상위 5종목
    python -m ch09_llm.evidence --date 2026-10-21 --time 103000 --code 005930
"""

import argparse
import datetime as dt
from typing import Literal

from pydantic import BaseModel

from ch03_pipeline.db import DB_PATH, connect
from ch06_leader_score.score import leader_scores
from ch09_llm.llm import ask

SYSTEM = """너는 한국 주식의 상승 재료를 찾는다. 아래 [뉴스]의 제목만 근거로 삼는다.
- has_material: 그 종목이 오른 이유로 볼 만한 그 종목 자신의 재료가 있으면 true. 시황 기사나 순위 목록만 있으면 false
- reason: 한 문장 요약. 재료가 없으면 빈 문자열
- evidence: 근거가 된 뉴스의 번호와, 그 제목에서 글자 그대로 옮긴 짧은 구절. 재료가 없으면 빈 목록
- confidence: 높음, 보통, 낮음"""


class Evidence(BaseModel):
    id: int
    quote: str


class Material(BaseModel):
    has_material: bool
    reason: str
    evidence: list[Evidence]
    confidence: Literal["높음", "보통", "낮음"]


def news_before(con, date, code, t):
    """전 거래일 15:30 초과 ~ 판단 시각 t 미만에 나온, 그 종목이 관련 종목으로 붙은 뉴스."""
    prev = con.execute("SELECT max(date) FROM benchmark_daily WHERE date < ?", [date]).fetchone()[0]
    start = dt.datetime.combine(prev, dt.time(15, 30))
    until = dt.datetime.combine(date, dt.datetime.strptime(t, "%H%M%S").time())
    # 제공처만 다른 같은 제목은 하나만 남긴다 (먼저 나온 것)
    return con.execute("""
        SELECT min(n.news_id), min(n.ts) AS ts, arg_min(n.source, n.ts), n.title, bool_or(s.main)
        FROM news n JOIN news_stock s USING (news_id)
        WHERE s.code = ? AND n.ts > ? AND n.ts < ?
        GROUP BY n.title ORDER BY ts""", [code, start, until]).fetchall()


def norm(s):
    return "".join(s.split()).lower()


def verify(out, items, name):
    """LLM 답의 근거마다 세 가지를 확인한다. 하나라도 어긋나면 그 근거를 버린다.
    items: news_before의 결과 (news_id, ts, source, title, main)"""
    problems, kept = [], []
    for e in out.evidence:
        if not 0 <= e.id < len(items):
            problems.append("없는 번호")
        elif not e.quote.strip() or norm(e.quote) not in norm(items[e.id][3]):
            problems.append("인용 불일치")           # 제목에 없는 말을 지어냄
        elif not (items[e.id][4] or norm(name) in norm(items[e.id][3])):
            problems.append("다른 종목 뉴스")        # 첫 관련 종목도 아니고 제목에 종목명도 없음
        else:
            kept.append(e)
    if out.has_material and not kept:
        problems.append("근거 없음")
    return {"has_material": out.has_material, "verified": out.has_material and bool(kept),
            "reason": out.reason, "evidence": [(e.id, e.quote) for e in kept],
            "problems": problems, "confidence": out.confidence}


def explain(con, date, code, t, name, ret, model=None, items=None):
    items = news_before(con, date, code, t) if items is None else items
    lines = "\n".join(f"{i}. [{ts:%m-%d %H:%M}] {src} | {title}" for i, (_, ts, src, title, _) in enumerate(items))
    user = (f"종목: {name}\n{date} {t[:2]}:{t[2:4]} 기준 전일 대비 {ret:+.1%}\n\n"
            f"[뉴스]\n{lines or '(없음)'}")
    out, usage = ask(SYSTEM, user, Material, model=model)
    return verify(out, items, name), usage


def show(r, items, name, date, t, ret):
    print(f"{name} {date} {t[:2]}:{t[2:4]} {ret:+.2%}, 뉴스 {len(items)}건")
    print(f"  재료: {r['has_material']} → 검증 통과: {r['verified']} ({r['confidence']})  {r['reason']}")
    for i, q in r["evidence"]:
        print(f"  근거 {i}: [{items[i][1]:%H:%M}] {items[i][3]}  ← \"{q}\"")
    if r["problems"]:
        print("  걸러낸 근거:", ", ".join(r["problems"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--time", default="103000")
    ap.add_argument("--code", help="생략하면 그 시각 Leader Score 상위 --top 종목")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    con = connect(args.db, read_only=True)
    date = dt.date.fromisoformat(args.date)
    scores, _ = leader_scores(con, args.date, args.time)     # 6장: 판단 시각 t의 후보와 상승률
    pick = scores[scores.code == args.code] if args.code else scores.head(args.top)
    for r0 in pick.itertuples():
        items = news_before(con, date, r0.code, args.time)
        r, _ = explain(con, date, r0.code, args.time, r0.name, r0.ret, items=items)
        show(r, items, r0.name, date, args.time, r0.ret)


if __name__ == "__main__":
    main()
