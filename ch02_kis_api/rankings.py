"""02-3 순위 조회 — 거래대금 순위와 등락률 순위

    python -m ch02_kis_api.rankings
"""

from common.kis import KIS

# 대상 제외 구분 코드(10자리): 투자위험·경고·주의 / 관리종목 / 정리매매 / 불성실공시 /
# 우선주 / 거래정지 / ETF / ETN / 신용주문불가 / SPAC — 1이면 제외
EXCLUDE_NONE = "0000000000"
EXCLUDE_ETF_ETN_SPAC = "0000001101"

kis = KIS()


def value_rank(exclude=EXCLUDE_NONE):
    """거래대금 순위 상위 30종목 (거래량순위 API의 소속 구분 3 = 거래금액순)."""
    body = kis.get("/uapi/domestic-stock/v1/quotations/volume-rank", "FHPST01710000", {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "3",
        "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": exclude,
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": ""})
    return body["output"]


def change_rank(prc_cls_code):
    """등락률(상승률) 순위 상위 30종목.

    prc_cls_code — 0: 당일 저가 대비 상승률 순, 1: 전일 종가 대비 등락률 순
    """
    body = kis.get("/uapi/domestic-stock/v1/ranking/fluctuation", "FHPST01700000", {
        "fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20170",
        "fid_input_iscd": "0000", "fid_rank_sort_cls_code": "0", "fid_input_cnt_1": "0",
        "fid_prc_cls_code": prc_cls_code, "fid_input_price_1": "", "fid_input_price_2": "",
        "fid_vol_cnt": "", "fid_trgt_cls_code": "0", "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code": "0", "fid_rsfl_rate1": "", "fid_rsfl_rate2": ""})
    return body["output"]


for exclude, label in ((EXCLUDE_NONE, "제외 없음"), (EXCLUDE_ETF_ETN_SPAC, "ETF·ETN·SPAC 제외")):
    print(f"[거래대금 순위 TOP 10 — {label}]")
    for r in value_rank(exclude)[:10]:
        print(f"{r['data_rank']:>3}. {r['hts_kor_isnm']:<16} {float(r['prdy_ctrt']):>6.2f}%"
              f"  거래대금 {int(r['acml_tr_pbmn']) / 1e8:>8,.0f}억")
    print()

for code, label in (("0", "저가 대비"), ("1", "전일 종가 대비")):
    print(f"[상승률 순위 TOP 5 — fid_prc_cls_code={code}: {label}]")
    for r in change_rank(code)[:5]:
        print(f"{r['data_rank']:>3}. {r['hts_kor_isnm']:<12} 전일 대비 {float(r['prdy_ctrt']):>6.2f}%"
              f"  저가 대비 {float(r['lwpr_vrss_prpr_rate']):>6.2f}%")
    print()
