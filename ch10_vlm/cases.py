"""10장 공통 — 판단 시각마다 후보와 주도 테마 구성 종목을 고른다."""

from ch06_leader_score.score import leader_scores, load_themes

MAX_MEMBERS = 15     # 테마 평균선에 쓸 구성 종목 수 상한 (그 시각 거래대금 순)


def theme_members(con, date, t, scores=None):
    """종목코드 → 6장 점수가 고른 주도 테마의 다른 구성 종목 목록 (그 시각 후보인 종목만)."""
    if scores is None:
        scores, _ = leader_scores(con, date, t)
    themes = load_themes(con)
    cand = scores.sort_values("acc_value", ascending=False)
    in_cand = set(cand.code)
    by_theme = themes[themes.code.isin(in_cand)].groupby("theme_name").code.apply(list).to_dict()
    order = {c: i for i, c in enumerate(cand.code)}
    out = {}
    for r in scores.dropna(subset=["theme_name"]).itertuples():
        others = [c for c in by_theme.get(r.theme_name, []) if c != r.code]
        out[r.code] = sorted(others, key=order.get)[:MAX_MEMBERS]
    return out


def pick_stock(con, date, t, code=None):
    """(종목코드, 시장, 테마 구성 종목). 종목코드를 주지 않으면 그 시각 Leader Score 1위를 고른다."""
    scores, _ = leader_scores(con, date, t)
    if code is None:
        code = scores.code.iloc[0]
    market = con.execute("SELECT market FROM stock_master WHERE code = ?", [code]).fetchone()[0]
    has_bars = con.execute("SELECT count(*) FROM minute_bars WHERE date = ? AND code = ? AND time < ?",
                           [date, code, t]).fetchone()[0]
    if not has_bars:
        raise SystemExit(f"{date} {code}의 {t[:2]}:{t[2:4]} 이전 1분봉이 DB에 없습니다. "
                         "샘플 데이터는 날짜별 거래대금 상위 100종목만 담고 있으니, --code를 빼고 그 시각 1위 종목으로 해 보세요")
    return code, market, theme_members(con, date, t, scores).get(code, [])

