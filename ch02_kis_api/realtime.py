"""02-4 실시간 체결가 수신 (WebSocket) — 장중에만 데이터가 온다.

    python -m ch02_kis_api.realtime                       # 기본 5종목, 60초
    python -m ch02_kis_api.realtime 005930 000660 --seconds 300
"""

import argparse
import asyncio
import datetime as dt
import json
from collections import Counter

import websockets

from common.kis import KIS

WS_URLS = {"real": "ws://ops.koreainvestment.com:21000", "paper": "ws://ops.koreainvestment.com:31000"}
TR_ID = "H0STCNT0"   # 국내주식 실시간체결가 (KRX)

# 체결가 메시지의 필드 순서 (공식 예제 ccnl_krx.py 기준)
COLUMNS = [
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN",
    "PRDY_VRSS", "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC",
    "STCK_HGPR", "STCK_LWPR", "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL",
    "ACML_TR_PBMN", "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU",
    "CTTR", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN", "CCLD_DVSN", "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR", "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE",
    "NEW_MKOP_CLS_CODE", "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE", "MRKT_TRTM_CLS_CODE", "VI_STND_PRC", "MARKET_CLS_CODE",
]


def subscribe_message(approval_key, code, tr_type="1"):
    """tr_type 1 = 구독 등록, 2 = 구독 해제."""
    return json.dumps({
        "header": {"approval_key": approval_key, "custtype": "P",
                   "tr_type": tr_type, "content-type": "utf-8"},
        "body": {"input": {"tr_id": TR_ID, "tr_key": code}},
    })


def parse_trades(raw):
    """'0|H0STCNT0|003|필드^필드^...' → 체결 dict 목록. 한 메시지에 여러 건이 이어 붙어 올 수 있다."""
    _, tr_id, count, payload = raw.split("|", 3)
    values = payload.split("^")
    n = len(COLUMNS)
    return [dict(zip(COLUMNS, values[i * n:(i + 1) * n])) for i in range(int(count))]


async def open_ws(url, attempts=5, wait=5):
    """접속이 거부되면 기다렸다가 다시 시도한다 (모의투자 서버에서 장중 접속 거부가 실측됨)."""
    for i in range(1, attempts + 1):
        try:
            return await websockets.connect(url, open_timeout=10)
        except (websockets.exceptions.WebSocketException, OSError, asyncio.TimeoutError) as ex:
            print(f"접속 실패 {i}/{attempts}: {type(ex).__name__} — {wait}초 후 다시 시도")
            await asyncio.sleep(wait)
    raise ConnectionError(f"{url} 접속에 {attempts}번 실패했습니다")


async def run(codes, seconds):
    kis = KIS()
    approval_key = kis.approval_key()
    stats = Counter()
    lags = []
    async with await open_ws(WS_URLS[kis.env] + "/tryitout") as ws:
        for code in codes:   # 한 세션 최대 41종목. 간격 없이 몰아 보내면 일부 요청이 응답 없이 사라진다
            await ws.send(subscribe_message(approval_key, code))
            await asyncio.sleep(0.2)
        end = asyncio.get_running_loop().time() + seconds
        while (left := end - asyncio.get_running_loop().time()) > 0:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=left)
            except asyncio.TimeoutError:
                break
            if raw[0] in "01":                          # 시세 데이터
                trades = parse_trades(raw)
                stats["frames"] += 1
                stats["multi_frames"] += len(trades) > 1
                now = dt.datetime.now()
                for t in trades:
                    stats[t["MKSC_SHRN_ISCD"]] += 1
                    traded = dt.datetime.combine(now.date(), dt.datetime.strptime(t["STCK_CNTG_HOUR"], "%H%M%S").time())
                    lags.append((now - traded).total_seconds())
                    if stats[t["MKSC_SHRN_ISCD"]] <= 2:   # 종목마다 처음 2건만 출력
                        side = {"1": "매수", "5": "매도"}.get(t["CCLD_DVSN"], t["CCLD_DVSN"])
                        print(f"{t['STCK_CNTG_HOUR']} {t['MKSC_SHRN_ISCD']} {int(t['STCK_PRPR']):>9,}원 "
                              f"{float(t['PRDY_CTRT']):>6.2f}% 체결 {int(t['CNTG_VOL']):>7,}주({side}) "
                              f"체결강도 {float(t['CTTR']):>6.2f} 누적대금 {int(t['ACML_TR_PBMN']) / 1e8:>9,.0f}억")
            else:                                       # 시스템 메시지 (JSON)
                msg = json.loads(raw)
                if msg["header"]["tr_id"] == "PINGPONG":
                    await ws.pong(raw)
                    stats["pingpong"] += 1
                else:
                    body = msg.get("body", {})
                    print(f"[응답] {msg['header'].get('tr_key')} {body.get('rt_cd')} {body.get('msg1')}")

    trades_total = sum(stats[c] for c in codes)
    print(f"\n{seconds}초 동안 메시지 {stats['frames']}개, 체결 {trades_total}건 "
          f"(여러 건이 묶인 메시지 {stats['multi_frames']}개), PINGPONG {stats['pingpong']}회")
    for c in codes:
        print(f"  {c}: {stats[c]}건")
    if lags:
        lags.sort()
        print(f"체결 시각 대비 수신 지연(초): 중앙값 {lags[len(lags) // 2]:.1f}, 최대 {lags[-1]:.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*", default=["005930", "000660", "009150", "402340", "035420"])
    ap.add_argument("--seconds", type=int, default=60)
    args = ap.parse_args()
    asyncio.run(run(args.codes, args.seconds))


if __name__ == "__main__":
    main()
