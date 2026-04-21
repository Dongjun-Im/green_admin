"""단축키/사용법 안내 대화상자."""
import wx

from config import APP_NAME, APP_VERSION
from ui.item_text_ctrl import ItemTextCtrl


SHORTCUTS_TEXT = """초록등대 회원관리 — 단축키 안내

[작업]
  Ctrl+F          회원 검색 (아이디·이름·닉네임 부분 일치)
  Ctrl+B          우수회원 백업 즉시 실행 (TXT + Excel)
                  · 백업 후 게시물 기반 자동 승급도 함께 진행
  Ctrl+U          게시물 기반 자동 승급 단독 실행
                  · '우리들의 이야기' 게시판 글 수 기준
  Ctrl+R          장기미접속 조정 — 미리보기 (드라이런)
  Ctrl+Shift+R    장기미접속 조정 — 실제 적용 (확인 후)
  Ctrl+M          수동 메일 발송 (rtgreen 전용)
                  · 자동 발송이 실패했을 때 다시 시도
                  · rtgreen 외 계정은 안내 후 종료
  Ctrl+D          지금 작업 가능 여부 확인 (음성)

[정보]
  Ctrl+I          마지막 작업일 / 다음 예정일 음성 안내
  Ctrl+K          이 단축키 안내
  F1              프로그램 정보

[파일]
  Ctrl+O          백업 폴더 열기
  Ctrl+L          로그아웃 (저장된 자격 증명 삭제 후 재로그인)
  Alt+F4          프로그램 종료

[목록 탐색]
  ↑ / ↓           이전/다음 항목
  Home / End      처음/끝 항목
  Esc             현재 음성 중단

[규칙 - 사이트 실제 등급 기준]
  등급 번호: 5 일반회원 / 6 우수회원 / 7 최우수회원 / 8 명예회원 / 9 동호회관리자

  · 우수회원 백업: 6레벨(우수) + 7레벨(최우수), 3개월 주기
        매년 1/4/7/10월 1일 도래 (캘린더 기준)
  · 게시물 기반 자동 승급 ('우리들의 이야기' 게시판 기준):
        - 대기/신청(3,4) + 글 3건 이상  → 일반회원(5)
        - 일반회원(5) + 글 30건 이상   → 우수회원(6)
        - 일반회원(5) + 글 50건 이상   → 최우수회원(7)
        - 일반회원(5) + 글 100건 이상  → 명예회원(8)
        * 기존 등급보다 낮아지는 강등은 발생하지 않음
        * 3개월 주기 백업 시점에 함께 자동 실행
  · 자동 메일 발송 (rtgreen 로그인 전용):
        강등/탈퇴/승급 처리 후 대상자에게 자동 메일 안내
        다른 관리자 계정으로 로그인하면 메일 발송은 건너뜀
  · 장기미접속 조정: 6개월 이상 미접속, 6개월 주기
        매년 1/7월 1일 도래 (캘린더 기준)
      - 5레벨(일반회원)  → 탈퇴
      - 6레벨(우수회원)  → 일반회원
      - 7레벨(최우수회원) → 우수회원
      - 8레벨(명예회원)  → 제외
      - 9레벨(동호회관리자) → 제외
  · 자동 트리거된 조정도 반드시 미리보기 → 사용자 확인 후 적용됩니다.
"""


class HelpDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(
            parent,
            title="단축키 안내",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        label = wx.StaticText(panel, label="단축키 및 사용법(&S):")
        sizer.Add(label, 0, wx.ALL, 8)

        self.text = wx.TextCtrl(
            panel,
            value=SHORTCUTS_TEXT,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="단축키 안내",
        )
        sizer.Add(self.text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        close_btn = wx.Button(panel, wx.ID_OK, "닫기(&C)")
        sizer.Add(close_btn, 0, wx.ALIGN_CENTER | wx.ALL, 10)
        close_btn.SetDefault()

        panel.SetSizer(sizer)
        self.SetMinSize(wx.Size(600, 500))
        self.Fit()
        self.Centre()


def show_about(parent) -> None:
    info = wx.adv.AboutDialogInfo()
    info.SetName(APP_NAME)
    info.SetVersion(APP_VERSION)
    info.SetDescription(
        "초록등대 동호회 회원관리 자동화 도구.\n"
        "3개월마다 우수회원 백업, 6개월마다 장기미접속 회원 등급 조정.\n"
        "동호회관리자 권한이 있는 사용자만 사용할 수 있습니다."
    )
    wx.adv.AboutBox(info, parent)


# wx.adv 의존성 별도 import
import wx.adv  # noqa: E402
