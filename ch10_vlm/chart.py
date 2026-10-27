"""10-1·10-2 사람용 차트와 AI 전용 차트 — 판단 시각 t까지의 1분봉으로 그린다.

    python -m ch10_vlm.chart --date 2026-10-21 --time 103000 --code 005930     # outputs/charts/에 두 장
    python -m ch10_vlm.chart --date 2026-10-21 --time 103000                   # 그 시각 Leader Score 1위
"""

import argparse
import io

import matplotlib

matplotlib.use("Agg")                       # 화면 없이 파일로만 그린다
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from ch03_pipeline.db import DB_PATH, ROOT, connect  # noqa: E402
from ch04_candidates.benchmark import BENCHMARKS  # noqa: E402

OUT_DIR = ROOT / "outputs" / "charts"
DAY = pd.date_range("2000-01-01 09:00", "2000-01-01 15:30", freq="1min")   # x축은 하루 전체로 고정

BARS_SQL = """
SELECT code, time, open, high, low, close, volume, acc_value
FROM minute_bars WHERE date = $date AND code IN (SELECT unnest($codes)) AND time < $t
ORDER BY code, time
"""
PREV_SQL = """
SELECT code, arg_max(close, date) AS prev FROM daily_bars
WHERE date < $date AND code IN (SELECT unnest($codes)) GROUP BY code
"""
BENCH_SQL = """
SELECT b.time, b.close / p.prev - 1 AS ret FROM benchmark_minute b,
    (SELECT arg_max(close, date) AS prev FROM benchmark_daily WHERE code = $code AND date < $date) p
WHERE b.code = $code AND b.date = $date AND b.time < $t ORDER BY b.time
"""


def minute_index(times):
    return pd.to_datetime("2000-01-01 " + pd.Series(times).str[:4], format="%Y-%m-%d %H%M")


def load(con, date, t, code, market, members=()):
    """종목·시장·테마의 t 이전 분봉. 수익률은 모두 전일 종가 대비."""
    codes = [code, *members]
    p = {"date": date, "codes": codes, "t": t}
    bars = con.execute(BARS_SQL, p).df()
    prev = dict(con.execute(PREV_SQL, {"date": date, "codes": codes}).fetchall())
    s = bars[bars.code == code].copy()
    s["ts"] = minute_index(s.time).values
    s["ret"] = s.close / prev[code] - 1
    s["high_ret"] = s.high.cummax() / prev[code] - 1                          # 그 시각까지의 당일 고가
    s["vwap_ret"] = (s.acc_value / s.volume.cumsum()) / prev[code] - 1     # VWAP = 누적 거래대금 ÷ 누적 거래량
    s["value"] = s.acc_value.diff().fillna(s.acc_value)                    # 1분 거래대금
    bench = con.execute(BENCH_SQL, {"date": date, "code": BENCHMARKS[market], "t": t}).df()
    bench["ts"] = minute_index(bench.time).values
    theme = None
    if members:
        m = bars[bars.code != code].copy()
        m["ret"] = m.close / m.code.map(prev) - 1
        m["ts"] = minute_index(m.time).values
        # 구성 종목마다 분 단위로 펼쳐 빈 분을 앞 값으로 채운 뒤 평균한다
        wide = m.pivot_table(index="ts", columns="code", values="ret").reindex(s.ts).ffill()
        theme = wide.mean(axis=1).rename("ret").reset_index()
    return s, bench, theme, prev[code]


def summary_line(s, bench, theme):
    """VLM이 눈대중으로 읽기 어려운 값은 글자로 적어 준다 (10-1)."""
    parts = [f"NOW {s.ret.iloc[-1]:+.2%}", f"DAY HIGH {s.high_ret.iloc[-1]:+.2%}",
             f"VWAP {s.vwap_ret.iloc[-1]:+.2%}", f"MARKET {bench.ret.iloc[-1]:+.2%}"]
    if theme is not None:
        parts.append(f"THEME {theme.ret.iloc[-1]:+.2%}")
    return "   ".join(parts)


