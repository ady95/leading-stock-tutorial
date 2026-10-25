"""12-1·12-2 AI 대장주 모니터 (Streamlit)

    streamlit run ch12_monitor/app.py

- 리플레이: 저장된 분봉으로 지난 날을 5분 간격으로 다시 본다 (샘플 데이터로도 된다)
- 실시간 기록: 7장 장중 모니터(python -m ch07_realtime.live)가 남기는 outputs/live/날짜.jsonl을 1분마다 다시 읽는다
"""

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # streamlit run으로 실행해도 예제 모듈을 찾게

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from ch03_pipeline.db import DB_PATH, connect  # noqa: E402
from ch05_themes import theme_strength as ts  # noqa: E402
from ch06_leader_score.score import LABELS  # noqa: E402
from ch12_monitor.data import FLAG_NOTES, flags, from_live, from_replay, leader_changes, live_dates  # noqa: E402

st.set_page_config(page_title="AI 대장주 모니터", layout="wide")
DB = os.getenv("LEADING_DB", str(DB_PATH))


@st.cache_resource
def db():
    return connect(DB, read_only=True)


@st.cache_data(show_spinner="하루를 재생하는 중...")
def replay(date):
    return from_replay(db(), date)


@st.cache_data
def replay_dates():
    return [str(d) for (d,) in db().execute(
        "SELECT DISTINCT date FROM minute_done WHERE rows > 0 ORDER BY date DESC").fetchall()]


@st.cache_data
def themes_at(date, t):
    m = ts.members_at(db(), date, t)
    return ts.top_themes(m, 5)


def hhmm(t):
    return f"{t[:2]}:{t[2:4]}"


def load_day(mode, date):
    return replay(date) if mode == "리플레이" else from_live(date)


# ---- 사이드바: 모드와 날짜 ----
mode = st.sidebar.radio("데이터", ["리플레이", "실시간 기록"])
if mode == "리플레이":
    days = replay_dates()
    q = st.query_params.get("date")                 # 주소로 날짜·시각 지정: ?date=2026-10-21&t=103000
    date = st.sidebar.selectbox("날짜", days, index=days.index(q) if q in days else 0)
else:
    dates = live_dates()
    if not dates:
        st.info("outputs/live에 기록이 없습니다. 장중에 python -m ch07_realtime.live 를 먼저 실행하세요.")
        st.stop()
    date = st.sidebar.selectbox("날짜", dates)
following = mode == "실시간 기록" and date == dt.date.today().isoformat()   # 오늘 기록이면 최신 시각을 따라간다
if not following:
    df0, _ = load_day(mode, date)
    times = sorted(df0.t.unique())
    q = st.query_params.get("t")
    chosen = st.sidebar.select_slider("판단 시각", options=times, value=q if q in times else times[-1],
                                      format_func=hhmm)


