"""05-2·05-3·05-4 판단 시점 t의 테마 강도와 주도 테마 TOP N

    python -m ch05_themes.theme_strength --date 2026-10-21 --time 103000
"""

import argparse

import pandas as pd

from ch03_pipeline.db import DB_PATH, connect
from ch04_candidates.features import snapshot

STRONG_RS = 0.03       # 시장보다 3%포인트 이상 강하고, 실제로 오르고 있으면 "강한 종목"
MIN_OBS = 3            # t 시점에 데이터가 있는 테마 종목이 이만큼은 있어야 한다
MIN_STRONG = 2         # 강한 종목이 둘 이상이어야 "무리"로 본다
DEDUP_OVERLAP = 0.5    # 강한 종목이 앞선 테마와 이만큼 겹치면 같은 무리로 보고 건너뛴다


def members_at(con, date, t):
    """t 시점의 후보(04장)에 테마를 붙인다. 한 종목이 여러 테마에 들어갈 수 있다."""
    s = snapshot(con, date, t)
    s = s[s.is_candidate]
    themes = con.execute("SELECT theme_code, theme_name, code FROM theme_stock").df()
    m = themes.merge(s, on="code")
    m["strong"] = (m.rel_strength >= STRONG_RS) & (m.ret > 0)
    m["expected_value"] = m.acc_value / m.value_surge     # 평소 t까지 거래대금
    return m


def positive_ratio(s):
    return (s > 0).mean()


def theme_table(m):
    """테마별 지표와 점수. 점수는 네 지표의 테마 간 백분위 순위를 평균한 값이다."""
    g = m.groupby(["theme_code", "theme_name"])
    t = pd.DataFrame({
        "n_obs": g.code.count(),
        "n_strong": g.strong.sum(),
        "breadth": g.rel_strength.apply(positive_ratio),   # 시장보다 강한 종목 비율
        "med_rs": g.rel_strength.median(),
        "value_surge": g.acc_value.sum() / g.expected_value.sum(),   # 테마 전체의 평소 대비 거래대금
        "value_share": g.value_share.sum(),
    }).reset_index()
    t = t[(t.n_obs >= MIN_OBS) & (t.n_strong >= MIN_STRONG)].copy()
    for col in ("n_strong", "breadth", "med_rs", "value_surge"):
        t["rank_" + col] = t[col].rank(pct=True)
    t["score"] = t.filter(like="rank_").mean(axis=1)
    return t.sort_values("score", ascending=False).reset_index(drop=True)


def top_themes(m, n=5):
    """점수 순으로 고르되, 강한 종목이 앞서 고른 테마와 많이 겹치면 건너뛴다."""
    picked, strong_sets = [], []
    for row in theme_table(m).itertuples():
        strong = set(m[(m.theme_code == row.theme_code) & m.strong].code)
        if any(len(strong & s) / min(len(strong), len(s)) >= DEDUP_OVERLAP for s in strong_sets):
            continue
        picked.append(row)
        strong_sets.append(strong)
        if len(picked) == n:
            break
    return pd.DataFrame(picked)


def leaders(m, theme_code, n=3):
    """테마의 강한 종목을 t까지 거래대금 순으로."""
    x = m[(m.theme_code == theme_code) & m.strong]
    return x.sort_values("acc_value", ascending=False).head(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--time", default="103000")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    m = members_at(connect(args.db, read_only=True), args.date, args.time)
    top = top_themes(m, args.top)
    print(f"{args.date} {args.time[:2]}:{args.time[2:4]} 주도 테마 TOP {args.top} "
          f"(조건을 만족한 테마 {len(theme_table(m))}개 중)\n")
    for i, r in enumerate(top.itertuples(), 1):
        print(f"{i}. {r.theme_name}  점수 {r.score:.3f} | 강한 종목 {r.n_strong}/{r.n_obs}, "
              f"시장보다 강한 비율 {r.breadth:.0%}, 상대강도 중앙값 {r.med_rs * 100:+.2f}%p, "
              f"평소 대비 거래대금 {r.value_surge:.2f}배")
        for x in leaders(m, r.theme_code).itertuples():
            surge = "-" if pd.isna(x.value_surge) else f"{x.value_surge:.1f}배"
            print(f"     {x.name:<14} {x.ret * 100:+6.2f}%  상대강도 {x.rel_strength * 100:+6.2f}%p  "
                  f"거래대금 {x.acc_value / 1e8:>7,.0f}억  평소 대비 {surge}")


if __name__ == "__main__":
    main()
