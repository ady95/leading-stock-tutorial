"""03-3 DuckDB 저장 구조 — 책 전체가 같은 데이터베이스 파일을 쓴다.

    from ch03_pipeline.db import connect
    con = connect()
"""

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "leading_stock.duckdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_master (
    code VARCHAR PRIMARY KEY,      -- 단축코드 (005930)
    name VARCHAR,                  -- 종목명
    market VARCHAR,                -- KOSPI / KOSDAQ
    group_code VARCHAR,            -- ST 주권, EF ETF, EN ETN ...
    is_spac BOOLEAN,
    is_halted BOOLEAN,             -- 거래정지
    updated DATE                   -- 마스터를 받은 날
);
CREATE TABLE IF NOT EXISTS stock_master_history (   -- 날짜별 명부 (생존 편향 대비)
    updated DATE, code VARCHAR, name VARCHAR, market VARCHAR, group_code VARCHAR,
    is_spac BOOLEAN, is_halted BOOLEAN,
    PRIMARY KEY (updated, code)
);
CREATE TABLE IF NOT EXISTS daily_bars (
    date DATE, code VARCHAR,
    open BIGINT, high BIGINT, low BIGINT, close BIGINT,
    volume BIGINT,                 -- 거래량
    value BIGINT,                  -- 거래대금(원)
    PRIMARY KEY (date, code)
);
CREATE TABLE IF NOT EXISTS daily_done (
    code VARCHAR PRIMARY KEY, rows INTEGER
);
CREATE TABLE IF NOT EXISTS minute_bars (
    date DATE, code VARCHAR,
    time VARCHAR,                  -- 체결 시각 HHMMSS
    open BIGINT, high BIGINT, low BIGINT, close BIGINT,
    volume BIGINT,                 -- 그 1분 동안의 거래량
    acc_value BIGINT,              -- 그 시각까지의 누적 거래대금(원)
    PRIMARY KEY (date, code, time)
);
CREATE TABLE IF NOT EXISTS minute_done (
    date DATE, code VARCHAR, rows INTEGER,
    PRIMARY KEY (date, code)
);
"""


def connect(path=DB_PATH, read_only=False):
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    return con