def ai_chart(s, bench, theme, t, annotate=True, size=(8, 6), dpi=100):
    """AI 전용 차트: 축은 전일 대비 %, 비교선과 VWAP을 겹치고, 종목명·날짜·가격은 넣지 않는다.
    annotate=True면 맨 위에 현재 수치를 글자로 적는다."""
    fig, (ax, axv) = plt.subplots(2, 1, figsize=size, dpi=dpi, sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1]})
    ax.plot(s.ts, s.ret * 100, color="black", lw=2.0, label="STOCK")
    ax.plot(s.ts, s.vwap_ret * 100, color="tab:blue", lw=1.2, ls="--", label="STOCK VWAP")
    ax.plot(bench.ts, bench.ret * 100, color="tab:gray", lw=1.5, label="MARKET")
    if theme is not None:
        ax.plot(theme.ts, theme.ret * 100, color="tab:orange", lw=1.5, label="THEME AVG")
    ax.axhline(0, color="black", lw=0.6)
    now = minute_index([t]).iloc[0]
    for a in (ax, axv):
        a.axvline(now, color="tab:red", lw=1.0)
    ax.text(now, ax.get_ylim()[0], " NOW", color="tab:red", va="bottom", fontsize=11)
    ax.set_ylabel("% vs previous close", fontsize=11)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncol=4, fontsize=10, frameon=False)  # 선을 가리지 않게 바깥에
    ax.grid(alpha=0.3)
    five = s.set_index("ts").value.resample("5min").sum() / 1e8
    axv.bar(five.index, five.values, width=0.003, align="edge", color="tab:green")
    axv.set_ylabel("value / 5min\n(100M KRW)", fontsize=10)
    axv.set_xlim(DAY[0], DAY[-1])
    axv.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    if annotate:
        fig.suptitle(summary_line(s, bench, theme), fontsize=11, x=0.02, ha="left")
    fig.tight_layout()
    return to_png(fig)


def human_chart(s, label, prev, size=(8, 6), dpi=100):
    """사람용 차트(HTS 비슷하게): 원 단위 캔들, 이동평균, 거래량, 머리글의 현재가·등락률."""
    fig, (ax, axv) = plt.subplots(2, 1, figsize=size, dpi=dpi, sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1]})
    c = s.set_index("ts")
    up = c.close >= c.open
    w = 0.0005
    ax.vlines(c.index, c.low, c.high, color=["red" if u else "blue" for u in up], lw=0.6)
    ax.bar(c.index, (c.close - c.open).abs().clip(lower=prev * 0.0005), bottom=c[["open", "close"]].min(axis=1),
           width=w, color=["red" if u else "blue" for u in up])
    ax.plot(c.index, c.close.rolling(5).mean(), color="magenta", lw=0.8, label="MA5")
    ax.plot(c.index, c.close.rolling(20).mean(), color="orange", lw=0.8, label="MA20")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.5)
    last = c.close.iloc[-1]
    ax.set_title(f"{label}   {last:,.0f}  ({last / prev - 1:+.2%})", fontsize=12, loc="left")
    axv.bar(c.index, c.volume, width=w, color=["red" if u else "blue" for u in up])
    axv.set_xlim(DAY[0], DAY[-1])
    axv.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.tight_layout()
    return to_png(fig)


def to_png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)                          # 수천 장을 그릴 때 메모리가 쌓이지 않게 닫는다
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--time", default="103000")
    ap.add_argument("--code", help="생략하면 그 시각 Leader Score 1위 종목")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    from ch10_vlm.cases import pick_stock                    # 6장 점수가 고른 주도 테마의 구성 종목도 함께
    con = connect(args.db, read_only=True)
    code, market, members = pick_stock(con, args.date, args.time, args.code)
    s, bench, theme, prev = load(con, args.date, args.time, code, market, members)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{args.date}_{args.time}_{code}"
    (OUT_DIR / f"{stem}_ai.png").write_bytes(ai_chart(s, bench, theme, args.time))
    (OUT_DIR / f"{stem}_human.png").write_bytes(human_chart(s, code, prev))
    print(f"{OUT_DIR / stem}_ai.png, _human.png (테마 구성 {len(members)}종목)")


if __name__ == "__main__":
    main()
