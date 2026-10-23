"""06-1·06-2·06-3 규칙 기반 Leader Score

판단 시점 t의 후보(04장)마다 여섯 지표를 백분위 순위(0~1)로 바꾸고, 가중 평균한 값이 Leader Score다.

    python -m ch06_leader_score.score --date 2026-10-21 --time 103000
"""

import argparse

import pandas as pd

from ch03_pipeline.db import DB_PATH, connect
from ch04_candidates.features import snapshot
from ch05_themes import theme_strength as ts

# 연구용 초기 가중치 (기획 단계에서 정한 값, 검증된 값이 아니다)
WEIGHTS = {
    "money": 0.25,       # 거래대금 집중도
    "strength": 0.20,    # 상대 주가 강도
    "theme": 0.20,       # 테마 주도력
    "buying": 0.15,      # 실시간 매수세
    "news": 0.10,        # 뉴스·공시 영향력 — 후보 표에 news_score 열이 있을 때만 (9장)
    "hold": 0.10,        # 상승 지속 가능성
}
LABELS = {"money": "거래대금", "strength": "상대강도", "theme": "테마 주도력",
          "buying": "매수세", "news": "뉴스·공시", "hold": "지속성"}

INTRADAY_SQL = """
SELECT code, sum(volume) AS acc_volume, max(high) AS day_high, min(low) AS day_low
FROM minute_bars WHERE date = $date AND time < $t GROUP BY code
"""


def pct_rank(s, method="average"):
    """그날 후보 안에서의 백분위 순위. 값이 없으면 가운데(0.5)로 둔다."""
    return s.rank(pct=True, method=method).fillna(0.5)


def add_intraday(cand):
    """매수세·지속성 원지표. 필요한 열: price, acc_value, acc_volume, day_high."""
    cand = cand.copy()
    cand["vwap"] = cand.acc_value / cand.acc_volume                 # 당일 거래대금 ÷ 거래량
    cand["above_vwap"] = cand.price / cand.vwap - 1                  # VWAP보다 얼마나 위에 있나
    cand["from_high"] = cand.price / cand.day_high - 1               # 당일 고점에서 얼마나 내려왔나
    return cand


def indicators(con, date, t):
    """DB의 1분봉으로 t 시점 후보의 원지표를 만든다 (6장, 7장 리플레이)."""
    snap = snapshot(con, date, t)
    cand = snap[snap.is_candidate]
    intraday = con.execute(INTRADAY_SQL, {"date": pd.Timestamp(date).date(), "t": t}).df()
    return add_intraday(cand.merge(intraday, on="code"))


def score_frame(cand, themes, weights=WEIGHTS):
    """원지표가 담긴 후보 표에 테마 주도력과 Leader Score를 붙인다.

    DB(6장)에서 왔든 실시간 API(7장)에서 왔든, 열 이름만 같으면 같은 점수가 나온다.
    """
    c = cand.copy()
    m = ts.attach_themes(c, themes)
    table = ts.theme_table(m)
    strong = m[m.strong].merge(table[["theme_code", "score"]], on="theme_code")
    best = strong.sort_values("score", ascending=False).drop_duplicates("code")
    c = c.merge(best[["code", "theme_name", "score"]].rename(columns={"score": "theme_score"}),
                on="code", how="left")
    c["theme_score"] = c.theme_score.fillna(0.0)

    c["s_money"] = (pct_rank(c.value_share) + pct_rank(c.value_surge)) / 2
    c["s_strength"] = pct_rank(c.rel_strength)
    c["s_theme"] = pct_rank(c.theme_score, method="min")   # 주도 테마가 없으면(동점 0) 맨 아래
    c["s_buying"] = pct_rank(c.above_vwap)
    c["s_hold"] = pct_rank(c.from_high)
    if "news_score" in c.columns:                         # 9장: 뉴스·공시 영향력 (09-5)
        c["s_news"] = pct_rank(c.news_score, method="min")

    used = {k: w for k, w in weights.items() if f"s_{k}" in c.columns}
    total = sum(used.values())
    for k, w in used.items():
        c[f"w_{k}"] = w / total * c[f"s_{k}"]          # 점수에 대한 기여
    c["leader_score"] = c[[f"w_{k}" for k in used]].sum(axis=1)   # 기여 열을 이름으로 정확히 더한다
    return c.sort_values("leader_score", ascending=False).reset_index(drop=True), used


def load_themes(con):
    return con.execute("SELECT theme_code, theme_name, code FROM theme_stock").df()


def leader_scores(con, date, t, weights=WEIGHTS):
    """DB 기준 t 시점의 Leader Score (6장)."""
    return score_frame(indicators(con, date, t), load_themes(con), weights)


def reason(row, used):
    """선정 근거: 기여가 큰 지표 순으로 원지표 값을 붙인 한 줄 설명."""
    parts = {
        "money": f"거래대금 {row.acc_value / 1e8:,.0f}억(집중도 {row.value_share:.1%}, 평소의 "
                 + ("-" if pd.isna(row.value_surge) else f"{row.value_surge:.1f}배") + ")",
        "strength": f"시장 대비 {row.rel_strength * 100:+.1f}%p",
        "theme": f"주도 테마 '{row.theme_name}'" if row.theme_score > 0 else "주도 테마 없음",
        "buying": f"VWAP 대비 {row.above_vwap * 100:+.1f}%",
        "hold": f"고점 대비 {row.from_high * 100:+.1f}%",
    }
    if hasattr(row, "news_score"):
        parts["news"] = f"호재 뉴스 {row.good_n}건 · 악재 {row.bad_n}건"
    order = sorted(used, key=lambda k: getattr(row, f"w_{k}"), reverse=True)
    return " · ".join(parts[k] for k in order if k in parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--time", default="103000")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    scores, used = leader_scores(connect(args.db, read_only=True), args.date, args.time)
    weights = ", ".join(f"{LABELS[k]} {w / sum(used.values()):.0%}" for k, w in used.items())
    print(f"{args.date} {args.time[:2]}:{args.time[2:4]} Leader Score TOP {args.top} (후보 {len(scores)}종목)")
    print(f"적용 가중치: {weights} (뉴스·공시는 9장 전까지 제외)\n")
    for i, r in enumerate(scores.head(args.top).itertuples(), 1):
        print(f"{i:>2}. {r.name:<14} {r.leader_score:.3f}  {r.ret * 100:+6.2f}%  | {reason(r, used)}")


if __name__ == "__main__":
    main()
