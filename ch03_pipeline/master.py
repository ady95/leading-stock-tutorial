"""03-1 KOSPI·KOSDAQ 종목 마스터 내려받기

    python -m ch03_pipeline.master
"""

import datetime as dt
import io
import zipfile

import pandas as pd
import requests

from ch03_pipeline.db import connect

URL = "https://new.real.download.dws.co.kr/common/master/{}_code.mst.zip"

# 한 줄 = 앞부분(단축코드 9 + 표준코드 12 + 종목명) + 뒷부분(고정폭 속성).
# 뒷부분 칸 너비는 한국투자증권 공식 저장소 stocks_info/kis_*_code_mst.py 기준.
KOSPI_WIDTHS = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8,
                15, 21, 2, 7, 1, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
KOSDAQ_WIDTHS = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7,
                 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
# 시장별 (칸 너비, SPAC 칸 번호, 거래정지 칸 번호)
LAYOUT = {"kospi": (KOSPI_WIDTHS, 19, 34), "kosdaq": (KOSDAQ_WIDTHS, 14, 29)}


def download(market):
    raw = requests.get(URL.format(market), timeout=60).content
    return zipfile.ZipFile(io.BytesIO(raw)).read(f"{market}_code.mst").decode("cp949")


def parse(market, text):
    widths, spac_col, halt_col = LAYOUT[market]
    tail = sum(widths)                     # 뒷부분 길이 (줄바꿈 제외)
    starts = [sum(widths[:i]) for i in range(len(widths))]
    rows = []
    for line in text.splitlines():
        head, attrs = line[:-tail], line[-tail:]
        rows.append({
            "code": head[0:9].strip(),
            "name": head[21:].strip(),
            "market": market.upper(),
            "group_code": attrs[0:2],
            "is_spac": attrs[starts[spac_col]] == "Y",
            "is_halted": attrs[starts[halt_col]] == "Y",
        })
    return pd.DataFrame(rows)


def main():
    df = pd.concat([parse(m, download(m)) for m in LAYOUT], ignore_index=True)
    df["updated"] = dt.date.today()

    con = connect()
    con.execute("DELETE FROM stock_master")
    con.execute("INSERT INTO stock_master SELECT * FROM df")
    # 오늘의 명부를 날짜와 함께 따로 쌓아 두면, 나중에 폐지된 종목도 남는다
    con.execute("""INSERT OR REPLACE INTO stock_master_history
        SELECT updated, code, name, market, group_code, is_spac, is_halted FROM df""")

    print(con.execute("""
        SELECT market, group_code, count(*) AS n,
               count(*) FILTER (WHERE is_spac) AS spac,
               count(*) FILTER (WHERE is_halted) AS halted
        FROM stock_master GROUP BY ALL ORDER BY market, n DESC""").df().to_string(index=False))


if __name__ == "__main__":
    main()
