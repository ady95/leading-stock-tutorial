"""09-5 뉴스·LLM 특성 — (날짜, 판단 시각 t, 종목)마다 t 이전에 나온 뉴스를 센다.

    from ch09_llm.features import add_news_features
    df = add_news_features(con, df)      # df: date, t, code 열이 있는 표 (8장 학습 데이터)
"""

import pandas as pd

NEWS_FEATURES = ["news_n", "own_n"]          # LLM 없이 세는 것
LLM_FEATURES = ["good_n", "bad_n"]           # LLM 분류(09-2)가 있어야 하는 것

# 전 거래일 15:30 초과 ~ t 미만 (규칙 1: 판단 시각 t보다 앞선 정보만). 같은 제목은 한 번만 센다
FEATURE_SQL = """
WITH days AS (
    SELECT date, lag(date) OVER (ORDER BY date) AS prev FROM (SELECT DISTINCT date FROM benchmark_daily)),
x AS (
    SELECT r.date, r.t, r.code, n.title, bool_or(s.main) AS main,
           contains(replace(lower(n.title), ' ', ''), replace(lower(m.name), ' ', '')) AS named
    FROM rows r JOIN days d ON d.date = r.date::DATE
    JOIN news_stock s ON s.code = r.code JOIN news n USING (news_id)
    JOIN stock_master m ON m.code = r.code
    WHERE n.ts > d.prev + INTERVAL '15 hours 30 minutes' AND n.ts < r.t_ts
    GROUP BY ALL)
SELECT x.date, x.t, x.code,
       count(*) AS news_n,
       count(*) FILTER (WHERE x.main OR x.named) AS own_n,      -- 관련성 1차 조건 (09-2)
       count(*) FILTER (WHERE l.about AND l.kind IN ('개별재료', '공시') AND l.direction = '호재') AS good_n,
       count(*) FILTER (WHERE l.about AND l.kind IN ('개별재료', '공시') AND l.direction = '악재') AS bad_n,
       count(*) FILTER (WHERE (x.main OR x.named) AND l.code IS NULL) AS unlabeled_n
FROM x LEFT JOIN news_llm l ON l.code = x.code AND l.title = x.title
GROUP BY ALL
"""


def add_news_features(con, df):
    rows = df[["date", "t", "code"]].drop_duplicates().copy()
    rows["t_ts"] = pd.to_datetime(rows.date.astype(str) + " " + rows.t, format="%Y-%m-%d %H%M%S")
    feats = con.execute(FEATURE_SQL).df()        # rows는 위의 DataFrame
    feats["date"] = feats.date.astype(str)
    out = df.merge(feats, on=["date", "t", "code"], how="left")
    cols = NEWS_FEATURES + LLM_FEATURES + ["unlabeled_n"]
    out[cols] = out[cols].fillna(0).astype(int)  # 뉴스가 없으면 0건
    out["news_score"] = out.good_n - out.bad_n   # 6장 Leader Score의 '뉴스·공시' 원지표
    return out
