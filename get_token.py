# -*- coding: utf-8 -*-
"""
[최초 1회만] 카카오 refresh_token 발급.
사전준비: developers.kakao.com 에서 앱 생성 →
  · [카카오 로그인] 활성화 ON
  · [카카오 로그인 > Redirect URI]에 https://localhost 등록
  · [동의항목]에서 '카카오톡 메시지 전송(talk_message)' 사용 설정
  · [앱 키]의 REST API 키 준비
실행: python get_token.py
"""
import requests

REDIRECT = "https://localhost"

rest_key = input("REST API 키: ").strip()
auth_url = (
    "https://kauth.kakao.com/oauth/authorize?response_type=code"
    f"&client_id={rest_key}&redirect_uri={REDIRECT}&scope=talk_message"
)
print("\n1) 아래 URL을 브라우저에서 열고 '동의하고 계속하기'를 누르세요:")
print(auth_url)
print("\n2) https://localhost/?code=XXXX 로 이동하면 주소창의 code= 뒤 값을 복사하세요.\n")
code = input("code: ").strip()

r = requests.post("https://kauth.kakao.com/oauth/token", data={
    "grant_type": "authorization_code",
    "client_id": rest_key,
    "redirect_uri": REDIRECT,
    "code": code,
}, timeout=10)
d = r.json()
print("\n=== 결과 ===")
print(d)
if "refresh_token" in d:
    print("\n▶ GitHub Secrets에 저장:")
    print("   KAKAO_REST_KEY      =", rest_key)
    print("   KAKAO_REFRESH_TOKEN =", d["refresh_token"])
