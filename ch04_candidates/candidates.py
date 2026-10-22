"""04-4 오늘의 대장주 후보 추리기 — 판단 시점 t의 후보와 상승률 순위를 비교한다.

    python -m ch04_candidates.candidates --date 2026-10-21 --time 103000
    python -m ch04_candidates.candidates --date 2026-10-21 --time 103000 --top 30
"""

import argparse

from ch03_pipeline.db import DB_PATH, connect
from ch04_candidates.features import snapshot


def show(df, title, n):
    print(f"[{title}]")
    out = df.head(n).assign(
        ret=lambda x: (x.ret * 100).round(2), share=lambda x: (x.value_share * 100).round(2),
        surge=lambda x: x.value_surge.round(2), rs=lambda x: (x.rel_strength * 100).round(2),
        value_eok=lambda x: (x.acc_value / 1e8).round(0))
    print(out[["name", "market", "ret", "share", "surge", "rs", "value_eok"]].to_string(index=False), end="\n\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--time", default="103000", help="판단 시각 HHMMSS (이 시각보다 앞선 봉만 쓴다)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    df = snapshot(connect(args.db, read_only=True), args.date, args.time)
    cand = df[df.is_candidate]
    print(f"{args.date} {args.time[:2]}:{args.time[2:4]} 기준 — 분봉이 있는 {len(df)}종목 중 후보 {len(cand)}종목 "
          f"(t까지 최소 거래대금 {df.min_value_t.iloc[0] / 1e8:.1f}억, 평소 비중 {df.frac_t.iloc[0] * 100:.1f}%)\n")

    show(cand, f"대장주 후보 — t까지 거래대금 상위 {args.top}", args.top)
    show(df.sort_values("ret", ascending=False), f"참고: 상승률 상위 {args.top} (필터 전)", args.top)

    top_ret = df.sort_values("ret", ascending=False).head(args.top)
    failed = top_ret[~top_ret.is_candidate]
    print(f"상승률 상위 {args.top} 중 필터에서 빠진 종목 {len(failed)}개:")
    for r in failed.itertuples():
        why = [k for k, ok in (("우선주", r.pass_pref), ("1,000원 미만", r.pass_price),
                                ("봉 부족", r.pass_bars), ("거래대금 부족", r.pass_value)) if not ok]
        print(f"  {r.name} ({', '.join(why)})")


if __name__ == "__main__":
    main()
