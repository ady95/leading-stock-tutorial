"""09-4 종목-테마 연관성 — 최근 뉴스(LLM 분류)에서 종목과 테마의 연결을 세고, 5장 테마 사전과 합친다.

    python -m ch09_llm.links --date 2026-10-21
"""

import argparse
import datetime as dt

import pandas as pd

from ch03_pipeline.db import DB_PATH, connect

WINDOW = 20        # 최근 20거래일의 뉴스
MIN_TITLES = 3     # 서로 다른 제목 3개 이상에서 같은 연결이 나와야 인정

# LLM이 "이 종목이 주인공"이라고 본 (종목, 제목) 짝에서 테마를 센다 (09-2)
LINKS_SQL = """
WITH days AS (
    SELECT DISTINCT date FROM benchmark_daily WHERE date <= $end ORDER BY date DESC LIMIT $window + 1)
SELECT DISTINCT l.code, l.title, l.kind, l.direction, l.themes, l.new_theme
FROM news_llm l JOIN news n ON n.title = l.title
JOIN news_stock s ON s.news_id = n.news_id AND s.code = l.code
WHERE l.about
  AND n.ts > (SELECT min(date) FROM days) + INTERVAL '15 hours 30 minutes'
  AND n.ts <= $end::DATE + INTERVAL '15 hours 30 minutes'
"""


def own_titles(con, end, window=WINDOW):
    return con.execute(LINKS_SQL, {"end": end, "window": window}).df()


def theme_links(own, themes):
    """(종목, 테마)마다 서로 다른 제목 수. 사전과 비교해 셋 중 하나로 나눈다.
    LLM이 새 테마 칸에 사전의 테마 이름을 쓰기도 하므로, 어느 칸에 썼는지가 아니라 사전으로 판단한다."""
    known = own.explode("themes").dropna(subset=["themes"]).rename(columns={"themes": "theme_name"})
    new = own[own.new_theme != ""].copy()
    new["theme_name"] = new.new_theme
    links = (pd.concat([known, new]).groupby(["code", "theme_name"]).title.nunique()
             .rename("titles").reset_index())
    in_dict = set(zip(themes.theme_name, themes.code))
    names = set(themes.theme_name)
    links["in_dict"] = [(t, c) in in_dict for t, c in zip(links.theme_name, links.code)]
    links["source"] = ["사전에 있음" if d else "사전 테마의 새 종목" if t in names else "새 테마"
                       for t, d in zip(links.theme_name, links.in_dict)]
    return links.sort_values("titles", ascending=False)


def extended_themes(themes, links, min_titles=MIN_TITLES):
    """5장 테마 사전 + 뉴스로 찾은 연결. score_frame(6장)에 그대로 넣을 수 있는 모양이다."""
    found = links[(links.titles >= min_titles) & ~links.in_dict]
    code_of = dict(zip(themes.theme_name, themes.theme_code))
    add = pd.DataFrame({
        "theme_code": [code_of.get(t, "N:" + t) for t in found.theme_name],   # 새 테마는 N:이름
        "theme_name": found.theme_name.values, "code": found.code.values})
    return pd.concat([themes.loc[:, ["theme_code", "theme_name", "code"]], add], ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    con = connect(args.db, read_only=True)
    end = dt.date.fromisoformat(args.date)
    themes = con.execute("SELECT theme_code, theme_name, code FROM theme_stock").df()
    names = dict(con.execute("SELECT code, name FROM stock_master").fetchall())
    own = own_titles(con, end)
    links = theme_links(own, themes)
    strong = links[links.titles >= MIN_TITLES]
    print(f"{end} 기준 최근 {WINDOW}거래일: 주인공인 짝 {len(own):,}개 (제목 {own.title.nunique():,}개, 종목 {own.code.nunique():,}개)")
    n = strong.source.value_counts()
    print(f"제목 {MIN_TITLES}개 이상 연결 {len(strong):,}개: " +
          ", ".join(f"{k} {n.get(k, 0):,}" for k in ("사전에 있음", "사전 테마의 새 종목", "새 테마")))
    for _, r in strong[~strong.in_dict].head(15).iterrows():
        print(f"  {names.get(r.code, r.code)} — {r.theme_name} ({r.source}, 제목 {r.titles}개)")
    ext = extended_themes(themes, links)
    print(f"확장 사전: {len(themes):,}행 → {len(ext):,}행")


if __name__ == "__main__":
    main()
