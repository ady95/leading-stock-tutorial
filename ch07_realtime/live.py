"""07-4·07-5 장중 실시간 대장주 탐색 — 5분마다 후보를 모으고 Leader Score를 다시 계산한다.

    python -m ch07_realtime.live                       # 5분 간격, 15:20까지
    python -m ch07_realtime.live --interval 120 --cycles 3

한 주기(cycle)의 흐름
  1. 발견: 거래대금·상승률 순위(KOSPI·KOSDAQ 각 30종목)와 직전 TOP 30을 합쳐 후보 풀을 만든다
  2. 측정: 풀의 종목마다 현재가 API를 불러 가격·누적 거래대금·거래량·고가·VWAP·종목 상태를 받는다
  3. 점수: 6장과 같은 score_frame()으로 Leader Score를 계산한다
  4. 체결강도: 상위 종목만 체결 API로 당일 체결강도를 받는다
  5. 감지: 직전 주기와 비교해 1위 교체, TOP 5 진입·이탈, 1위 테마 교체, 주도력 약화를 알린다
"""

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import pandas as pd

from ch03_pipeline.db import DB_PATH, ROOT, connect
from ch04_candidates.features import BASELINE_DAYS, MIN_DAY_VALUE, MIN_PRICE
from ch05_themes import theme_strength as ts
from ch06_leader_score.score import load_themes, score_frame
from ch07_realtime.replay import FRAC_SQL, HIST_SQL, time_grid
from common.kis import KIS

Q = "/uapi/domestic-stock/v1/quotations"
BENCHMARKS = {"KOSPI": "069500", "KOSDAQ": "229200"}
# 대상 제외 코드: 투자위험·경고·주의 / 관리 / 정리매매 / (불성실공시 포함) / 우선주 / 거래정지 / ETF / ETN / (신용불가 포함) / SPAC
EXCLUDE = "1110111101"
CONFIRM = 2              # 새 1위가 이만큼 연속으로 1위여야 "교체"로 본다 (07-3: 확인 없이는 하루 23번 바뀐다)
OUT_DIR = ROOT / "outputs" / "live"


