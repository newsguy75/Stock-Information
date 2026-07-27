# -*- coding: utf-8 -*-
"""카카오 '나에게 보내기'(기본 텍스트 템플릿) 최소 구현."""
import json
import requests

KAUTH = "https://kauth.kakao.com/oauth/token"
KAPI  = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


def refresh_access_token(rest_key, refresh_token):
    """refresh_token으로 access_token 재발급.
    반환: (access_token, new_refresh_token 또는 None)
    (refresh_token은 만료 1개월 미만일 때만 새로 발급됨)"""
    r = requests.post(KAUTH, data={
        "grant_type": "refresh_token",
        "client_id": rest_key,
        "refresh_token": refresh_token,
    }, timeout=10)
    r.raise_for_status()
    d = r.json()
    return d["access_token"], d.get("refresh_token")


def send_text(access_token, text, link_url="https://finance.naver.com", button_title=None):
    """기본 텍스트 템플릿 전송 (text 최대 200자)."""
    template = {
        "object_type": "text",
        "text": text[:200],
        "link": {"web_url": link_url, "mobile_web_url": link_url},
    }
    if button_title:
        template["button_title"] = button_title
    r = requests.post(
        KAPI,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()
