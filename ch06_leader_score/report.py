"""06-4 장 마감 후 대장주 TOP 10과 주도 테마 TOP 5 리포트

    python -m ch06_leader_score.report --date 2026-10-21          # outputs/reports/2026-10-21.md
"""

import argparse
import datetime as dt
from pathlib import Path

from ch03_pipeline.db import DB_PATH, ROOT, connect
from ch03_pipeline.minutes import CLOSE, SESSIONS
from ch05_themes import theme_strength as ts
from ch06_leader_score.score import LABELS, leader_scores, reason

OUT_DIR = ROOT / "outputs" / "reports"


def after_close(date):
    """그날 정규장 마지막 봉(15:30, 수능일 16:30)까지 포함하는 시각."""
    close_t = SESSIONS.get(date.replace("-", ""), (None, CLOSE))[1]
    return (dt.datetime.strptime(close_t, "%H%M%S") + dt.timedelta(minutes=1)).strftime("%H%M%S")


def build(con, date):
    t = after_close(date)
    scores, used = leader_scores(con, date, t)
    m = ts.members_at(con, date, t)
    themes = ts.top_themes(m, 5)
    total = sum(used.values())
    bench = scores.groupby("market").bench_ret.first()

    lines = [f"# 대장주 리포트 — {date}", "",
             f"- 시장: KOSPI 기준 ETF {bench.get('KOSPI', float('nan')) * 100:+.2f}%, "
             f"KOSDAQ 기준 ETF {bench.get('KOSDAQ', float('nan')) * 100:+.2f}% (전일 종가 대비)",
             f"- 후보: {len(scores)}종목 (유동성 필터 통과, 04-3)",
             "- 가중치: " + ", ".join(f"{LABELS[k]} {w / total:.0%}" for k, w in used.items())
             + " (뉴스·공시는 9장 전까지 제외)", "",
             "## 주도 테마 TOP 5", ""]
    if themes.empty:
        lines.append("조건을 만족한 주도 테마가 없습니다.")
    for i, r in enumerate(themes.itertuples(), 1):
        names = ", ".join(f"{x.name}({x.ret * 100:+.1f}%)" for x in ts.leaders(m, r.theme_code).itertuples())
        lines.append(f"{i}. **{r.theme_name}** — 강한 종목 {r.n_strong}/{r.n_obs}, "
                     f"상대강도 중앙값 {r.med_rs * 100:+.2f}%p · {names}")

    lines += ["", "## 대장주 TOP 10 (Leader Score)", "",
              "| 순위 | 종목 | 점수 | 등락률 | 선정 근거 (기여가 큰 순) |", "|---|---|---|---|---|"]
    for i, r in enumerate(scores.head(10).itertuples(), 1):
        lines.append(f"| {i} | {r.name} | {r.leader_score:.3f} | {r.ret * 100:+.2f}% | {reason(r, used)} |")

    top_ret = scores.sort_values("ret", ascending=False).head(10)
    overlap = len(set(top_ret.code) & set(scores.head(10).code))
    lines += ["", f"참고: 후보 중 상승률 상위 10종목과 겹치는 종목 {overlap}개", "",
              "> 이 리포트는 연구·교육 목적의 규칙 기반 점수이며 투자 권유가 아닙니다."]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    text = build(connect(args.db, read_only=True), args.date)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{args.date}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"저장: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
