"""단축키 안내 대화상자 (Ctrl+K).

단축키만 간단히 모아 보여 줍니다. 기능 설명은 사용 설명서(Shift+F1) 에 있습니다.
"""
import wx

from config import APP_NAME, APP_VERSION
from ui.item_text_ctrl import ItemTextCtrl


SHORTCUTS_TEXT = f"""{APP_NAME} — 단축키  (버전 {APP_VERSION})

[작업]
  Ctrl+F          회원 검색 (아이디·이름·닉네임 부분 일치)
  Ctrl+G          (검색 창에서) 선택한 회원 등급 변경
  Alt+W           (검색 창에서) 선택한 회원 탈퇴 처리 — 여러 명 체크 시 일괄
  Ctrl+N          (검색 창에서) 회원 메모/태그 편집
  Ctrl+T          회원 통계 (등급별 인원 + 최근 활동)
  Ctrl+B          우수회원 백업 (이어서 게시물 기반 자동 승급도 진행)
  Ctrl+U          게시물 기반 자동 승급만 실행
  Ctrl+R          장기미접속 조정 — 미리보기
  Ctrl+Shift+R    장기미접속 조정 — 실제 적용 (확인 후)
  Ctrl+Z          마지막 작업 되돌리기
  Ctrl+M          수동 메일 발송 (rtgreen 계정 전용)
  Ctrl+P          자료실 구독비 관리
  Ctrl+D          지금 작업 가능 여부 확인 (음성)

[정보 / 도움말]
  Ctrl+I          마지막 작업일 / 다음 예정일 음성 안내
  Ctrl+K          이 단축키 목록
  Shift+F1        사용 설명서 (챕터별 안내)
  F1              프로그램 정보

[파일]
  Ctrl+O          백업 폴더 열기
  Ctrl+Shift+Y    등급 변경 이력
  Ctrl+Shift+D    백업 비교 (분기 간 신규/승급/강등/빠짐)
  Ctrl+Shift+L    작업 로그 뷰어
  Ctrl+L          로그아웃 (저장된 자격 증명 삭제 후 재로그인)
  Alt+F4          프로그램 종료

[목록 / 체크 목록 안에서]
  ↑ / ↓           이전 / 다음 항목
  Home / End      처음 / 끝 항목
  Space           체크 목록에서 항목 선택 / 해제
  Esc             현재 음성 중단

* 기능별 자세한 사용법은 Shift+F1 (사용 설명서) 에서 챕터별로 볼 수 있습니다.
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

        label = wx.StaticText(panel, label="단축키 목록(&S):")
        sizer.Add(label, 0, wx.ALL, 8)

        self.text = wx.TextCtrl(
            panel,
            value=SHORTCUTS_TEXT,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="단축키 목록",
        )
        sizer.Add(self.text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        hint = wx.StaticText(
            panel, label="기능 설명은 Shift+F1 (사용 설명서) 을 보세요."
        )
        sizer.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        close_btn = wx.Button(panel, wx.ID_OK, "닫기(&C)")
        sizer.Add(close_btn, 0, wx.ALIGN_CENTER | wx.ALL, 10)
        close_btn.SetDefault()

        panel.SetSizer(sizer)
        self.SetEscapeId(wx.ID_OK)
        self.SetMinSize(wx.Size(560, 480))
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
