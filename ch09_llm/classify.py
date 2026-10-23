"""09-2 LLM 뉴스 분류 — (종목, 제목) 짝마다 주인공 여부·종류·방향·테마를 붙인다.

같은 짝은 한 번만 분류하고, 이미 분류한 짝은 건너뛴다.

    python -m ch09_llm.classify --start 2026-10-21                 # 관련성 1차 조건을 통과한 짝만
    python -m ch09_llm.classify --start 2026-10-21 --all-pairs     # 시황·순위 기사까지 모두
"""

import argparse
import datetime as dt
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from ch03_pipeline.db import DB_PATH, connect
from ch09_llm.llm import ask

BATCH = 40              # 한 번에 보내는 짝 수
SCHEMA = """
CREATE TABLE IF NOT EXISTS news_llm (
    code VARCHAR, title VARCHAR,
    about BOOLEAN,         -- 이 종목이 제목의 주인공(직접 당사자)인가
    kind VARCHAR,          -- 개별재료 / 공시 / 시황 / 수급순위 / 기타
    direction VARCHAR,     -- 이 종목 주가에 호재 / 악재 / 중립
    themes VARCHAR[],      -- 테마 사전에 있는 이름만
    new_theme VARCHAR,     -- 사전에 없는 새 테마 (없으면 '')
    model VARCHAR, created TIMESTAMP,
    PRIMARY KEY (code, title)
);
"""
# 순위표·브리핑처럼 제목 모양만으로 알 수 있는 것은 LLM에 보내지 않는다
LIST_PATTERN = re.compile(r"상위\s?\d*\s?종목|상위종목|모닝 브리핑|마감시황|장중시황|증시요약|순매수,도")

# 하루치 뉴스(전 거래일 15:30 초과 ~ 그날 15:30) 중, 아직 분류하지 않은 (종목, 제목) 짝
PAIRS_SQL = """
WITH days AS (
    SELECT date, lag(date) OVER (ORDER BY date) AS prev FROM (SELECT DISTINCT date FROM benchmark_daily))
SELECT DISTINCT t.code, m.name, n.title FROM targets t JOIN days d USING (date)
JOIN news_stock s ON s.code = t.code JOIN news n USING (news_id)
JOIN stock_master m ON m.code = t.code
LEFT JOIN news_llm l ON l.code = t.code AND l.title = n.title
WHERE l.code IS NULL AND $first
  AND n.ts > d.prev + INTERVAL '15 hours 30 minutes' AND n.ts <= t.date + INTERVAL '15 hours 30 minutes'
ORDER BY t.code, n.title
"""
# 관련성 1차 조건 (LLM에 보내기 전 비용을 줄이는 거름망):
# 응답의 첫 관련 종목이 이 종목이거나, 제목에 종목명이 그대로 나온다
FIRST = "(s.main OR contains(replace(lower(n.title), ' ', ''), replace(lower(m.name), ' ', '')))"

SYSTEM = """너는 한국 주식 뉴스 제목을 분류한다. 항목마다 [종목명]과 제목이 짝지어 있다.
제목에 적힌 내용만 근거로 삼고, 제목에 없는 사실은 추측하지 않는다.
- about: 그 종목이 제목의 주인공 또는 직접 당사자면 true. 약칭(예: 삼전, SKT, KAI)도 같은 회사로 본다.
  이름이 비슷한 다른 회사이거나, 여러 종목 중 하나로 잠깐 언급되거나, 시황·순위 목록이면 false
- kind: 개별재료(특정 기업의 실적·수주·계약·신제품·투자·인수합병·임상·인허가 등), 공시(거래소·코스닥 공시 제목),
  시황(지수·업종·경제 전반), 수급순위(매매 상위 목록·브리핑), 기타
- direction: 그 종목의 주가에 호재, 악재, 중립
- themes: 아래 [테마 목록]에서 해당하는 이름을 목록에 적힌 그대로. 해당이 없으면 빈 목록
- new_theme: [테마 목록]에 없는 투자 테마가 제목에 분명히 드러나면 짧은 이름, 아니면 빈 문자열"""


class Label(BaseModel):
    id: int
    about: bool
    kind: Literal["개별재료", "공시", "시황", "수급순위", "기타"]
    direction: Literal["호재", "악재", "중립"]
    themes: list[str]
    new_theme: str