class Monitor:
    def __init__(self, con, kis, top=10, strength_top=10):
        self.kis, self.top, self.strength_top = kis, top, strength_top
        self.today = dt.date.today()
        d = self.today.isoformat()
        grid, _ = time_grid(d, step=1)                  # 1분 단위 시간대 비중 (가장 가까운 앞 시각을 쓴다)
        base_from = con.execute(
            "SELECT min(date) FROM (SELECT DISTINCT date FROM daily_bars WHERE date < ? ORDER BY date DESC LIMIT ?)",
            [self.today, BASELINE_DAYS]).fetchone()[0]
        self.frac = con.execute(FRAC_SQL, {"date": self.today, "grid": grid, "n": BASELINE_DAYS}).df().sort_values("t")
        self.hist = con.execute(HIST_SQL, {"date": self.today, "base_from": base_from}).df().set_index("code")
        self.master = con.execute("SELECT code, name, market FROM stock_master").df().set_index("code")
        self.themes = load_themes(con)
        self.prev = None
        self.leader, self.pending, self.pending_n = None, None, 0
        self.theme, self.theme_pending, self.theme_pending_n = None, None, 0
        self.calls = 0

    def get(self, path, tr_id, params):
        self.calls += 1
        return self.kis.get(path, tr_id, params)

    # 1. 발견
    def discover(self):
        pool = set()
        for iscd in ("0001", "1001"):                  # KOSPI, KOSDAQ
            rows = self.get(Q + "/volume-rank", "FHPST01710000", {
                "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171", "FID_INPUT_ISCD": iscd,
                "FID_DIV_CLS_CODE": "1", "FID_BLNG_CLS_CODE": "3", "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": EXCLUDE, "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""})["output"]
            pool |= {r["mksc_shrn_iscd"] for r in rows}
            rows = self.get("/uapi/domestic-stock/v1/ranking/fluctuation", "FHPST01700000", {
                "fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20170", "fid_input_iscd": iscd,
                "fid_rank_sort_cls_code": "0", "fid_input_cnt_1": "0", "fid_prc_cls_code": "1",
                "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "", "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": EXCLUDE, "fid_div_cls_code": "0", "fid_rsfl_rate1": "", "fid_rsfl_rate2": ""})["output"]
            pool |= {r["stck_shrn_iscd"] for r in rows}
        if self.prev is not None:                        # 직전 TOP 30은 순위에서 빠져도 계속 지켜본다
            pool |= set(self.prev.code.head(30))
        return sorted(c for c in pool if c in self.master.index and c in self.hist.index)

    # 2. 측정
    def quote(self, code):
        o = self.get(Q + "/inquire-price", "FHKST01010100",
                     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})["output"]
        return {"code": code, "price": int(o["stck_prpr"]), "base_price": int(o["stck_sdpr"]),
                "acc_value": int(o["acml_tr_pbmn"]), "acc_volume": int(o["acml_vol"]),
                "day_high": int(o["stck_hgpr"]), "kis_vwap": float(o["wghn_avrg_stck_prc"]),
                "vi": o.get("vi_cls_code"), "warn": o.get("mrkt_warn_cls_code"), "halt": o.get("temp_stop_yn")}

    def frac_at(self, t):
        f = self.frac[self.frac.t <= t]
        return float(f.frac_t.iloc[-1]) if len(f) else float(self.frac.frac_t.iloc[0])

    def measure(self, codes, t):
        bench = {m: self.quote(c) for m, c in BENCHMARKS.items()}
        bench_ret = {m: q["price"] / q["base_price"] - 1 for m, q in bench.items()}
        x = pd.DataFrame([self.quote(c) for c in codes])
        x = x.join(self.master, on="code").join(self.hist, on="code")
        frac_t = self.frac_at(t)
        # 등락률은 거래소 기준가(전일 종가, 권리락 등 반영)로 계산한다. DB의 전일 종가와 다르면 기록해 둔다
        x["ret"] = x.price / x.base_price - 1
        x["base_mismatch"] = x.base_price != x.prev_close
        x["value_share"] = x.acc_value / x.acc_value.sum()
        x["value_surge"] = x.acc_value / (x.med_value.where(x.med_value > 0) * frac_t)
        x["bench_ret"] = x.market.map(bench_ret)
        x["rel_strength"] = x.ret - x.bench_ret
        x["vwap"] = x.acc_value / x.acc_volume
        x["above_vwap"] = x.price / x.vwap - 1
        x["from_high"] = x.price / x.day_high - 1
        x["is_candidate"] = ((x.price >= MIN_PRICE) & (x.acc_value >= MIN_DAY_VALUE * frac_t)
                             & (x.halt != "Y") & (x.warn == "00"))
        return x, bench_ret, frac_t

    # 4. 체결강도
    def strength(self, codes):
        out = {}
        for c in codes:
            rows = self.get(Q + "/inquire-ccnl", "FHKST01010300",
                            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": c})["output"]
            out[c] = float(rows[0]["tday_rltv"]) if rows else None
        return out

    # 5. 감지
    def confirm_leader(self, top):
        """1위가 CONFIRM번 연속 같은 종목일 때만 대장주를 바꾼다. 바뀌었으면 (옛, 새)를 돌려준다."""
        if self.leader is None:
            self.leader = top
        elif top == self.leader:
            self.pending, self.pending_n = None, 0
        else:
            self.pending_n = self.pending_n + 1 if top == self.pending else 1
            self.pending = top
            if self.pending_n >= CONFIRM:
                old, self.leader = self.leader, top
                self.pending, self.pending_n = None, 0
                return old, top
        return None

    def confirm_theme(self, top):
        """주도 테마 1위도 대장주와 같은 방식으로 확인한다."""
        if top is None:
            return None
        if self.theme is None:
            self.theme = top
        elif top == self.theme:
            self.theme_pending, self.theme_pending_n = None, 0
        else:
            self.theme_pending_n = self.theme_pending_n + 1 if top == self.theme_pending else 1
            self.theme_pending = top
            if self.theme_pending_n >= CONFIRM:
                old, self.theme = self.theme, top
                self.theme_pending, self.theme_pending_n = None, 0
                return old, top
        return None

    def events(self, s, theme_top):
        ev = []
        top5 = list(s.code.head(5))
        changed = self.confirm_leader(top5[0])
        if changed:
            ev.append(f"대장주 교체(확인 {CONFIRM}회): {self.name(changed[0])} → {self.name(changed[1])}")
        if self.prev is not None:
            p5 = list(self.prev.code.head(5))
            for c in set(top5) - set(p5):
                ev.append(f"TOP 5 진입: {self.name(c)}")
            for c in set(p5) - set(top5):
                ev.append(f"TOP 5 이탈: {self.name(c)}")
        theme_changed = self.confirm_theme(theme_top)
        if theme_changed:
            ev.append(f"1위 테마 교체(확인 {CONFIRM}회): {theme_changed[0]} → {theme_changed[1]}")
        for r in s.head(5).itertuples():
            if r.above_vwap < 0 or r.from_high < -0.03:
                ev.append(f"주도력 약화: {r.name} (VWAP 대비 {r.above_vwap * 100:+.1f}%, 고점 대비 {r.from_high * 100:+.1f}%)")
        return ev

    def name(self, code):
        return self.master.name.get(code, code)

    def cycle(self):
        started, calls0 = time.time(), self.calls
        t = dt.datetime.now().strftime("%H%M%S")
        codes = self.discover()
        x, bench_ret, frac_t = self.measure(codes, t)
        cand = x[x.is_candidate]
        s, _ = score_frame(cand, self.themes)
        top_theme = ts.top_themes(ts.attach_themes(cand, self.themes), 1)
        theme_top = top_theme.theme_name.iloc[0] if len(top_theme) else None
        cttr = self.strength(list(s.code.head(self.strength_top)))
        s["cttr"] = s.code.map(cttr)
        ev = self.events(s, theme_top)
        self.prev = s
        return {"t": t, "pool": len(codes), "candidates": len(s), "frac_t": frac_t, "bench": bench_ret,
                "base_mismatch": int(x.base_mismatch.sum()), "leader": self.name(self.leader),
                "top_theme": theme_top, "confirmed_theme": self.theme,
                "calls": self.calls - calls0, "seconds": round(time.time() - started, 1), "events": ev,
                "top": s.head(self.top)[["code", "name", "leader_score", "ret", "rel_strength", "above_vwap",
                                         "from_high", "theme_name", "cttr", "price", "kis_vwap", "vwap"]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=300, help="주기(초)")
    ap.add_argument("--until", default="152000", help="이 시각이 지나면 멈춘다 (HHMMSS)")
    ap.add_argument("--cycles", type=int, help="이만큼만 돌고 멈춘다")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)   # 파일로 출력을 돌려도 주기마다 바로 기록되게

    mon = Monitor(connect(args.db, read_only=True), KIS())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log = OUT_DIR / f"{mon.today}.jsonl"
    n = 0
    while dt.datetime.now().strftime("%H%M%S") < args.until and (args.cycles is None or n < args.cycles):
        start = time.time()
        r = mon.cycle()
        n += 1
        print(f"\n[{r['t'][:2]}:{r['t'][2:4]}:{r['t'][4:]}] 풀 {r['pool']} · 후보 {r['candidates']} · "
              f"API {r['calls']}회 · {r['seconds']}초 · KOSPI {r['bench']['KOSPI'] * 100:+.2f}% "
              f"KOSDAQ {r['bench']['KOSDAQ'] * 100:+.2f}%")
        for i, row in enumerate(r["top"].head(5).itertuples(), 1):
            cttr = "-" if pd.isna(row.cttr) else f"{row.cttr:.1f}"
            theme = row.theme_name if isinstance(row.theme_name, str) else "-"
            print(f"  {i}. {row.name:<12} {row.leader_score:.3f} {row.ret * 100:+6.2f}% 체결강도 {cttr:>6}  {theme}")
        for e in r["events"]:
            print(f"  ! {e}")
        with log.open("a", encoding="utf-8") as f:
            rec = {k: v for k, v in r.items() if k != "top"}
            rec["top"] = r["top"].to_dict(orient="records")
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        time.sleep(max(0, args.interval - (time.time() - start)))
    print(f"\n{n}회 실행, 기록: {log.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
