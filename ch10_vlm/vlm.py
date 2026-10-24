"""10-3 VLM 호출 — 차트 이미지를 Responses API의 input_image로 보내고 구조화된 답을 받는다.

    from ch10_vlm.vlm import ask_image
    out, usage = ask_image("지시", "질문", png_bytes, MySchema)
"""

import base64
import os
import time

from ch09_llm.llm import client


def ask_image(system, user, png, schema, model=None, retries=4):
    model = model or os.environ["OPENAI_MODEL"]
    url = "data:image/png;base64," + base64.b64encode(png).decode()     # 파일을 올리지 않고 바로 보낸다
    content = [{"type": "input_text", "text": user}, {"type": "input_image", "image_url": url}]
    for attempt in range(retries):
        try:
            res = client().responses.parse(
                model=model, text_format=schema,
                input=[{"role": "system", "content": system}, {"role": "user", "content": content}])
            if res.output_parsed is not None:
                return res.output_parsed, res.usage
        except Exception as e:                  # 네트워크 오류, 한도 초과, 형식 오류
            if attempt == retries - 1:
                raise
            print(f"  VLM 재시도 {attempt + 1}: {type(e).__name__}")
        time.sleep(2 ** attempt)
    raise RuntimeError("VLM 응답을 받지 못했습니다")
