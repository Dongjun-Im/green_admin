"""자동 메일 발송 (rtgreen 전용).

소리샘의 /message/write.php 폼을 multipart/form-data POST 로 호출.

⚠ 로그인한 사용자의 아이디가 MAIL_SENDER_USER_ID (rtgreen) 가 아니면
   모든 send_* 메서드가 조용히 스킵한다. 다른 관리자가 실수로 대량 메일을
   보내는 것을 방지.
"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import requests

from config import HTTP_TIMEOUT, MAIL_SENDER_USER_ID, MAIL_WRITE_URL, USER_AGENT


def _build_files_payload(attachments: Optional[list]) -> list[tuple]:
    """첨부파일 경로 목록을 requests 의 files 인자 형식으로 변환.

    그누보드 g5 의 /message/write.php 는 빈 ms_file[] 더미라도 multipart 로
    POST 받기를 요구하므로 첨부가 없을 때도 빈 part 한 개를 포함한다.
    """
    payload: list[tuple] = []
    if attachments:
        for raw in attachments:
            p = Path(raw)
            if not p.exists() or not p.is_file():
                continue
            mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            payload.append(
                ("ms_file[]", (p.name, p.read_bytes(), mime))
            )
    if not payload:
        payload.append(
            ("ms_file[]", ("", b"", "application/octet-stream"))
        )
    return payload


@dataclass
class MailResult:
    skipped: bool = False
    success: bool = False
    message: str = ""
    recipients: list[str] = field(default_factory=list)
    response_snippet: str = ""


# 발송 방식
# v0.5: 일괄(bulk) 발송은 수신자 ID 가 서로에게 노출되어 개인정보 누출 위험.
#       개별(individual) 발송 한 가지로만 동작한다.
SEND_MODE_INDIVIDUAL = "individual"
# 구버전 호환 별칭 — 외부 호출이 SEND_MODE_BULK 를 넘기면 individual 로 안전 전환.
SEND_MODE_BULK = SEND_MODE_INDIVIDUAL


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
        mode: str = SEND_MODE_INDIVIDUAL,
        progress_cb: Optional[callable] = None,
        attachments: Optional[list] = None,
    ) -> list[MailResult]:
        """수신인들에게 동일 내용 메일을 회원별로 개별 발송.

        v0.5 부터는 mode 파라미터에 무엇이 들어와도 항상 individual 로 동작.
        수신자 ID 가 다른 수신자에게 노출되는 것을 막기 위함.

        attachments(v1.0): 첨부파일 경로 목록 (str/Path). 모든 수신자에게
        동일 첨부 발송. 사이트 ms_file[] 폼 필드를 사용.

        progress_cb(current, total) 으로 진행률을 알릴 수 있다.
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
        total = len(rec_list)
        for idx, uid in enumerate(rec_list, start=1):
            if progress_cb:
                try:
                    progress_cb(idx, total)
                except Exception:
                    pass
            results.append(self._send_chunk([uid], subject, body, attachments))
        return results

    def _send_chunk(
        self,
        chunk: list[str],
        subject: str,
        body: str,
        attachments: Optional[list] = None,
    ) -> MailResult:
        # /message/write.php 폼은 multipart/form-data. 첨부가 있으면 ms_file[]
        # 로 같이 전송, 없으면 빈 file part 한 개로 multipart 형식만 유지.
        data = {
            "reply": "0",
            "cl": "green",
            "receivers": ",".join(chunk),
            "ms_subject": subject,
            "ms_content": body,
        }
        files = _build_files_payload(attachments)

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


def template_welcome(member) -> tuple[str, str]:
    """신규 가입 승인 시 발송되는 환영 메일 (v1.0+)."""
    nick = member.nickname or member.name or member.user_id
    subject = "[초록등대] 가입을 진심으로 환영합니다 !"
    body = (
        f"{nick} 회원님, 안녕하세요.\n\n"
        f"초록등대 동호회에 가입해 주셔서 진심으로 감사드립니다.\n"
        f"운영진의 검토를 거쳐 회원님의 가입이 정식 승인되어\n"
        f"이제 동호회의 모든 게시판과 활동에 자유롭게 참여하실 수 있습니다.\n\n"
        f"동호회 활동 안내:\n"
        f"  · '우리들의 이야기' 게시판에서 일상과 이야기를 나눠 주세요.\n"
        f"  · '질문게시판' 에서는 궁금한 점을 자유롭게 질문하실 수 있어요.\n"
        f"  · 글 작성과 댓글 활동에 따라 등급이 차근차근 올라갑니다.\n"
        f"        - 활동점수 5 이상   → 일반회원\n"
        f"        - 활동점수 30 이상  → 우수회원\n"
        f"        - 활동점수 60 이상  → 최우수회원\n"
        f"        - 활동점수 300 이상 → 명예회원\n"
        f"      (활동점수 = 글 수 × 1.0 + 댓글 수 × 0.3)\n\n"
        f"앞으로 초록등대에서 즐겁고 따뜻한 시간을 함께 만들어 가요.\n"
        f"궁금한 점은 언제든 운영진에게 메일로 문의해 주세요.\n\n"
        f"감사합니다.\n"
        f"초록등대 운영진 드림"
    )
    return subject, body
