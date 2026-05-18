; 초록등대 회원관리 — Inno Setup 설치 스크립트
;
; 빌드 방법:
;   1) 먼저 PyInstaller 로 onedir 빌드:  py -3.12 -m PyInstaller --noconfirm chorok_green_admin.spec
;      → dist\초록등대회원관리\ 폴더가 만들어짐
;   2) Inno Setup 6 설치 후:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;      → installer_out\초록등대회원관리_v1.1.0_setup.exe 가 만들어짐
;   (build_release.py 가 1)+2) 와 무설치 ZIP 까지 한 번에 해 줌)

#define AppName          "초록등대 회원관리"
#define AppVersion       "1.3.0"
#define AppPublisher     "초록등대 동호회"
#define AppExeName       "초록등대회원관리.exe"
#define SourceDir        "dist\초록등대회원관리"

[Setup]
; AppId 는 한 프로그램을 고유 식별 — 절대 바꾸지 말 것 (바꾸면 업데이트 설치가 새 프로그램으로 인식됨)
AppId={{8F3A2C1D-9B4E-4A7F-A1C2-3D5E6F708192}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer_out
OutputBaseFilename=초록등대회원관리_v{#AppVersion}_setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; 관리자 권한이 없으면 사용자 폴더(%LocalAppData%)에 설치하도록 선택 가능
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayName={#AppName} v{#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; postinstall: 정상(대화형) 설치 후 마지막 화면의 "프로그램 실행" 체크박스용.
; nowait: 본 설치관리자를 잠그지 않고 곧장 자기 EXE 실행.
; skipifsilent 를 일부러 제외 — /VERYSILENT 자동 업데이트 흐름에서도 새 버전이
; 자동으로 켜져야 하기 때문 (v1.2.9 무인 설치 흐름).
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall
