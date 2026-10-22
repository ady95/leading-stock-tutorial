"""05-1 테마 사전 만들기 — 한국투자증권 테마 코드 파일을 내려받아 저장한다.

    python -m ch05_themes.themes
"""

import datetime as dt
import io
import zipfile

import pandas as pd
import requests

from ch03_pipeline.db import connect

URL = "https://new.real.download.dws.co.kr/common/master/theme_code.mst.zip"

# 테마가 아니라 상장 연도 등으로 묶은 분류. 함께 움직일 이유가 없으므로 뺀다
EXCLUDE_KEYWORDS = ["신규 상장주"]


def download():
    raw = requests.get(URL, timeout=60).content
    return zipfile.ZipFile(io.BytesIO(raw)).read("theme_code.mst").decode("cp949")


def parse(text):
    """한 줄 = 테마코드 3자리 + 테마명 + 종목코드 6자리 + 빈칸 3자리."""
    rows = [{"theme_code": line[0:3], "theme_name": line[3:-9].strip(), "code": line[-9:-3]}
            for line in text.splitlines() if line.strip()]
    df = pd.DataFrame(rows).drop_duplicates(["theme_code", "code"])
    keep = ~df.theme_name.str.contains("|".join(EXCLUDE_KEYWORDS))
    return df[keep].reset_index(drop=True), df[~keep].theme_name.nunique()


def main():
    df, n_excluded = parse(download())
    df_hist = df.assign(updated=dt.date.today())[["updated", "theme_code", "theme_name", "code"]]

    con = connect()
    con.execute("DELETE FROM theme_stock")
    con.execute("INSERT INTO theme_stock SELECT * FROM df")
    con.execute("INSERT OR REPLACE INTO theme_stock_history SELECT * FROM df_hist")

    print(f"테마 {df.theme_code.nunique()}개, 테마-종목 {len(df):,}쌍 (제외한 분류 {n_excluded}개)")
    print(con.execute("""
        SELECT count(DISTINCT u.code) AS universe,
               count(DISTINCT t.code) AS with_theme,
               round(count(DISTINCT t.code) * 100.0 / count(DISTINCT u.code), 1) AS coverage_pct
        FROM stock_master u LEFT JOIN theme_stock t USING (code)
        WHERE u.group_code = 'ST' AND NOT u.is_spac""").df().to_string(index=False))


if __name__ == "__main__":
    main()
