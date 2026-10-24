"""12-1 대시보드 데이터 — 리플레이(7장)와 실시간 기록(7장 live의 JSONL)을 같은 모양의 표로 만든다.

한 행 = (판단 시각 t, 종목). 열: t, rank, code, name, leader_score, ret, rel_strength, above_vwap,
from_high, theme_name, theme_score, w_* (지표별 기여) ...
"""

import datetime as dt
import json

import pandas as pd

from ch03_pipeline.db import ROOT
from ch07_realtime.live import CONFIRM
from ch07_realtime.replay import replay_day

LIVE_DIR = ROOT / "outputs" / "live"
TOP = 10
HOT_RET_RANK = 5      # 후보 중 상승률 5위 안: 11-5에서 이후 30분이 후보 평균보다 확실히 나빴다


def from_replay(con, date, step=5):
    """저장된 분봉으로 하루를 재생한다 (장이 끝난 날, 샘플 데이터)."""
    r = replay_day(con, date, step)
    r["ret_rank"] = r.groupby("t").ret.rank(ascending=False, method="min")
    meta = {t: {"candidates": len(g), "bench": g.groupby("market").bench_ret.first().to_dict()}
            for t, g in r.groupby("t")}
    return r[r["rank"] <= TOP].reset_index(drop=True), meta


def from_live(date):
    """7장 장중 모니터가 남긴 기록을 읽는다. 시장 등락과 알림도 함께 돌려준다."""
    path = LIVE_DIR / f"{date}.jsonl"
    rows, meta = [], {}
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        for i, x in enumerate(rec["top"], 1):
            rows.append({"t": rec["t"], "rank": i, **x})
        meta[rec["t"]] = {k: rec.get(k) for k in ("bench", "events", "candidates", "leader", "top_theme", "confirmed_theme")}
    df = pd.DataFrame(rows)
    # 2026-10-25에 기록 열을 늘리기 전의 기록에는 없는 열이 있다
    if "ret_rank" not in df:          # 기록된 TOP 10 안의 순위로 대신한다
        df["ret_rank"] = df.groupby("t").ret.rank(ascending=False, method="min")
    if "theme_score" not in df:       # 주도 테마 이름이 있으면 1
        df["theme_score"] = df.theme_name.notna().astype(float)
    return df, meta


def live_dates():
    return sorted((p.stem for p in LIVE_DIR.glob("*.jsonl")), reverse=True)


def leader_changes(df, confirm=CONFIRM):
    """1위가 confirm번 연속 같은 종목일 때만 대장주를 바꾼다 (07-3과 같은 규칙)."""
    leader, pending, n, out = None, None, 0, []
    for t, g in df[df["rank"] == 1].sort_values("t").groupby("t"):
        top = g.name.iloc[0]
        if leader is None:
            leader = top
            out.append((t, None, top))
        elif top == leader:
            pending, n = None, 0
        else:
            n = n + 1 if top == pending else 1
            pending = top
            if n >= confirm:
                out.append((t, leader, top))
                leader, pending, n = top, None, 0
    return pd.DataFrame(out, columns=["t", "old", "new"])


def news_today(code, t):
    """오늘 전 거래일 15:30 이후 ~ t 미만의 뉴스를 API로 바로 받아, 09-3 explain()이 받는 모양으로 만든다."""
    from ch03_pipeline.db import connect
    from ch09_llm.news import fetch_news, ts_of
    from common.kis import KIS
    today = dt.date.today()
    prev = connect(read_only=True).execute(
        "SELECT max(date) FROM benchmark_daily WHERE date < ?", [today]).fetchone()[0]
    until = dt.datetime.combine(today, dt.datetime.strptime(t, "%H%M%S").time())
    _, _, rows = fetch_news(KIS(), today, prev, code)
    seen, items = set(), []
    for r in sorted(rows, key=ts_of):
        title = r["hts_pbnt_titl_cntt"].strip()
        if ts_of(r) < until and title not in seen:       # 같은 제목은 하나만
            seen.add(title)
            items.append((r["cntt_usiq_srno"], ts_of(r), r["dorg"].strip(), title, r["iscd1"].strip() == code))
    return items


FLAG_NOTES = {
    "상승률 상위": "후보 중 상승률 5위 안입니다. 11-5에서 상승률 TOP 5는 이후 30분 동안 후보 평균보다 확실히 약했습니다.",
    "고점 대비 하락": "당일 고점에서 3% 넘게 내려왔습니다. 주도력이 식고 있을 수 있습니다 (07-5).",
    "VWAP 아래": "현재가가 오늘 평균 매수 단가(VWAP)보다 낮습니다. 오늘 산 사람 대부분이 손실입니다 (07-2).",
}


def flags(row):
    """11장 결론과 7장 알림 기준에 따른 주의 표시 (짧은 이름 목록). 설명은 FLAG_NOTES."""
    out = []
    if row.ret_rank <= HOT_RET_RANK:
        out.append("상승률 상위")
    if row.from_high < -0.03:
        out.append("고점 대비 하락")
    if row.above_vwap < 0:
        out.append("VWAP 아래")
    return out
