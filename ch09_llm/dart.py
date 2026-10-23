"""09-1 OPEN DART 공시 목록과 한국투자증권 공시 제목 비교 — 공시가 "언제" 나왔는지 확인한다.

DART 공시 목록에는 접수 날짜만 있고 시각이 없다. 한국투자증권 뉴스 제목 API의
거래소 공시(F)·코스닥 공시(G)에는 시각이 있으므로, 둘을 맞춰 보고 시간대를 센다.

    python -m ch09_llm.dart --date 2026-10-21
"""

import argparse
import collections
import os
import re

import requests
from dotenv import load_dotenv

from ch03_pipeline.db import ROOT
from common.kis import KIS

DART_URL = "https://opendart.fss.or.kr/api/list.json"
NEWS_PATH = "/uapi/domestic-stock/v1/quotations/news-title"


def dart_list(day):
    """하루치 공시 목록. corp_code 없이 조회하면 기간은 3개월까지, 한 번에 100건까지 받는다."""
    load_dotenv(ROOT / ".env")
    rows, page = [], 1
    while True:
        body = requests.get(DART_URL, timeout=20, params={
            "crtfc_key": os.environ["DART_API_KEY"], "bgn_de": day, "end_de": day,
            "page_no": page, "page_count": 100}).json()
        if body["status"] != "000":          # 013: 조회된 데이터 없음 (휴일 등)
            break
        rows += body["list"]
        if page >= body["total_page"]:
            break
        page += 1
    return rows


def kis_disclosures(kis, day):
    """그날 나온 거래소·코스닥 공시 제목 (제공처 코드 F, G). 23:59:59부터 거꾸로 40건씩."""
    out = {}
    for provider in ("F", "G"):
        date, hour = day, "235959"
        for _ in range(50):
            page = kis.get(NEWS_PATH, "FHKST01011800", {
                "FID_NEWS_OFER_ENTP_CODE": provider, "FID_COND_MRKT_CLS_CODE": "", "FID_INPUT_ISCD": "",
                "FID_TITL_CNTT": "", "FID_INPUT_DATE_1": date, "FID_INPUT_HOUR_1": hour,
                "FID_RANK_SORT_CLS_CODE": "", "FID_INPUT_SRNO": ""}).get("output") or []
            new = [r for r in page if r["cntt_usiq_srno"] not in out]
            out.update((r["cntt_usiq_srno"], r) for r in new)
            if not new or len(page) < 40 or page[-1]["data_dt"] < day:
                break
            date, hour = page[-1]["data_dt"], page[-1]["data_tm"]
    return [r for r in out.values() if r["data_dt"] == day]


def norm(s):
    return re.sub(r"[\s()\[\]ㆍ·,]", "", s).replace("기재정정", "정정")


def session(hhmmss):
    return "장 시작 전" if hhmmss < "090000" else "장중" if hhmmss <= "153000" else "장 마감 후"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    day = args.date.replace("-", "")

    dart = [r for r in dart_list(day) if r["corp_cls"] in ("Y", "K")]     # 유가증권(Y)·코스닥(K)
    kis = kis_disclosures(KIS(), day)
    print(f"{args.date} DART 공시(유가증권·코스닥) {len(dart)}건, 한국투자증권 공시 제목 {len(kis)}건")
    when = collections.Counter(session(r["data_tm"]) for r in kis)
    for k in ("장 시작 전", "장중", "장 마감 후"):
        print(f"  {k}: {when[k]}건 ({when[k] / max(len(kis), 1):.0%})")

    # 같은 종목의 공시 제목이 그날 한국투자증권에도 있는가 (보고서 이름 앞부분으로 맞춘다)
    by_code = collections.defaultdict(list)
    for r in kis:
        by_code[r["iscd1"].strip()].append(r)
    found = sum(any(norm(r["report_nm"])[:8] in norm(x["hts_pbnt_titl_cntt"]) for x in by_code[r["stock_code"]])
                for r in dart)
    print(f"DART 공시 중 같은 종목·같은 이름의 공시 제목이 있는 것 {found}건 ({found / max(len(dart), 1):.0%})")


if __name__ == "__main__":
    main()
