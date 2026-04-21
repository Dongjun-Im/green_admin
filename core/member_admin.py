"""소리샘 admin.member.php 일괄 폼 POST 어댑터.

⚠ dry_run=True 가 기본값. 명시적으로 False 설정 시에만 실제 POST.

사이트 폼 구조:
    <form name="fmemberlist" action="" method="post">
      <input type="hidden" name="cl" value="green">
      <input type="hidden" name="sst" value="cl_datetime">
      <input type="hidden" name="sod" value="desc">
      <input type="hidden" name="sfl" value="">
      <input type="hidden" name="stx" value="">
      <input type="hidden" name="page" value="1">
      <input type="hidden" name="token" value="">
      ... 회원별 <select name='cl_level[mb_id]'> ...
      <input type="submit" name="act_button" value="등급변경" />
    </form>

탈퇴는 cl_level[mb_id]=1 (option value='1' = 탈퇴) 로 설정.
한 번의 POST 에 여러 회원의 cl_level 을 묶어 일괄 처리 가능.
"""
from __future__ import annotations

from typing import Iterable

import requests

from config import (
    ADMIN_MEMBER_URL,
    HTTP_TIMEOUT,
    USER_AGENT,
    WITHDRAW_LEVEL,
)
from core.models import AdminActionResult, Member


class MemberAdminAdapter:
    POST_URL = ADMIN_MEMBER_URL  # form action 비어있음 → 같은 페이지로 POST
    REFERER = ADMIN_MEMBER_URL

    def __init__(
        self,
        session: requests.Session,
        cl: str = "green",
        dry_run: bool = True,
    ) -> None:
        self.session = session
        self.cl = cl
        self.dry_run = dry_run

    # ---------- 단건 ----------

    def change_level(self, member: Member, new_level: int) -> AdminActionResult:
        return self._submit({member.user_id: new_level}, action_label=f"레벨 변경 → {new_level}")

    def delete_member(self, member: Member) -> AdminActionResult:
        # 사이트에는 별도 탈퇴 엔드포인트가 없음. 등급을 1(탈퇴) 로 변경하면 됨.
        return self._submit({member.user_id: WITHDRAW_LEVEL}, action_label="탈퇴 처리")

    # ---------- 일괄 ----------

    def bulk_apply(
        self, level_map: dict[str, int], action_label: str = "일괄 등급 변경"
    ) -> AdminActionResult:
        """여러 회원을 한 번에 처리. {mb_id: new_level} 형태."""
        if not level_map:
            return AdminActionResult(success=True, message="처리할 회원 없음")
        return self._submit(level_map, action_label=action_label)

    # ---------- 내부 ----------

    def _build_payload(self, level_map: dict[str, int]) -> list[tuple[str, str]]:
        """폼 hidden + 회원별 cl_level 필드 묶음.
        list[tuple] 로 만들어 같은 키 반복 가능."""
        payload: list[tuple[str, str]] = [
            ("cl", self.cl),
            ("sst", "cl_datetime"),
            ("sod", "desc"),
            ("sfl", ""),
            ("stx", ""),
            ("page", "1"),
            ("token", ""),
            ("act_button", "등급변경"),
        ]
        for mb_id, new_level in level_map.items():
            payload.append((f"cl_level[{mb_id}]", str(new_level)))
        return payload

    def _submit(self, level_map: dict[str, int], action_label: str) -> AdminActionResult:
        payload = self._build_payload(level_map)
        request_payload_dict = {k: v for k, v in payload}  # 로깅용 요약

        if self.dry_run:
            preview = ", ".join(f"{k}→{v}" for k, v in level_map.items())
            return AdminActionResult(
                success=True,
                message=f"[DRY-RUN] {action_label}: {preview}",
                request_url=self.POST_URL,
                request_payload=request_payload_dict,
            )

        headers = {
            "User-Agent": USER_AGENT,
            "Referer": self.REFERER,
        }
        try:
            resp = self.session.post(
                self.POST_URL,
                data=payload,
                headers=headers,
                timeout=HTTP_TIMEOUT,
                allow_redirects=True,
            )
        except requests.exceptions.RequestException as e:
            return AdminActionResult(
                success=False,
                message=f"네트워크 오류: {e}",
                request_url=self.POST_URL,
                request_payload=request_payload_dict,
            )

        body = resp.text or ""
        snippet = body[:500]
        if not resp.ok:
            return AdminActionResult(
                success=False,
                message=f"HTTP {resp.status_code}",
                request_url=self.POST_URL,
                request_payload=request_payload_dict,
                response_snippet=snippet,
            )

        # 성공 판정: 사이트가 처리 후 회원 목록 페이지를 다시 보여주므로
        # 응답에 "동호회 회원 관리" 또는 회원 목록 form 마커가 있으면 성공으로 간주.
        is_success = (
            "동호회 회원 관리" in body
            or "fmemberlist" in body
            or "수정되었습니다" in body
            or "처리되었습니다" in body
        )
        # 명시적 실패 키워드
        if "권한이 없습니다" in body or "오류" in body[:300]:
            is_success = False

        return AdminActionResult(
            success=is_success,
            message=action_label + (" 성공" if is_success else " 실패"),
            request_url=self.POST_URL,
            request_payload=request_payload_dict,
            response_snippet=snippet,
        )
