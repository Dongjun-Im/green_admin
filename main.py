"""초록등대 회원관리 - 진입점.

UI 모드 흐름:
    1. wx.App 생성
    2. green_auth 로 소리샘 로그인 (저장된 자격 증명 자동 로그인 지원)
    3. admin_permission_check 로 동호회관리자 등급 확인
    4. MainFrame 표시
    5. wx.CallAfter 로 자동 스케줄 트리거 (3개월 백업 / 6개월 조정 미리보기)

헤드리스 모드 (v1.2.11):
    `python main.py --task <name>` 또는 `초록등대회원관리.exe --task <name>` 으로
    UI 없이 한 가지 작업만 수행하고 종료. Windows 작업 스케줄러 연동용.
    지원 작업은 core/scheduler_runner.py:ALL_TASKS 참고.

`import wx` 는 의도적으로 _run_ui() 안에서만 함 — 헤드리스 모드는 wx 없이도
모듈을 임포트할 수 있어야 테스트가 깔끔하고, 작업 스케줄러가 GUI 세션이 없는
백그라운드 컨텍스트에서 실행할 때도 안전.
"""
import argparse
import os
import sys


def _parse_args(argv: list[str]) -> argparse.Namespace:
    # 작업 키 목록은 scheduler_runner 가 권위 — 새 작업 추가 시 거기만 고치면 됨.
    from core.scheduler_runner import ALL_TASKS
    p = argparse.ArgumentParser(
        prog="초록등대회원관리",
        description="초록등대 동호회 회원관리 (UI 모드 + 헤드리스 작업 모드).",
        add_help=True,
    )
    p.add_argument(
        "--task",
        choices=ALL_TASKS,
        default=None,
        metavar="<name>",
        help=(
            "헤드리스 모드로 한 작업만 수행하고 종료. UI 안 뜸. "
            f"지원: {', '.join(ALL_TASKS)}."
        ),
    )
    return p.parse_args(argv)


def _run_ui() -> int:
    """UI 모드 실행 — wx + ChorokGreenAdminApp 을 여기서만 로드한다."""
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
                wx.MessageBox(reason, "권한 거부", wx.OK | wx.ICON_ERROR)
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
                        path, winsound.SND_FILENAME | winsound.SND_ASYNC,
                    )
                else:
                    winsound.Beep(800, 150)
            except Exception:
                pass

    app = ChorokGreenAdminApp(False)
    if not app.IsMainLoopRunning():
        # OnInit 이 False 를 반환하면 MainLoop 진입 안 함
        if app.GetTopWindow() is None:
            return 1
    app.MainLoop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # 헤드리스 모드 — wx 진입 없이 작업만 수행하고 종료.
    if args.task:
        from core.scheduler_runner import run_task
        return run_task(args.task)

    # 기본: UI 모드.
    return _run_ui()


if __name__ == "__main__":
    sys.exit(main())
