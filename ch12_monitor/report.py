"""12-3 장 마감 리포트 — 6장 리포트 + 장중 흐름 + 선정 결과의 사후 성과.

장이 끝나고 그날 분봉을 모은 뒤(03-2, 매일 18:30) 실행한다.

    python -m ch12_monitor.report --date 2026-10-21      # outputs/reports/2026-10-21_monitor.md
"""

import argparse

from ch03_pipeline.db import DB_PATH, ROOT, connect
from ch04_candidates.benchmark import BENCHMARKS
from ch06_leader_score.report import build as daily_report
from ch11_backtest.costs import round_trip_cost
from ch11_backtest.outcome import add_outcome, plus_minutes
from ch12_monitor.data import LIVE_DIR, from_live, from_replay, leader_changes

OUT_DIR = ROOT / "outputs" / "reports"
CHECK_TIMES = ["100000", "110000", "133000", "143000"]     # 사후 성과를 볼 시각 (11-5와 같다)
TOP_K = 5

BENCH_SQL = """
SELECT arg_max(close, time) FILTER (WHERE time < $t) AS entry,
       arg_max(close, time) FILTER (WHERE time < $t_end) AS exit_price
FROM benchmark_minute WHERE date = $date AND code = $code
"""


def bench_ret30(con, date, t, market):
    e, x = con.execute(BENCH_SQL, {"date": date, "t": t, "t_end": plus_minutes(t, 30),
                                   "code": BENCHMARKS[market]}).fetchone()
    return x / e - 1 if e and x else float("nan")


def outcome_table(con, date, picks):
    """picks: t, code, name, market 열. 30분 보유 결과와 시장 대비, 비용 차감 후."""
    o = add_outcome(con, picks.assign(date=date))
    o["bench30"] = [bench_ret30(con, date, t, m) for t, m in zip(o.t, o.market)]
    o["rel30"] = o.ret30 - o.bench30
    o["net30"] = o.ret30 - o.entry.map(round_trip_cost, na_action="ignore")   # 진입가가 없으면 비워 둔다
    return o


def build(con, date):
    lines = [daily_report(con, date).rstrip("\n").replace("# 대장주 리포트", "# 장 마감 리포트"), ""]
    replay, _ = from_replay(con, date)
    live_path = LIVE_DIR / f"{date}.jsonl"
    live = from_live(date)[0] if live_path.exists() else None

    # 장중 흐름
    lines += ["## 장중 대장주 흐름 (리플레이, 5분 간격, 2회 연속 확인)", ""]
    for r in leader_changes(replay).itertuples():
        lines.append(f"- {r.t[:2]}:{r.t[2:4]} " + (f"첫 대장주 {r.new}" if r.old is None else f"{r.old} → **{r.new}**"))
    if live is not None:
        lines += ["", f"실시간 모니터 기록: {live.t.nunique()}회, 대장주 교체 {len(leader_changes(live)) - 1}번"]

    # 사후 성과
    lines += ["", f"## 선정 결과의 사후 성과 (Leader Score TOP {TOP_K}, 30분 보유)", "",
              "| 시각 | 종목 | 30분 수익률 | 시장 대비 | MFE | MAE | 비용 차감 후 |", "|---|---|---|---|---|---|---|"]
    picks = replay[replay.t.isin(CHECK_TIMES) & (replay["rank"] <= TOP_K)][["t", "code", "name", "market"]]
    o = outcome_table(con, date, picks)
    for r in o.sort_values(["t"]).itertuples():
        lines.append(f"| {r.t[:2]}:{r.t[2:4]} | {r.name} | {r.ret30:+.2%} | {r.rel30:+.2%} | {r.mfe30:+.2%} | "
                     f"{r.mae30:+.2%} | {r.net30:+.2%} |")
    lines += ["", f"- 평균: 30분 {o.ret30.mean():+.2%}, 시장 대비 {o.rel30.mean():+.2%}, 비용 차감 후 {o.net30.mean():+.2%} "
              f"({len(o)}건 중 비용 차감 후 이익 {(o.net30 > 0).sum()}건)"]

    # 실시간 기록과 리플레이 비교 (같은 규칙이 장중에도 같은 종목을 골랐나)
    if live is not None:
        lines += ["", "## 실시간 기록과 리플레이 비교", "", "| 시각 | 실시간 TOP 5 | 리플레이 TOP 5 | 겹침 |", "|---|---|---|---|"]
        for t in CHECK_TIMES:
            near = [x for x in sorted(live.t.unique()) if x <= t]
            if not near:
                continue
            lv = live[(live.t == near[-1]) & (live["rank"] <= TOP_K)].name.tolist()
            rp = replay[(replay.t == t) & (replay["rank"] <= TOP_K)].name.tolist()
            lines.append(f"| {t[:2]}:{t[2:4]} (실시간 {near[-1][:2]}:{near[-1][2:4]}) | {', '.join(lv)} | "
                         f"{', '.join(rp)} | {len(set(lv) & set(rp))}/{TOP_K} |")
    lines += ["", "> 연구·교육 목적의 규칙 기반 점수이며 투자 권유가 아닙니다. 11장에서 비용을 넘는 성과는 확인되지 않았습니다."]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    text = build(connect(args.db, read_only=True), args.date)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{args.date}_monitor.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"저장: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
