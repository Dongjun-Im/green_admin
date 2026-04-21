"""스크린리더 친화적인 단일행 표시용 TextCtrl.

Windows 메시지 레벨에서 화살표/Home/End/Enter 등을 차단해
스크린리더가 목록 전체를 읽지 않고 한 항목만 읽도록 한다.
초록멀티의 ItemTextCtrl 패턴과 동일.
"""
import wx


class ItemTextCtrl(wx.TextCtrl):
    def MSWHandleMessage(self, msg, wParam, lParam):
        WM_KEYDOWN = 0x0100
        WM_CHAR = 0x0102
        if msg in (WM_KEYDOWN, WM_CHAR):
            blocked_vk = {
                0x24,  # Home
                0x23,  # End
                0x26,  # Up
                0x28,  # Down
                0x0D,  # Return
                0x08,  # Back
                0x21,  # PageUp
                0x22,  # PageDown
                0x1B,  # Escape
                0x2E,  # Delete
            }
            if wParam in blocked_vk:
                return True, 0
        return super().MSWHandleMessage(msg, wParam, lParam)
