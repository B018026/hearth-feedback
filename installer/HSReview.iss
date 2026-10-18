; HS振り返り — Windows用インストーラー(Inno Setup 6)
; ビルド: python build.py --installer   (内部で ISCC /DSrcDir=... /DAppVersion=... を呼びます)
#ifndef SrcDir
  #error SrcDir (PyInstallerの出力フォルダ dist\HSReview) を /DSrcDir= で指定してください
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "HS振り返り"
#define AppExe "HSReview.exe"

[Setup]
AppId={{6D1F4B0E-3C2A-4F7B-9A55-7E0C2B8D41A9}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=尾張
DefaultDirName={autopf}\HSReview
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputBaseFilename=HSReview-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 管理者権限なしで、自分のユーザーにだけインストールする(ダイアログで全ユーザーも選べる)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
ArchitecturesInstallIn64BitMode=x64compatible
ShowLanguageDialog=no

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作る"; GroupDescription: "追加のショートカット:"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{#AppName}を起動する"; Flags: nowait postinstall skipifsilent

[Code]
// アンインストール時: 取り込んだ試合データ(%APPDATA%\HSReview)を残すか、消すかを選べる(初期値は「残す」)
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\HSReview');
    if DirExists(DataDir) then
    begin
      if MsgBox('取り込んだ試合のデータ(棋譜・AIの回答・カードデータ)も削除しますか?' + #13#10 +
                '「いいえ」を選ぶと残します(再インストールすると、また使えます)。' + #13#10 + #13#10 +
                '※ AIのAPIキーは、Windowsの資格情報マネージャーに保存されています。' + #13#10 +
                '　 不要な場合は、資格情報マネージャーの「HSReview」を削除してください。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
