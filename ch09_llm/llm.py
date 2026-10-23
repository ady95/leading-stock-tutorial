"""9장 공통 — OpenAI Responses API 구조화 출력 호출.

    from ch09_llm.llm import ask
    out = ask("시스템 지시", "사용자 입력", MySchema)   # MySchema는 pydantic 모델

.env의 OPENAI_API_KEY, OPENAI_MODEL을 쓰고, OPENAI_BASE_URL이 있으면 그 주소로 보낸다.
"""

import os
import time

from dotenv import load_dotenv
from openai import OpenAI

from ch03_pipeline.db import ROOT

load_dotenv(ROOT / ".env")
_client = None


def client():
    global _client
    if _client is None:
        _client = OpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)
    return _client


def ask(system, user, schema, model=None, retries=4):
    """schema(pydantic 모델) 모양으로 답을 받는다. 실패하면 잠시 기다렸다가 다시 시도한다."""
    model = model or os.environ["OPENAI_MODEL"]
    for attempt in range(retries):
        try:
            res = client().responses.parse(
                model=model, text_format=schema,
                input=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            if res.output_parsed is not None:
                return res.output_parsed, res.usage
        except Exception as e:                  # 네트워크 오류, 한도 초과, 형식 오류
            if attempt == retries - 1:
                raise
            print(f"  LLM 재시도 {attempt + 1}: {type(e).__name__}")
        time.sleep(2 ** attempt)
    raise RuntimeError("LLM 응답을 받지 못했습니다")