def main_view():
    df, meta = load_day(mode, date)                # 실시간 기록은 다시 그릴 때마다 파일을 새로 읽는다
    t = max(df.t) if following else chosen
    if meta.get("errors"):                         # 장중 모니터가 실패하고 건너뛴 주기 (07-5)
        st.sidebar.warning(f"실패한 주기 {len(meta['errors'])}번: 마지막 {meta['errors'][-1]['error'][:80]}")
    now = df[df.t == t].sort_values("rank")
    st.title(f"AI 대장주 모니터 — {date} {hhmm(t)}")
    st.caption("연구·교육 목적의 규칙 기반 점수입니다. 투자 권유가 아니며, 11장에서 이 점수로 비용을 넘는 성과는 확인되지 않았습니다.")

    # 시장 요약
    c1, c2, c3 = st.columns(3)
    bench, n_cand = pd.Series(meta[t]["bench"]), meta[t]["candidates"]
    c1.metric("KOSPI 기준 ETF", f"{bench.get('KOSPI', float('nan')):+.2%}")
    c2.metric("KOSDAQ 기준 ETF", f"{bench.get('KOSDAQ', float('nan')):+.2%}")
    c3.metric("후보 종목", f"{n_cand}")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("대장주 TOP 10")
        table = now.assign(
            순위=now["rank"].astype(int), 종목=now.name, 점수=now.leader_score.round(3),
            등락률=(now.ret * 100).round(2), 시장대비=(now.rel_strength * 100).round(2),
            테마=now.theme_name.where(now.theme_score > 0, "-"),
            주의=[" · ".join(flags(r)) for r in now.itertuples()])
        st.dataframe(table[["순위", "종목", "점수", "등락률", "시장대비", "테마", "주의"]],
                     hide_index=True, use_container_width=True)
    with right:
        st.subheader("주도 테마")
        if mode == "리플레이":
            th = themes_at(date, t)
            st.dataframe(th.assign(강도=th.score.round(3), 강한종목=th.n_strong.astype(str) + "/" + th.n_obs.astype(str))
                         [["theme_name", "강도", "강한종목"]].rename(columns={"theme_name": "테마"}),
                         hide_index=True, use_container_width=True)
        else:
            st.write(f"1위 테마: **{meta[t]['top_theme']}** (확인된 1위: {meta[t]['confirmed_theme']})")
            for e in meta[t]["events"]:
                st.write(f"- {e}")

    # 선택 종목의 선정 근거 (12-2)
    st.subheader("선정 근거")
    pick = st.selectbox("종목", list(now.name))
    row = now[now.name == pick].iloc[0]
    contrib = {LABELS[c[2:]]: row[c] for c in now.columns if c.startswith("w_") and pd.notna(row[c])}
    fig = go.Figure(go.Bar(x=list(contrib.values()), y=list(contrib.keys()), orientation="h"))
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                      title=f"{pick}: Leader Score {row.leader_score:.3f} = 지표별 기여의 합")
    st.plotly_chart(fig, use_container_width=True)
    for f in flags(row):
        st.warning(f"{f}: {FLAG_NOTES[f]}")
    if mode == "리플레이":
        from ch10_vlm.cases import theme_members
        from ch10_vlm.chart import ai_chart, load
        members = theme_members(db(), date, t).get(row.code, [])
        s, bench_s, theme, _ = load(db(), date, t, row.code, row.market, members)
        st.image(ai_chart(s, bench_s, theme, t), caption="AI 전용 차트 (10장)", width=640)
    if st.button("LLM으로 재료 확인 (09-3, API 비용 발생)"):
        from ch09_llm.evidence import explain, news_before
        from ch12_monitor.data import news_today
        # 지난 날은 DB의 뉴스(09-1), 오늘은 한국투자증권 API로 바로 받는다
        items = news_today(row.code, t) if following else news_before(db(), dt.date.fromisoformat(date), row.code, t)
        r, _ = explain(db(), date, row.code, t, pick, row.ret, items=items)
        st.write(f"재료: **{'확인됨' if r['verified'] else '없음'}** — {r['reason'] or '-'}")
        for i, q in r["evidence"]:
            st.write(f"- [{items[i][1]:%H:%M}] {items[i][3]}")

    # 순위 변화 타임라인 (12-2)
    st.subheader("순위 변화")
    top5 = now.name.head(5).tolist()                 # 지금 TOP 5가 어떻게 올라왔는지
    past = sorted(x for x in df.t.unique() if x <= t)
    hist = (df[df.name.isin(top5)].pivot_table(index="t", columns="name", values="rank")
            .reindex(past).stack(future_stack=True).rename("rank").reset_index())   # TOP 10 밖이던 시각은 빈 값 → 선이 끊긴다
    hist = hist.assign(시각=pd.to_datetime(date + " " + hist.t, format="%Y-%m-%d %H%M%S"))   # 시간축으로 그린다
    fig = px.line(hist, x="시각", y="rank", color="name", markers=True)
    fig.update_yaxes(autorange="reversed", title="순위 (TOP 10 밖은 끊김)")
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), legend_title_text="")
    st.plotly_chart(fig, use_container_width=True)
    ch = leader_changes(df[df.t <= t])
    for r in ch.itertuples():
        st.write(f"- {hhmm(r.t)} " + (f"첫 대장주: **{r.new}**" if r.old is None else f"대장주 교체: {r.old} → **{r.new}**"))


if following:
    st.fragment(main_view, run_every="60s")()      # 장중에는 1분마다 다시 그린다
else:
    main_view()
