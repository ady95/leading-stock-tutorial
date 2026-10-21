"""한국투자증권 Open API 공통 모듈 — 접근토큰 발급·캐시, 호출 속도 제한, 재시도.

책 전체에서 이 모듈을 가져다 씁니다.

    from common.kis import KIS
    kis = KIS()   # .env의 KIS_ENV(real 또는 paper)를 따른다
    data = kis.get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                   {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"})
"""

import json
import os
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"

DOMAINS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "paper": "https://openapivts.koreainvestment.com:29443",
}
# 초당 호출 수. 실전은 한도(초당 20건)보다 여유 있게, 모의투자는 1초 1건에서만 오류가 없었다
RATE_PER_SEC = {"real": 15, "paper": 1}


class KISError(Exception):
    def __init__(self, msg_cd, msg):
        super().__init__(f"{msg_cd} {msg}")
        self.msg_cd = msg_cd


class RateLimiter:
    """여러 스레드에서 불러도 초당 호출 수를 넘지 않게 호출 간격을 벌린다."""

    def __init__(self, per_sec):
        self.interval = 1.0 / per_sec
        self.lock = threading.Lock()
        self.next_at = time.monotonic()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            at = max(self.next_at, now)
            self.next_at = at + self.interval
        time.sleep(max(0.0, at - now))


class KIS:
    def __init__(self, env=None, app_key=None, app_secret=None):
        load_dotenv(ROOT / ".env")
        self.env = env or os.getenv("KIS_ENV", "paper")
        self.domain = DOMAINS[self.env]
        self.app_key = app_key or os.environ["KIS_APP_KEY"]
        self.app_secret = app_secret or os.environ["KIS_APP_SECRET"]
        self.limiter = RateLimiter(RATE_PER_SEC[self.env])
        self.token_path = CACHE_DIR / f"token_{self.env}.json"
        self.token_lock = threading.Lock()
        self.token = None
        self.token_exp = 0.0
        self.session = requests.Session()

    # ---- 접근토큰 ----
    def access_token(self):
        """유효한 토큰을 돌려준다. 캐시 파일 → 새로 발급 순으로 찾는다."""
        with self.token_lock:
            if self.token and self.token_exp > time.time() + 600:
                return self.token
            if self.token_path.exists():
                cached = json.loads(self.token_path.read_text())
                if cached["expires_at"] > time.time() + 600:
                    self.token, self.token_exp = cached["access_token"], cached["expires_at"]
                    return self.token
            res = requests.post(self.domain + "/oauth2/tokenP", timeout=15, json={
                "grant_type": "client_credentials",
                "appkey": self.app_key, "appsecret": self.app_secret})
            body = res.json()
            if "access_token" not in body:
                raise KISError(body.get("error_code", res.status_code), body.get("error_description", ""))
            self.token = body["access_token"]
            self.token_exp = time.time() + int(body["expires_in"])
            CACHE_DIR.mkdir(exist_ok=True)
            self.token_path.write_text(json.dumps(
                {"access_token": self.token, "expires_at": self.token_exp}))
            return self.token

    def _drop_token(self):
        with self.token_lock:
            self.token, self.token_exp = None, 0.0
            self.token_path.unlink(missing_ok=True)

    # ---- 조회 ----
    def get(self, path, tr_id, params, retries=5):
        """GET 조회. 초당 한도 초과·네트워크 오류는 기다렸다가 다시 시도한다."""
        for attempt in range(retries):
            self.limiter.wait()
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self.access_token()}",
                "appkey": self.app_key, "appsecret": self.app_secret,
                "tr_id": tr_id, "custtype": "P",
            }
            try:
                res = self.session.get(self.domain + path, headers=headers, params=params, timeout=15)
                body = res.json()
            except (requests.RequestException, ValueError):
                time.sleep(2 ** attempt)
                continue
            if body.get("rt_cd") == "0":
                return body
            msg_cd = body.get("msg_cd", "")
            if msg_cd == "EGW00201":                  # 초당 거래건수 초과
                time.sleep(1.0)
            elif msg_cd in ("EGW00121", "EGW00123"):  # 토큰 무효·만료
                self._drop_token()
            else:
                raise KISError(msg_cd, body.get("msg1", "").strip())
        raise KISError("RETRY", f"{tr_id} 호출이 {retries}번 모두 실패했습니다")

    def approval_key(self):
        """실시간(WebSocket) 접속용 승인키를 발급한다."""
        res = requests.post(self.domain + "/oauth2/Approval", timeout=15, json={
            "grant_type": "client_credentials",
            "appkey": self.app_key, "secretkey": self.app_secret})
        return res.json()["approval_key"]
