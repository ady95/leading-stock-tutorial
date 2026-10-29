"""12-4 가상 장부로 신호 검증하기 — 실제 주문 없이, 신호가 났을 때 샀다고 보고 결과를 기록한다.

신호: 대장주 교체(07-3, 2회 연속 확인)가 확인된 시각에 새 대장주를 산다.
매매: 그 시각 직전 봉 종가에 사서 30분 뒤 판다 (11-2와 같은 규칙), 왕복 비용(11-3)을 뺀다.

    python -m ch12_monitor.paper --start 2026-10-01 --end 2026-10-21     # outputs/paper_ledger.csv
"""

import argparse

import pandas as pd

from ch03_pipeline.db import DB_PATH, ROOT, connect
from ch12_monitor.data import from_replay, leader_changes
from ch12_monitor.report import outcome_table

LEDGER = ROOT / "outputs" / "paper_ledger.csv"
LAST_ENTRY = "145000"      # 30분 보유가 장 마감 동시호가(15:20) 전에 끝나도록


def day_signals(con, date):
    replay, _ = from_replay(con, date)
    ch = leader_changes(replay)
    ch = ch[ch.old.notna() & (ch.t <= LAST_ENTRY)]                # 첫 대장주는 교체가 아니다
    first = replay.loc[replay["rank"] == 1, ["t", "code", "name", "market"]]
    return ch.merge(first, left_on=["t", "new"], right_on=["t", "name"]).loc[:, ["t", "code", "name", "market"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    con = connect(args.db, read_only=True)
    days = [str(d) for (d,) in con.execute(
        "SELECT DISTINCT date FROM minute_done WHERE rows > 0 AND date BETWEEN ? AND ? ORDER BY date",
        [args.start, args.end]).fetchall()]
    rows = []
    for d in days:
        sig = day_signals(con, d)
        if len(sig):
            rows.append(outcome_table(con, d, sig))
        print(f"  {d}: 신호 {len(sig)}건", flush=True)
    led = pd.concat(rows, ignore_index=True)
    led = led[["date", "t", "code", "name", "entry", "exit_price", "ret30", "rel30", "mfe30", "mae30", "net30"]]
    LEDGER.parent.mkdir(exist_ok=True)
    led.to_csv(LEDGER, index=False, encoding="utf-8-sig")

    daily = led.groupby("date").net30.mean()
    print(f"\n{len(days)}일, 신호 {len(led)}건 (하루 평균 {len(led) / len(days):.1f}건) → {LEDGER.relative_to(ROOT)}")
    print(f"30분 수익률 평균 {led.ret30.mean():+.2%}, 시장 대비 {led.rel30.mean():+.2%}, MFE {led.mfe30.mean():+.2%}, "
          f"MAE {led.mae30.mean():+.2%}")
    print(f"비용 차감 후 평균 {led.net30.mean():+.2%}, 이익 거래 {(led.net30 > 0).mean():.1%}, "
          f"이익 난 날 {(daily > 0).mean():.1%}")
    won = led.net30 * 1_000_000
    print(f"신호마다 100만 원씩 샀다면 손익 합계 {won.sum():+,.0f}원 (거래당 {won.mean():+,.0f}원, 비용 차감 후)")


if __name__ == "__main__":
    main()
