# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None
app_dir = os.path.abspath('.')

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
    ],
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
        # ui 패키지
        'ui',
        'ui.main_frame',
        'ui.item_text_ctrl',
        'ui.confirm_dialog',
        'ui.help_dialog',
        'ui.search_dialog',
        'ui.mail_dialog',
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
        'test',
        'unittest',
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
