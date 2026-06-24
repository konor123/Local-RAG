; -*- Inno Setup Script -*-
; OSL RAG Internal Inno Setup installer script.
; Bundles the PyInstaller-built native_ui, Ollama + models, HuggingFace
; embedding model, and LibreOffice installer. Run `packaging\build.ps1`
; to produce a packed `deps/` directory, then compile with Inno Setup.

#define MyAppName "OSL RAG Internal"
#define MyAppNameShort "OSL RAG Internal"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "OSL ENG"
#define MyAppURL "https://example.com"
#define MyAppExeName "native_ui.exe"
#define MyOllamaExeName "ollama.exe"
#define MyLibreOfficeMsi "LibreOffice.msi"

[Setup]
; Internal signing is the installer's responsibility; the build script
; signs the final binary if a cert is available.
; (Leave SignTool unset for non-commercial builds.)
AppId={{B4E7E5C0-0000-4000-9000-000000000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=output
OutputBaseFilename=OSL_RAG_Internal_Setup
Compression=lzma2
SolidCompression=no
DiskSpanning=yes
DiskSliceSize=700000000
SlicesPerDisk=1
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; SetupIconFile=icon.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Uninstallable=yes
MinVersion=10.0

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "데스크톱에 바로가기 만들기"; GroupDescription: "추가 작업:"
Name: "startup";     Description: "Windows 시작 시 자동 실행";      GroupDescription: "추가 작업:"

[Files]
; PyInstaller single-folder distribution
Source: "..\dist\native_ui\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

; Ollama binary
Source: "deps\ollama\{#MyOllamaExeName}"; DestDir: "{app}\ollama"; Flags: ignoreversion

; Ollama models (exaone3.5:2.4b + qwen3.5:4b + all-minilm)
Source: "deps\ollama_models\*"; DestDir: "{userpf}\.ollama\models"; Flags: recursesubdirs ignoreversion

; HuggingFace embedding model
Source: "deps\hf_models\*"; DestDir: "{userpf}\.cache\huggingface\hub"; Flags: recursesubdirs ignoreversion

; LibreOffice installer (run silently post-install)
Source: "deps\{#MyLibreOfficeMsi}"; DestDir: "{tmp}"; Flags: ignoreversion deleteafterinstall

; README / License
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\LICENSE";   DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Dirs]
; App data directories (created at install time, populated at first run)
Name: "{userappdata}\{#MyAppNameShort}"
Name: "{localappdata}\{#MyAppNameShort}"
Name: "{localappdata}\{#MyAppNameShort}\turbovec_index"

[Icons]
; Start Menu
Name: "{group}\{#MyAppNameShort}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\설정 폴더 열기"; Filename: "{userappdata}\{#MyAppNameShort}"
Name: "{group}\로그 폴더 열기"; Filename: "{app}\logs"
Name: "{group}\제거";          Filename: "{uninstallexe}"

; Desktop (optional)
Name: "{commondesktop}\{#MyAppNameShort}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 1) Run LibreOffice installer silently (skipped if user already has it)
Filename: "{sys}\msiexec.exe"; Parameters: "/i ""{tmp}\{#MyLibreOfficeMsi}"" /passive /norestart"; \
    Flags: waituntilterminated; Check: ShouldInstallLibreOffice

; 2) Pre-create the Ollama directory so the first run is fast
Filename: "{cmd}"; Parameters: "/C if not exist ""{userpf}\.ollama"" mkdir ""{userpf}\.ollama"""; \
    Flags: runhidden

; 3) Launch the app
Filename: "{app}\{#MyAppExeName}"; Description: "OSL RAG Internal 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up runtime state created by the app
Type: filesandordirs; Name: "{userappdata}\{#MyAppNameShort}"
Type: filesandordirs; Name: "{localappdata}\{#MyAppNameShort}"
Type: filesandordirs; Name: "{localappdata}\{#MyAppNameShort}\turbovec_index"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\cache"
Type: filesandordirs; Name: "{app}\*.cache"
Type: filesandordirs; Name: "{app}\file_list_cache.json"
Type: filesandordirs; Name: "{app}\embed_log.txt"
Type: filesandordirs; Name: "{app}\chat_memory.json"
Type: filesandordirs; Name: "{userstartup}\{#MyAppNameShort}.bat"

[Code]
// Custom check used by the [Run] section above.
function ShouldInstallLibreOffice(): Boolean;
begin
  // Skip if LibreOffice is already installed (PATH or registry hit).
  // Match ingest.py by checking both native and WOW6432Node paths.
  Result := not FileExists(ExpandConstant('{pf}\LibreOffice\program\soffice.exe')) and
            not FileExists(ExpandConstant('{pf32}\LibreOffice\program\soffice.exe')) and
            not RegKeyExists(HKLM, 'SOFTWARE\LibreOffice\LibreOffice') and
            not RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\LibreOffice\LibreOffice');
end;

procedure InitializeWizard();
begin
  // Default: enable "Windows 시작 시 자동 실행" task.
  WizardForm.TasksList.Checked[1] := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  BatPath, BatContent: String;
begin
  if CurStep = ssPostInstall then
  begin
    if IsTaskSelected('startup') then
    begin
      // Use a .bat launcher (matches native_ui.py _enable_startup).
      BatPath := ExpandConstant('{userstartup}\{#MyAppNameShort}.bat');
      BatContent :=
        '@echo off' + #13#10 +
        'start "" "' + ExpandConstant('{app}\{#MyAppExeName}') + '"' + #13#10;
      if not SaveStringToFile(BatContent, BatPath, False) then
        MsgBox('시작 프로그램 바로가기를 만들지 못했습니다.', mbError, MB_OK);
    end;
  end;
end;
