"""09-1 뉴스 제목 수집 — 날짜별 거래대금 상위 N종목의 뉴스·공시 제목을 모은다.

한 종목의 하루치 뉴스는 "전 거래일 15:30 초과 ~ 그날 15:30 이하"로 정한다.
장 마감 뒤와 주말에 나온 뉴스는 다음 거래일의 뉴스가 된다.

    python -m ch09_llm.news --top 300            # minute_done이 있는 모든 날짜
    python -m ch09_llm.news --top 3 --days 2     # 최근 2일 × 상위 3종목 (시험용)
"""

import argparse
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ch03_pipeline.db import DB_PATH, connect
from common.kis import KIS, KISError

PATH = "/uapi/domestic-stock/v1/quotations/news-title"
CLOSE = "153000"
MAX_PAGES = 15          # 1회 40건 × 15쪽 = 하루 600건까지. 1년 동안 이 상한에 걸린 날은 삼성전자의 몇 날뿐이었다

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    news_id VARCHAR PRIMARY KEY,   -- 콘텐츠 일련번호 (cntt_usiq_srno)
    ts TIMESTAMP,                  -- 발표 시각
    provider VARCHAR,              -- 제공처 코드 (F 거래소 공시, G 코스닥 공시 ...)
    source VARCHAR,                -- 제공처 이름
    title VARCHAR,
    category VARCHAR               -- 제공처의 분류 코드
);
CREATE TABLE IF NOT EXISTS news_stock (    -- 종목코드로 조회했을 때 돌아온 뉴스 = 그 종목의 관련 뉴스
    news_id VARCHAR, code VARCHAR,
    main BOOLEAN,                  -- 응답의 첫 번째 관련 종목(iscd1)이 이 종목인가
    PRIMARY KEY (news_id, code)
);
CREATE TABLE IF NOT EXISTS news_done (
    date DATE, code VARCHAR, rows INTEGER,
    PRIMARY KEY (date, code)
);
"""


def ts_of(r):
    return dt.datetime.strptime(r["data_dt"] + r["data_tm"], "%Y%m%d%H%M%S")


def fetch_news(kis, date, prev_date, code):
    """그날 15:30부터 거꾸로 40건씩 받으며, 전 거래일 15:30에 닿으면 멈춘다."""
    start = dt.datetime.combine(prev_date, dt.time(15, 30))
    end = dt.datetime.combine(date, dt.time(15, 30))
    day, hour, rows = date.strftime("%Y%m%d"), CLOSE, {}
    for _ in range(MAX_PAGES):
        body = kis.get(PATH, "FHKST01011800", {
            "FID_NEWS_OFER_ENTP_CODE": "", "FID_COND_MRKT_CLS_CODE": "", "FID_INPUT_ISCD": code,
            "FID_TITL_CNTT": "", "FID_INPUT_DATE_1": day, "FID_INPUT_HOUR_1": hour,
            "FID_RANK_SORT_CLS_CODE": "", "FID_INPUT_SRNO": ""})
        page = body.get("output") or []
        new = [r for r in page if r["cntt_usiq_srno"] not in rows]
        for r in new:
            rows[r["cntt_usiq_srno"]] = r
        # 같은 초에 나온 뉴스가 페이지 경계에 걸치므로, 다음 페이지는 마지막 행의 시각부터 다시 받고 중복을 버린다
        if not new or len(page) < 40 or ts_of(page[-1]) <= start:
            break
        day, hour = page[-1]["data_dt"], page[-1]["data_tm"]
    return date, code, [r for r in rows.values() if start < ts_of(r) <= end]


def safe_fetch(kis, date, prev_date, code):
    """계속 실패하는 조회는 건너뛴다. news_done에 남기지 않으므로 다시 실행하면 그것만 다시 받는다."""
    try:
        return fetch_news(kis, date, prev_date, code)
    except KISError as e:
        print(f"  건너뜀 {date} {code}: {e}", flush=True)
        return date, code, None


def save(con, code, rows):
    df_news = pd.DataFrame([{
        "news_id": r["cntt_usiq_srno"], "ts": ts_of(r), "provider": r["news_ofer_entp_code"],
        "source": r["dorg"].strip(), "title": r["hts_pbnt_titl_cntt"].strip(),
        "category": r["news_lrdv_code"].strip()} for r in rows])
    # 응답에는 관련 종목이 첫 칸(iscd1)에 하나만 채워져 오는 경우가 대부분이다.
    # 그래서 "어떤 종목으로 조회했나"를 연결로 저장한다
    df_links = pd.DataFrame({"news_id": df_news.news_id, "code": code,
                             "main": [r["iscd1"].strip() == code for r in rows]})
    con.execute("INSERT OR IGNORE INTO news SELECT * FROM df_news")
    con.execute("INSERT OR IGNORE INTO news_stock SELECT * FROM df_links")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=300, help="날짜별 거래대금 상위 N종목")
    ap.add_argument("--days", type=int, help="최근 N거래일만")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    con, kis = connect(args.db), KIS()
    con.execute(SCHEMA)
    # 분봉을 모은 날짜 × 종목만 대상으로 한다 (8장 학습 데이터와 같은 범위)
    targets = con.execute("""
        WITH days AS (
            SELECT date, lag(date) OVER (ORDER BY date) AS prev_date
            FROM (SELECT DISTINCT date FROM benchmark_daily)),
        ranked AS (
            SELECT b.date, b.code, row_number() OVER (PARTITION BY b.date ORDER BY b.value DESC) AS rk
            FROM daily_bars b JOIN minute_done m USING (date, code) WHERE m.rows > 0),
        recent AS (
            SELECT DISTINCT date FROM minute_done ORDER BY date DESC LIMIT ?)
        SELECT r.date, d.prev_date, r.code FROM ranked r JOIN days d USING (date)
        LEFT JOIN news_done n USING (date, code)
        WHERE r.rk <= ? AND n.code IS NULL AND d.prev_date IS NOT NULL
          AND r.date IN (SELECT date FROM recent)
        ORDER BY r.date, r.rk""", [args.days or 100000, args.top]).fetchall()
    print(f"수집 대상 {len(targets)}건 (날짜 × 종목, {kis.env})")

    workers = 8 if kis.env == "real" else 1
    with ThreadPoolExecutor(workers) as ex:
        for i, (date, code, rows) in enumerate(ex.map(lambda t: safe_fetch(kis, *t), targets), 1):
            if rows is None:
                continue
            if rows:
                save(con, code, rows)
            con.execute("INSERT OR REPLACE INTO news_done VALUES (?, ?, ?)", [date, code, len(rows)])
            if i % 500 == 0 or i == len(targets):
                print(f"  {i}/{len(targets)} {date} {code}: {len(rows)}건", flush=True)


if __name__ == "__main__":
    main()
