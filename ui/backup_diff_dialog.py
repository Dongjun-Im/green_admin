"""백업 비교 다이얼로그.

저장된 분기 백업 폴더 두 개를 선택해
신규/승급/강등/빠짐을 한눈에 보여준다.
TXT 리포트로 내보내기 가능.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import wx

from config import BACKUPS_DIR
from core.backup_diff import (
    BackupDiff,
    diff_backups,
    list_backup_folders,
    load_snapshot,
    write_diff_report,
)
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


class BackupDiffDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(
            parent,
            title="백업 비교",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.folders = list_backup_folders(Path(BACKUPS_DIR))
        self.diff: BackupDiff | None = None
        self._build_ui()
        self.SetMinSize(wx.Size(720, 560))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        if not self.folders:
            sizer.Add(
                wx.StaticText(
                    panel,
                    label=(
                        "백업 폴더가 없습니다.\n"
                        "Ctrl+B 로 백업을 한 번 이상 실행하면 비교할 수 있습니다."
                    ),
                ),
                0, wx.ALL, 20,
            )
            close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
            sizer.Add(close_btn, 0, wx.ALIGN_CENTER | wx.ALL, 10)
            close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
            panel.SetSizer(sizer)
            return

        labels = [f.name for f in self.folders]

        sel_sizer = wx.BoxSizer(wx.HORIZONTAL)
        sel_sizer.Add(
            wx.StaticText(panel, label="이전(&O):"),
            0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5,
        )
        self.old_choice = wx.Choice(panel, choices=labels, name="이전 백업")
        sel_sizer.Add(self.old_choice, 1, wx.ALL, 5)

        sel_sizer.Add(
            wx.StaticText(panel, label="이후(&N):"),
            0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5,
        )
        self.new_choice = wx.Choice(panel, choices=labels, name="이후 백업")
        sel_sizer.Add(self.new_choice, 1, wx.ALL, 5)
        sizer.Add(sel_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # 기본값: 마지막 두 폴더
        if len(self.folders) >= 2:
            self.old_choice.SetSelection(len(self.folders) - 2)
            self.new_choice.SetSelection(len(self.folders) - 1)
        else:
            self.old_choice.SetSelection(0)
            self.new_choice.SetSelection(0)

        compare_btn = wx.Button(panel, wx.ID_ANY, "비교 실행(&R)")
        sizer.Add(compare_btn, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        sizer.Add(
            wx.StaticText(panel, label="결과(&L):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 5,
        )
        self.result_text = ItemTextCtrl(
            panel, value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="비교 결과",
        )
        sizer.Add(self.result_text, 1, wx.EXPAND | wx.ALL, 5)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.export_btn = wx.Button(panel, wx.ID_ANY, "TXT 내보내기(&E)")
        self.export_btn.Disable()
        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
        btn_sizer.Add(self.export_btn, 0, wx.ALL, 5)
        btn_sizer.Add(close_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        panel.SetSizer(sizer)
        compare_btn.Bind(wx.EVT_BUTTON, self._on_compare)
        self.export_btn.Bind(wx.EVT_BUTTON, self._on_export)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    def _announce(self) -> None:
        if self.folders:
            speak(f"백업 비교. 폴더 {len(self.folders)}개 발견.")

    # ---------- 동작 ----------

    def _on_compare(self, event) -> None:
        old_idx = self.old_choice.GetSelection()
        new_idx = self.new_choice.GetSelection()
        if old_idx == wx.NOT_FOUND or new_idx == wx.NOT_FOUND:
            return
        if old_idx == new_idx:
            speak("같은 백업 폴더는 비교할 수 없습니다.")
            return
        try:
            old = load_snapshot(self.folders[old_idx])
            new = load_snapshot(self.folders[new_idx])
        except Exception as e:
            wx.MessageBox(f"백업 로드 실패: {e}", "오류", wx.OK | wx.ICON_ERROR)
            return
        self.diff = diff_backups(old, new)
        self._render_diff()

    def _render_diff(self) -> None:
        d = self.diff
        if d is None:
            return
        lines: list[str] = []
        lines.append(f"{d.old.date_label}  →  {d.new.date_label}")
        lines.append(
            f"이전 {len(d.old.members)}명, 이후 {len(d.new.members)}명"
        )
        lines.append(d.summary)
        lines.append("=" * 50)

        if d.new_entries:
            lines.append(f"[신규 진입 {len(d.new_entries)}명]")
            for m in sorted(d.new_entries, key=lambda x: -x["level"]):
                lines.append(
                    f"  + {m['user_id']} ({m['nickname']}) "
                    f"- {m['level_label']}"
                )
            lines.append("")
        if d.promoted:
            lines.append(f"[승급 {len(d.promoted)}명]")
            for m in d.promoted:
                lines.append(
                    f"  ↑ {m['user_id']} ({m['nickname']}) "
                    f"- {m['old_level_label']} → {m['new_level_label']}"
                )
            lines.append("")
        if d.demoted:
            lines.append(f"[강등 {len(d.demoted)}명]")
            for m in d.demoted:
                lines.append(
                    f"  ↓ {m['user_id']} ({m['nickname']}) "
                    f"- {m['old_level_label']} → {m['new_level_label']}"
                )
            lines.append("")
        if d.dropped:
            lines.append(f"[빠짐 {len(d.dropped)}명]")
            for m in sorted(d.dropped, key=lambda x: -x["level"]):
                lines.append(
                    f"  - {m['user_id']} ({m['nickname']}) "
                    f"- 이전 {m['level_label']}"
                )
            lines.append("")
        if not (d.new_entries or d.promoted or d.demoted or d.dropped):
            lines.append("")
            lines.append("(변화 없음)")

        self.result_text.SetValue("\n".join(lines))
        self.export_btn.Enable()
        speak(f"비교 완료. {d.summary}")

    def _on_export(self, event) -> None:
        if self.diff is None:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(BACKUPS_DIR) / (
            f"diff_{self.diff.old.date_label}"
            f"_to_{self.diff.new.date_label}_{ts}.txt"
        )
        try:
            write_diff_report(self.diff, out)
        except Exception as e:
            wx.MessageBox(f"내보내기 실패: {e}", "오류", wx.OK | wx.ICON_ERROR)
            return
        speak(f"비교 결과를 저장했습니다.")
        wx.MessageBox(
            f"비교 결과를 저장했습니다:\n{out}",
            "내보내기 완료",
            wx.OK | wx.ICON_INFORMATION,
        )

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()
