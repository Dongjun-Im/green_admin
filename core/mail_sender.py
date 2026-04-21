"""자동 메일 발송 (rtgreen 전용).

소리샘의 /message/write.php 폼을 multipart/form-data POST 로 호출.

⚠ 로그인한 사용자의 아이디가 MAIL_SENDER_USER_ID (rtgreen) 가 아니면
   모든 send_* 메서드가 조용히 스킵한다. 다른 관리자가 실수로 대량 메일을
   보내는 것을 방지.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

import requests

from config import HTTP_TIMEOUT, MAIL_SENDER_USER_ID, MAIL_WRITE_URL, USER_AGENT


@dataclass
class MailResult:
    skipped: bool = False
    success: bool = False
    message: str = ""
    recipients: list[str] = field(default_factory=list)
    response_snippet: str = ""


# 발송 방식
SEND_MODE_BULK = "bulk"         # 한 번의 POST 에 수신인 여러 명 (쉼표 구분)
SEND_MODE_INDIVIDUAL = "individual"  # 회원마다 POST 1번씩


# "명백한 실패" 마커. 이 중 하나라도 응답 본문에 있으면 실패로 판정.
# 그 외의 모든 HTTP 200 응답은 성공으로 본다 (false negative 방지).
EXPLICIT_FAILURE_MARKERS = (
    "권한이 없습니다",
    "로그인이 필요",
    "로그인 후 이용",
    "회원만 이용",
    "자신에게만 발송",   # 일부 사이트에서 self-only 제한 경고
)


class MailSender:
    CHUNK_SIZE = 50   # 일괄 발송 시 한 POST 의 최대 수신인 수
    SENDER_USER_ID = MAIL_SENDER_USER_ID

    def __init__(self, session: requests.Session, current_user_id: str) -> None:
        self.session = session
        self.current_user_id = (current_user_id or "").lower()
        self.enabled = self.current_user_id == self.SENDER_USER_ID.lower()

    def send(
        self,
        recipients: Iterable[str],
        subject: str,
        body: str,
        mode: str = SEND_MODE_BULK,
    ) -> list[MailResult]:
        """수신인들에게 동일 내용 메일 발송.

        mode:
          - SEND_MODE_BULK: 한 번의 POST 에 여러 수신인을 쉼표로 묶어 전송.
                            빠르지만 수신자가 서로의 아이디를 볼 수 있음.
          - SEND_MODE_INDIVIDUAL: 회원마다 별도 POST. 프라이버시 보호.
                                  회원 수만큼 시간이 걸림.

        enabled == False 면 MailResult(skipped=True) 를 한 번 반환.
        """
        rec_list = [r for r in recipients if r]
        if not rec_list:
            return [MailResult(skipped=True, message="수신인 없음")]

        if not self.enabled:
            return [MailResult(
                skipped=True,
                message=f"'{self.SENDER_USER_ID}' 로 로그인하지 않아 메일 발송이 비활성화됨",
                recipients=rec_list,
            )]

        results: list[MailResult] = []
        if mode == SEND_MODE_INDIVIDUAL:
            for uid in rec_list:
                results.append(self._send_chunk([uid], subject, body))
        else:
            for start in range(0, len(rec_list), self.CHUNK_SIZE):
                chunk = rec_list[start:start + self.CHUNK_SIZE]
                results.append(self._send_chunk(chunk, subject, body))
        return results

    def _send_chunk(
        self, chunk: list[str], subject: str, body: str
    ) -> MailResult:
        # /message/write.php 폼은 multipart/form-data. 빈 file part 를 포함해
        # 서버가 multipart 로 정상 처리하도록 보장.
        data = {
            "reply": "0",
            "cl": "green",
            "receivers": ",".join(chunk),
            "ms_subject": subject,
            "ms_content": body,
        }
        files = {"ms_file[]": ("", b"", "application/octet-stream")}

        headers = {
            "User-Agent": USER_AGENT,
            "Referer": MAIL_WRITE_URL,
        }

        try:
            resp = self.session.post(
                MAIL_WRITE_URL,
                data=data,
                files=files,
                headers=headers,
                timeout=HTTP_TIMEOUT * 2,
                allow_redirects=True,
            )
        except requests.exceptions.RequestException as e:
            return MailResult(
                success=False,
                message=f"네트워크 오류: {e}",
                recipients=chunk,
            )

        body_text = resp.text or ""
        snippet = body_text[:500]

        # HTTP 상태 체크
        if not resp.ok:
            return MailResult(
                success=False,
                message=f"HTTP {resp.status_code}",
                recipients=chunk,
                response_snippet=snippet,
            )

        # 빈 응답은 의심스럽지만 실제 사이트에서 일부 성공 시 빈 리다이렉트가 있음.
        # 명백한 실패 마커가 있을 때만 실패로 판정. 그 외 HTTP 200 은 성공으로 간주.
        for marker in EXPLICIT_FAILURE_MARKERS:
            if marker in body_text:
                return MailResult(
                    success=False,
                    message=f"사이트 거부: {marker!r}",
                    recipients=chunk,
                    response_snippet=snippet,
                )

        return MailResult(
            success=True,
            message="발송 완료",
            recipients=chunk,
            response_snippet=snippet,
        )


# ---------------- 기본 템플릿 ----------------

def template_demote(member, from_label: str, to_label: str) -> tuple[str, str]:
    nick = member.nickname or member.name or member.user_id
    subject = f"[초록등대] 회원 등급이 조정되었습니다 ({from_label} → {to_label})"
    body = (
        f"{nick} 회원님 안녕하세요.\n\n"
        f"초록등대 동호회 관리 프로그램입니다.\n\n"
        f"최근 6개월 이상 접속 기록이 없어, 동호회 규정에 따라 "
        f"회원님의 등급이 {from_label}에서 {to_label}(으)로 조정되었습니다.\n\n"
        f"다시 활동해 주시면 예전 등급으로 복구가 가능합니다. "
        f"언제든 소리샘 초록등대 동호회로 찾아주세요.\n\n"
        f"감사합니다.\n"
        f"초록등대 운영진 드림"
    )
    return subject, body


def template_delete(member) -> tuple[str, str]:
    nick = member.nickname or member.name or member.user_id
    subject = "[초록등대] 장기 미접속으로 탈퇴 처리되었습니다"
    body = (
        f"{nick} 회원님 안녕하세요.\n\n"
        f"초록등대 동호회 관리 프로그램입니다.\n\n"
        f"6개월 이상 접속 기록이 없어 동호회 규정에 따라 "
        f"회원 탈퇴 처리가 이루어졌습니다.\n\n"
        f"다시 이용을 원하시면 언제든 재가입해 주세요. "
        f"초록등대는 항상 열려 있습니다.\n\n"
        f"감사합니다.\n"
        f"초록등대 운영진 드림"
    )
    return subject, body


def template_promote(member, from_label: str, to_label: str, post_count: int) -> tuple[str, str]:
    nick = member.nickname or member.name or member.user_id
    subject = f"[초록등대] 축하합니다 ! {to_label} 로 승급되셨습니다"
    body = (
        f"{nick} 회원님 안녕하세요.\n\n"
        f"초록등대 동호회 관리 프로그램입니다.\n\n"
        f"'우리들의 이야기' 게시판에 {post_count}건의 글을 작성해 주셨습니다.\n"
        f"회원님의 활동에 감사드리며, 등급이 {from_label}에서 {to_label}(으)로 "
        f"승급되었음을 알려드립니다.\n\n"
        f"앞으로도 활발한 활동 부탁드립니다.\n\n"
        f"감사합니다.\n"
        f"초록등대 운영진 드림"
    )
    return subject, body
