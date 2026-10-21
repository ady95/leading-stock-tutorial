"""02-3 현재가 조회 — KRX·NXT·통합 시세 비교

    python -m ch02_kis_api.price            # 삼성전자
    python -m ch02_kis_api.price 000660     # 다른 종목
"""

import sys

from common.kis import KIS

PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
MARKETS = {"J": "KRX", "NX": "NXT", "UN": "통합"}

code = sys.argv[1] if len(sys.argv) > 1 else "005930"
kis = KIS()

print(f"{'시장':<6}{'현재가':>10}{'등락률':>9}{'누적거래량':>14}{'누적거래대금(억)':>18}")
for market, label in MARKETS.items():
    out = kis.get(PATH, "FHKST01010100", {
        "FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code})["output"]
    print(f"{label:<6}{int(out['stck_prpr']):>10,}{float(out['prdy_ctrt']):>8.2f}%"
          f"{int(out['acml_vol']):>14,}{int(out['acml_tr_pbmn']) / 1e8:>18,.0f}")

print(f"\n업종: {out['bstp_kor_isnm']} / 시가총액: {int(out['hts_avls']):,}억 원")
