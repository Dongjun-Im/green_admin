"""초록등대 회원관리 - 진입점.

흐름:
    1. wx.App 생성
    2. green_auth 로 소리샘 로그인 (저장된 자격 증명 자동 로그인 지원)
    3. admin_permission_check 로 동호회관리자 등급 확인
    4. MainFrame 표시
    5. wx.CallAfter 로 자동 스케줄 트리거 (3개월 백업 / 6개월 조정 미리보기)
"""
import os
import sys

import wx

from config import APP_NAME
from green_auth import run_authentication
from screen_reader import speak


class ChorokGreenAdminApp(wx.App):
    def OnInit(self) -> bool:
        self.session = None
        self.user_id = ""
        self._play_sound("startup.wav")

        # 1) 인증
        auth = run_authentication(APP_NAME)
        if auth is None:
            return False
        self.session = auth.session
        self.user_id = auth.user_id

        # 2) 권한 체크 - 동호회관리자만
        from core.permission import admin_permission_check
        speak("동호회관리자 권한을 확인하는 중입니다.")
        wx.SafeYield()
        ok, reason = admin_permission_check(self.session, self.user_id)
        if not ok:
            speak(reason)
            wx.MessageBox(
                reason,
                "권한 거부",
                wx.OK | wx.ICON_ERROR,
            )
            return False

        # 3) 메인 프레임
        try:
            import winsound
            winsound.Beep(1500, 200)
        except Exception:
            pass

        from ui.main_frame import MainFrame
        frame = MainFrame(self.session, self.user_id)
        self.SetTopWindow(frame)
        frame.Show()

        # 4) 자동 스케줄 트리거 (UI가 떠 있는 상태에서)
        wx.CallAfter(frame.run_scheduled_tasks_if_due)
        return True

    def OnExit(self) -> int:
        self._play_sound("shutdown.wav")
        try:
            from screen_reader import cancel_speech
            cancel_speech()
        except Exception:
            pass
        return 0

    def _play_sound(self, name: str) -> None:
        try:
            import winsound
            from config import SOUNDS_DIR
            path = os.path.join(SOUNDS_DIR, name)
            if os.path.exists(path):
                winsound.PlaySound(
                    path,
                    winsound.SND_FILENAME | winsound.SND_ASYNC,
                )
            else:
                winsound.Beep(800, 150)
        except Exception:
            pass


def main() -> int:
    app = ChorokGreenAdminApp(False)
    if not app.IsMainLoopRunning():
        # OnInit 이 False 를 반환하면 MainLoop 진입 안 함
        if app.GetTopWindow() is None:
            return 1
    app.MainLoop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
