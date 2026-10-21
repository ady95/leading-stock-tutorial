"""02-2 접근토큰 발급과 캐시 확인

    python -m ch02_kis_api.get_token
"""

import datetime as dt
import json

from common.kis import KIS

kis = KIS()
token = kis.access_token()
cached = json.loads(kis.token_path.read_text())
expires_at = dt.datetime.fromtimestamp(cached["expires_at"])

print(f"환경: {kis.env} ({kis.domain})")
print(f"토큰: {token[:12]}...{token[-6:]} (길이 {len(token)})")
print(f"만료: {expires_at:%Y-%m-%d %H:%M:%S}")
print(f"캐시: {kis.token_path.relative_to(kis.token_path.parent.parent)}")
