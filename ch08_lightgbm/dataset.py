"""08-1·08-2 학습 데이터 만들기 — 리플레이(07-1)로 시점별 특성과 미래 정답을 만든다.

    python -m ch08_lightgbm.dataset                       # data/ml_dataset.parquet
    python -m ch08_lightgbm.dataset --start 2026-07-23    # 기간을 줄여서

한 행 = (날짜, 판단 시점 t, 종목). 특성은 t보다 앞선 봉만, 정답만 t 이후의 봉을 쓴다.
"""

import argparse
import datetime as dt
import time

import numpy as np
import pandas as pd

from ch03_pipeline.db import DB_PATH, ROOT, connect
from ch06_leader_score.score import load_themes, score_frame
from ch07_realtime.replay import day_indicators

OUT = ROOT / "data" / "ml_dataset.parquet"
SAMPLE_EVERY = 3            # 5분 grid에서 3칸마다 = 15분 간격으로 판단 시점을 뽑는다
FIRST_STEP = 5              # 개장 30분 뒤(09:30)부터 — 앞의 30분은 30분 변화량을 잴 수 없다
HORIZONS = {"fwd30": 6, "fwd60": 12}   # 5분 grid 기준 몇 칸 뒤인가
PERSIST_DAYS = 5            # 지난 5거래일 내내 분봉 수집 대상이었던 종목 = 오늘 포함 여부가 미래와 거의 무관

FEATURES = [
    "ret", "rel_strength", "bench_ret", "value_share", "value_surge", "log_value",
    "above_vwap", "from_high", "theme_score", "minutes",
    "ret_15", "ret_30", "rs_15", "flow_15",
]


def add_window_features(x):
    """같은 종목의 앞선 grid 시점과 비교한 최근 변화. 모두 t 이전 정보다."""
    x = x.sort_values(["code", "t"]).copy()
    g = x.groupby("code")
    x["ret_15"] = x.price / g.price.shift(3) - 1                       # 최근 15분 수익률
    x["ret_30"] = x.price / g.price.shift(6) - 1                       # 최근 30분 수익률
    bench_15 = (1 + x.bench_ret) / (1 + g.bench_ret.shift(3)) - 1
    x["rs_15"] = x.ret_15 - bench_15                                   # 최근 15분 시장 대비
    x["flow_15"] = (x.acc_value - g.acc_value.shift(3)) / x.acc_value  # 거래대금 중 최근 15분 비중
    return x


def add_labels(x):
    """정답: t 이후 30분·60분 동안의 시장 대비 수익률 (t+k 시점 직전 봉 가격 기준)."""
    g = x.groupby("code")
    for name, k in HORIZONS.items():
        stock = g.price.shift(-k) / x.price - 1
        bench = (1 + g.bench_ret.shift(-k)) / (1 + x.bench_ret) - 1
        x[name] = stock - bench
    return x


def add_persistent(con, df):
    """선택 편향 점검용 표시 (08-5). 분봉 수집 대상은 그날 장 마감 후 거래대금으로 골랐으므로(03-4 규칙 4),
    지난 PERSIST_DAYS거래일 내내 수집 대상이었던 종목만 '포함 여부가 미래와 무관한' 종목으로 본다."""
    done = con.execute("SELECT date, code FROM minute_done WHERE rows > 0").df()
    done["date"] = done.date.astype(str)
    days = sorted(done.date.unique())
    members = done.groupby("date").code.apply(set).to_dict()
    persist = {}
    for i, d in enumerate(days):
        if i >= PERSIST_DAYS:
            persist[d] = set.intersection(*(members[days[j]] for j in range(i - PERSIST_DAYS, i)))
    df["persistent"] = [c in persist.get(d, set()) for d, c in zip(df.date, df.code)]
    return df


def build_day(con, date, themes):
    x = day_indicators(con, date)
    grid = sorted(x.t.unique())
    x = add_labels(add_window_features(x))
    x["minutes"] = x.t.map({t: (i + 1) * 5 for i, t in enumerate(grid)})
    x["log_value"] = np.log10(x.acc_value.clip(lower=1))
    picked = grid[FIRST_STEP::SAMPLE_EVERY]
    rows = []
    for t in picked:
        g = x[(x.t == t) & x.is_candidate]
        if len(g) < 20:
            continue
        scored, _ = score_frame(g, themes)          # theme_score와 비교용 leader_score
        rows.append(scored[["t", "code", "name", "market", "leader_score", "acc_value"] + FEATURES[:-4]
                           + ["ret_15", "ret_30", "rs_15", "flow_15", "fwd30", "fwd60"]].assign(date=date))
    return pd.concat(rows, ignore_index=True) if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-10-15")
    ap.add_argument("--end", default="2026-10-21")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    con = connect(args.db, read_only=True)
    themes = load_themes(con)
    dates = [str(d) for (d,) in con.execute(
        "SELECT DISTINCT date FROM minute_bars WHERE date BETWEEN ? AND ? ORDER BY date",
        [dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)]).fetchall()]
    started, frames = time.time(), []
    for i, d in enumerate(dates, 1):
        day = build_day(con, d, themes)
        if day is not None:
            frames.append(day)
        if i % 25 == 0 or i == len(dates):
            print(f"  {i}/{len(dates)} {d} ({time.time() - started:.0f}초)", flush=True)
    df = add_persistent(con, pd.concat(frames, ignore_index=True))
    OUT.parent.mkdir(exist_ok=True)
    df.to_parquet(OUT)
    print(f"{len(df):,}행, {df.date.nunique()}일, 판단 시점 {df.groupby('date').t.nunique().median():.0f}개/일, "
          f"5일 연속 수집 종목 {df.persistent.mean():.1%} → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