class Labels(BaseModel):
    items: list[Label]


def theme_names(con):
    return [r[0] for r in con.execute(
        "SELECT DISTINCT theme_name FROM theme_stock ORDER BY theme_name").fetchall()]


def classify_batch(pairs, themes):
    """pairs: [(code, name, title), ...] → {(code, title): 라벨}, 사용 토큰, 버린 테마 이름 수"""
    user = "[테마 목록]\n" + ", ".join(themes) + "\n\n[항목]\n" + "\n".join(
        f"{i}. [{name}] {title}" for i, (_, name, title) in enumerate(pairs))
    out, usage = ask(SYSTEM, user, Labels)
    known, rows, dropped = set(themes), {}, 0
    for x in out.items:
        if 0 <= x.id < len(pairs):
            code, _, title = pairs[x.id]
            kept = [t for t in x.themes if t in known]     # 목록에 없는 이름(LLM이 지어낸 이름)은 버린다
            dropped += len(x.themes) - len(kept)
            rows[(code, title)] = (x.about, x.kind, x.direction, kept, x.new_theme.strip())
    return rows, usage, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="이 날짜부터의 뉴스")
    ap.add_argument("--end", default="2100-01-01")
    ap.add_argument("--targets", help="(date, code) 열이 있는 parquet. 그 날짜·종목의 뉴스만")
    ap.add_argument("--all-pairs", action="store_true", help="1차 조건 없이 모든 짝을 분류")
    ap.add_argument("--limit", type=int, help="분류할 짝 수 상한 (시험용)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    con = connect(args.db)
    con.execute(SCHEMA)
    themes = theme_names(con)
    targets = con.execute("SELECT date, code FROM news_done WHERE date BETWEEN ? AND ?",
                          [args.start, args.end]).df()
    if args.targets:                       # 09-5: Leader Score 상위 후보의 (날짜, 종목)만
        keep = pd.read_parquet(args.targets)[["date", "code"]].astype(str).drop_duplicates()
        targets = targets.astype(str).merge(keep).astype({"date": "datetime64[ns]"})
    sql = PAIRS_SQL.replace("$first", "TRUE" if args.all_pairs else FIRST)
    pairs = con.execute(sql).fetchall()          # targets는 위의 DataFrame
    if args.limit:
        pairs = pairs[:args.limit]
    listed = [p for p in pairs if LIST_PATTERN.search(p[2])]
    todo = [p for p in pairs if not LIST_PATTERN.search(p[2])]
    print(f"분류할 짝 {len(pairs):,}개 (제목 {len({p[2] for p in pairs}):,}개): "
          f"규칙으로 처리 {len(listed):,}개, LLM {len(todo):,}개")

    now, model = dt.datetime.now(), os.environ["OPENAI_MODEL"]
    rule = pd.DataFrame({"code": [p[0] for p in listed], "title": [p[2] for p in listed], "about": False,
                         "kind": "수급순위", "direction": "중립", "themes": [[] for _ in listed],
                         "new_theme": "", "model": "rule", "created": now})
    if len(rule):
        con.execute("INSERT OR IGNORE INTO news_llm SELECT * FROM rule")

    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    tokens, dropped = [0, 0], 0
    with ThreadPoolExecutor(args.workers) as ex:
        for i, (rows, usage, drop) in enumerate(ex.map(lambda b: classify_batch(b, themes), batches), 1):
            df = pd.DataFrame([{"code": c, "title": t, "about": a, "kind": k, "direction": d, "themes": th,
                                "new_theme": nt, "model": model, "created": dt.datetime.now()}
                               for (c, t), (a, k, d, th, nt) in rows.items()])
            if len(df):
                con.execute("INSERT OR IGNORE INTO news_llm SELECT * FROM df")
            tokens[0] += usage.input_tokens
            tokens[1] += usage.output_tokens
            dropped += drop
            if i % 20 == 0 or i == len(batches):
                print(f"  {i}/{len(batches)} 묶음, 토큰 입력 {tokens[0]:,} 출력 {tokens[1]:,}, "
                      f"목록에 없어 버린 테마 이름 {dropped}개", flush=True)


if __name__ == "__main__":
    main()
