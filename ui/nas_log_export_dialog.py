"""자료실 접속 로그 — 내보내기 형식 선택 다이얼로그.

PaymentExportDialog 와 동일한 구조 — XLSX/HTML/TXT 중 골라 한 번에 저장.
"""
from __future__ import annotations

import wx


FORMAT_XLSX = "xlsx"
FORMAT_HTML = "html"
FORMAT_TXT = "txt"


class NasLogExportDialog(wx.Dialog):
    def __init__(self, parent) -> None:
        super().__init__(
            parent,
            title="자료실 접속 로그 내보내기",
            style=wx.DEFAULT_DIALOG_STYLE,
        )
        self.selected_formats: list[str] = []
        self._build_ui()
        self.Fit()
        self.Centre()

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(
            panel,
            label=(
                "저장할 형식을 선택하세요. 여러 개를 함께 선택하면\n"
                "한 번의 저장으로 backups 폴더에 모두 생성됩니다.\n"
                "(파일명: nas_access_log_YYYY-MM-DD.<확장자>)\n"
                "현재 화면의 필터(기간 / 회원 / 동작 / 카테고리)가 그대로 반영됩니다."
            ),
        )
        sizer.Add(intro, 0, wx.ALL, 12)

        self.cb_xlsx = wx.CheckBox(
            panel, label="엑셀 (.xlsx) — 한 시트, 자동 필터(&X)",
        )
        self.cb_html = wx.CheckBox(
            panel, label="HTML 리포트 (.html) — 한 페이지 표(&H)",
        )
        self.cb_txt = wx.CheckBox(
            panel, label="텍스트 (.txt) — 스크린리더 친화(&T)",
        )
        for cb in (self.cb_xlsx, self.cb_html, self.cb_txt):
            cb.SetValue(True)
            sizer.Add(cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        btn_sizer = wx.StdDialogButtonSizer()
        save_btn = wx.Button(panel, wx.ID_OK, "저장(&S)")
        save_btn.SetDefault()
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, "취소(&C)")
        save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        btn_sizer.AddButton(save_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)
        sizer.SetSizeHints(self)

    def _on_save(self, _event=None) -> None:
        formats: list[str] = []
        if self.cb_xlsx.IsChecked():
            formats.append(FORMAT_XLSX)
        if self.cb_html.IsChecked():
            formats.append(FORMAT_HTML)
        if self.cb_txt.IsChecked():
            formats.append(FORMAT_TXT)
        if not formats:
            wx.MessageBox(
                "최소 하나 이상의 형식을 선택해 주세요.",
                "선택 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        self.selected_formats = formats
        self.EndModal(wx.ID_OK)
