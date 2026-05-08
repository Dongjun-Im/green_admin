"""회원 메모/태그 편집 다이얼로그.

검색 다이얼로그에서 호출. 저장은 로컬 SQLite (member_notes.db) — 사이트와 무관.
"""
from __future__ import annotations

import wx

from core.member_notes import MemberNote, MemberNotesStore
from core.models import Member
from screen_reader import speak


class MemberNoteDialog(wx.Dialog):
    def __init__(self, parent, member: Member, store: MemberNotesStore):
        super().__init__(
            parent,
            title=f"메모 / 태그 — {member.user_id}",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.member = member
        self.store = store
        self.note: MemberNote = (
            store.get(member.user_id)
            or MemberNote(user_id=member.user_id, note="", tags=[])
        )
        self.changed = False
        self._build_ui()
        self.SetMinSize(wx.Size(500, 460))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        info = (
            f"아이디: {self.member.user_id}\n"
            f"이름·닉: {self.member.name} / {self.member.nickname}"
        )
        sizer.Add(wx.StaticText(panel, label=info), 0, wx.ALL, 10)

        sizer.Add(
            wx.StaticText(panel, label="태그(&T) — 콤마로 구분:"),
            0, wx.LEFT | wx.RIGHT, 10,
        )
        self.tags_input = wx.TextCtrl(
            panel, value=", ".join(self.note.tags), name="태그",
        )
        sizer.Add(self.tags_input, 0, wx.EXPAND | wx.ALL, 10)

        sizer.Add(
            wx.StaticText(panel, label="메모(&N):"),
            0, wx.LEFT | wx.RIGHT, 10,
        )
        self.note_input = wx.TextCtrl(
            panel,
            value=self.note.note,
            style=wx.TE_MULTILINE,
            name="메모",
        )
        sizer.Add(self.note_input, 1, wx.EXPAND | wx.ALL, 10)

        if self.note.updated_at:
            sizer.Add(
                wx.StaticText(panel, label=f"마지막 수정: {self.note.updated_at}"),
                0, wx.LEFT | wx.RIGHT, 10,
            )

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        save_btn = wx.Button(panel, wx.ID_OK, "저장(&S)")
        delete_btn = wx.Button(panel, wx.ID_ANY, "삭제(&D)")
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, "취소(&C)")
        btn_sizer.Add(save_btn, 0, wx.ALL, 5)
        btn_sizer.Add(delete_btn, 0, wx.ALL, 5)
        btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 8)

        panel.SetSizer(sizer)
        save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        cancel_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self.note_input.SetFocus()

    def _announce(self) -> None:
        if self.note.has_content:
            speak(f"기존 메모가 있습니다. {self.note.display_summary()}")
        else:
            speak("새 메모를 작성합니다.")

    def _on_save(self, event):
        tags = [t.strip() for t in self.tags_input.GetValue().split(",")]
        tags = [t for t in tags if t]
        self.note.note = self.note_input.GetValue().strip()
        self.note.tags = tags
        try:
            self.store.upsert(self.note)
        except Exception as e:
            wx.MessageBox(f"저장 실패: {e}", "오류", wx.OK | wx.ICON_ERROR)
            return
        self.changed = True
        speak("메모를 저장했습니다.")
        self.EndModal(wx.ID_OK)

    def _on_delete(self, event):
        if not self.note.has_content:
            self.EndModal(wx.ID_CANCEL)
            return
        confirm = wx.MessageBox(
            "이 회원의 메모와 태그를 삭제합니다. 계속하시겠습니까?",
            "메모 삭제",
            wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT,
        )
        if confirm != wx.YES:
            return
        try:
            self.store.delete(self.member.user_id)
        except Exception as e:
            wx.MessageBox(f"삭제 실패: {e}", "오류", wx.OK | wx.ICON_ERROR)
            return
        self.changed = True
        speak("메모를 삭제했습니다.")
        self.EndModal(wx.ID_OK)

    def _on_char_hook(self, event: wx.KeyEvent):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()
