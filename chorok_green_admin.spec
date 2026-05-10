# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None
app_dir = os.path.abspath('.')

# data/google_credentials.json 이 빌드 시 존재하면 exe 안에 번들.
# 사용자는 첫 실행 시 별도 다운로드·복사 없이 브라우저 OAuth 승인만 하면 된다.
# 번들된 client_id 는 데스크톱 OAuth 클라이언트라 노출되어도 사용자별 토큰
# (google_token.json) 으로만 실제 권한이 부여되므로 보안상 안전.
_credentials_path = os.path.join(app_dir, 'data', 'google_credentials.json')
_extra_datas = []
if os.path.isfile(_credentials_path):
    _extra_datas.append((_credentials_path, '.'))

a = Analysis(
    ['main.py'],
    pathex=[app_dir],
    binaries=[],
    datas=[
        (os.path.join(app_dir, 'green_auth'), 'green_auth'),
        (os.path.join(app_dir, 'sounds'), 'sounds'),
        (os.path.join(app_dir, 'core'), 'core'),
        (os.path.join(app_dir, 'ui'), 'ui'),
        (os.path.join(app_dir, 'tools'), 'tools'),
        # 사용방법.txt 는 한글 파일명 인코딩 이슈로 spec 에서 제외.
        # 필요하면 dist 폴더 옆에 수동으로 복사.
    ] + _extra_datas,
    hiddenimports=[
        # green_auth 패키지
        'green_auth',
        'green_auth.auth_app',
        'green_auth.authenticator',
        'green_auth.config',
        'green_auth.credentials',
        'green_auth.login_dialog',
        'green_auth.screen_reader',
        # core 패키지
        'core',
        'core.models',
        'core.permission',
        'core.member_parser',
        'core.crawler',
        'core.member_admin',
        'core.backup_service',
        'core.level_adjustment',
        'core.promotion_service',
        'core.schedule_tracker',
        'core.log_writer',
        'core.post_counter',
        'core.post_count_green3',
        'core.mail_sender',
        # core 패키지 (v0.4 신규)
        'core.log_reader',
        'core.backup_diff',
        'core.backup_retention',
        'core.undo_stack',
        'core.member_notes',
        'core.html_report',
        'core.keybindings',
        # core 패키지 (v0.5 신규)
        'core.activity_counter',
        'core.mvp_service',
        'core.pending_members',
        # core 패키지 (v1.0 신규)
        'core.level_history',
        'core.site_diagnostics',
        'core.update_check',
        # core 패키지 (v1.0.4 신규)
        'core.admin_flags',
        'core.app_options',
        'core.progress_audio',
        'core.board_admin',
        # core 패키지 — 자료실 구독비 관리
        'core.toss_xlsx',
        'core.payment_store',
        'core.payment_matcher',
        'core.payment_matrix',
        'core.payment_xlsx',
        'core.payment_html',
        'core.payment_txt',
        'core.payment_mail',
        # DSM(Synology) 통합 (그룹 B)
        'core.dsm_config',
        'core.dsm_client',
        'core.dsm_service',
        'core.dsm_workflow',
        'core.sheets_sync',
        # ui 패키지
        'ui',
        'ui.main_frame',
        'ui.item_text_ctrl',
        'ui.confirm_dialog',
        'ui.help_dialog',
        'ui.search_dialog',
        'ui.mail_dialog',
        # ui 패키지 (v0.4 신규)
        'ui.level_change_dialog',
        'ui.stats_dialog',
        'ui.backup_diff_dialog',
        'ui.log_viewer_dialog',
        'ui.confirm_promotion_dialog',
        'ui.member_note_dialog',
        'ui.promotion_imminent_dialog',
        # ui 패키지 (v0.5 신규)
        'ui.mvp_dialog',
        'ui.pending_member_dialog',
        'ui.progress_dialog',
        'ui.board_dialog',
        # ui 패키지 (v1.0 신규)
        'ui.level_history_dialog',
        # ui 패키지 — 자료실 구독비 관리
        'ui.payment_alias_dialog',
        'ui.payment_dialog',
        'ui.payment_export_dialog',
        'ui.payment_mail_dialog',
        'ui.sheets_sync_dialog',
        'ui.dsm_setup_dialog',
        'ui.dsm_dialog',
        'ui.dsm_create_user_dialog',
        'ui.new_subscriber_dialog',
        # 표준 라이브러리 (JIT 사용)
        'sqlite3',
        # 외부 의존성 (동적 import 대비)
        'win32com.client',
        'lxml',
        'lxml.etree',
        'lxml._elementpath',
        'bs4',
        'cryptography',
        'cryptography.fernet',
        'cryptography.hazmat',
        'cryptography.hazmat.backends.openssl',
        'openpyxl',
        'openpyxl.workbook',
        'openpyxl.styles',
        'openpyxl.utils',
        'dateutil',
        'dateutil.relativedelta',
        'dateutil.parser',
        'requests',
        'urllib3',
        'charset_normalizer',
        'idna',
        # 토스 거래내역 복호화 (자료실 구독비 관리)
        'msoffcrypto',
        'olefile',
        # 구글시트 양방향 동기화 (Phase 2) — google-api-python-client
        # 동적 import 가 많아 패키지 단위로 광범위 등록.
        'google.auth',
        'google.auth.transport.requests',
        'google.oauth2.credentials',
        'google_auth_oauthlib',
        'google_auth_oauthlib.flow',
        'googleapiclient',
        'googleapiclient.discovery',
        'googleapiclient.http',
        'googleapiclient.discovery_cache',
        'googleapiclient.discovery_cache.base',
        'googleapiclient.discovery_cache.file_cache',
        'httplib2',
        'oauthlib',
        'requests_oauthlib',
        'uritemplate',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'PIL',
        'pandas',
        'scipy',
        # 'test' / 'unittest' 는 제외하지 않음 — google-auth-oauthlib 가
        # OAuth 콜백 처리 중 unittest.mock 을 transitive import 하는데,
        # 빌드에서 빠지면 첫 동기화에서 "No module named 'unittest'" 로 실패.
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='초록등대회원관리',
    debug=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='초록등대회원관리',
)
