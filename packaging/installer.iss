; -*- Inno Setup Script -*-
; OSL AI Assistant Inno Setup installer script.
; Bundles the PyInstaller-built native_ui, Ollama binary, and LibreOffice
; installer. Required Ollama models are downloaded during setup.
; Run `packaging\build.ps1`
; to produce a packed `deps/` directory, then compile with Inno Setup.

#define MyAppName "OSL AI Assistant"
#define MyAppNameShort "OSL AI Assistant"
#define MyAppNameShortNoSpace "OSL_AI_Assistant"
#define MyAppVersion "1.4.1"
#define MyAppPublisher "OSL ENG"
#define MyAppURL "https://example.com"
#define MyAppExeName "OSL_AI_Assistant.exe"
#define MyOllamaExeName "ollama.exe"
#define MyLibreOfficeMsi "LibreOffice.msi"
#define MyLegacyAppNameShort "OSL RAG Internal"

[Setup]
; Internal signing is the installer's responsibility; the build script
; signs the final binary if a cert is available.
; (Leave SignTool unset for non-commercial builds.)
AppId={{B4E7E5C0-0000-4000-9000-000000000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=output
OutputBaseFilename=OSL_AI_Assistant_Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\assets\app_icon.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Uninstallable=yes
CloseApplications=yes
MinVersion=10.0

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "데스크톱에 바로가기 만들기"; GroupDescription: "추가 작업:"
Name: "startup";     Description: "Windows 시작 시 자동 실행";      GroupDescription: "추가 작업:"; Flags: checkedonce

[Files]
; PyInstaller single-folder distribution
Source: "..\dist\OSL_AI_Assistant\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

; Ollama binary
Source: "deps\ollama\{#MyOllamaExeName}"; DestDir: "{app}\ollama"; Flags: ignoreversion

; Ollama runtime support files (llama-server.exe, DLLs, etc.)
Source: "deps\ollama\lib\ollama\*"; DestDir: "{app}\ollama\lib\ollama"; Flags: recursesubdirs ignoreversion skipifsourcedoesntexist

; LibreOffice installer (run silently post-install)
Source: "deps\{#MyLibreOfficeMsi}"; DestDir: "{tmp}"; Flags: ignoreversion deleteafterinstall

; License
Source: "..\LICENSE";   DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Dirs]
; App data directories (created at install time, populated at first run)
Name: "{userappdata}\{#MyAppNameShort}"
Name: "{localappdata}\{#MyAppNameShort}"
Name: "{localappdata}\{#MyAppNameShort}\logs"
Name: "{localappdata}\{#MyAppNameShort}\faiss_index"
Name: "{localappdata}\{#MyAppNameShort}\turbovec_index"

[Icons]
; Start Menu
Name: "{group}\{#MyAppNameShort}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\설정 폴더 열기"; Filename: "{userappdata}\{#MyAppNameShort}"
Name: "{group}\로그 폴더 열기"; Filename: "{localappdata}\{#MyAppNameShort}\logs"
Name: "{group}\제거";          Filename: "{uninstallexe}"

; Desktop (optional)
Name: "{commondesktop}\{#MyAppNameShort}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 1) Run LibreOffice installer silently (skipped if user already has it)
Filename: "{sys}\msiexec.exe"; Parameters: "/i ""{tmp}\{#MyLibreOfficeMsi}"" /passive /norestart"; \
    Flags: waituntilterminated; Check: ShouldInstallLibreOffice

; 2) Launch the app
Filename: "{app}\{#MyAppExeName}"; Description: "OSL AI Assistant 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up runtime state created by the app
Type: filesandordirs; Name: "{userappdata}\{#MyAppNameShort}"
Type: filesandordirs; Name: "{localappdata}\{#MyAppNameShort}"
Type: filesandordirs; Name: "{userappdata}\{#MyLegacyAppNameShort}"
Type: filesandordirs; Name: "{localappdata}\{#MyLegacyAppNameShort}"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\cache"
Type: filesandordirs; Name: "{app}\*.cache"
Type: filesandordirs; Name: "{app}\file_list_cache.json"
Type: filesandordirs; Name: "{app}\embed_log.txt"
Type: filesandordirs; Name: "{app}\chat_memory.json"
Type: filesandordirs; Name: "{userstartup}\{#MyAppNameShortNoSpace}.bat"
Type: filesandordirs; Name: "{userstartup}\{#MyLegacyAppNameShort}.bat"

[Code]
var
  FatalPostInstallFailure: Boolean;

procedure FailPostInstall(MessageText: String);
begin
  FatalPostInstallFailure := True;
  MsgBox(
    MessageText + #13#10#13#10 +
    '설치를 종료합니다. 네트워크 연결을 확인한 뒤 설치 파일을 다시 실행하세요.',
    mbCriticalError,
    MB_OK);
  WizardForm.Close;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if FatalPostInstallFailure then
  begin
    Confirm := False;
    Cancel := True;
  end;
end;

procedure StopOllamaProcesses();
var
  ResultCode: Integer;
begin
  // Update reliability is more important than keeping a running Ollama server.
  // taskkill returns a non-zero code when the process is absent; this is OK.
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM ollama.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM ollama_llama_server.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM llama-server.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function GetCustomSetupExitCode: Integer;
begin
  if FatalPostInstallFailure then
    Result := 1
  else
    Result := 0;
end;

function UrlReachable(Url: String): Boolean;
var
  Http: Variant;
begin
  Result := False;
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.SetTimeouts(3000, 3000, 3000, 3000);
    Http.Open('GET', Url, False);
    Http.Send('');
    Result := (Http.Status >= 200) and (Http.Status < 500);
  except
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  StopOllamaProcesses();
  if not UrlReachable('https://registry.ollama.ai/v2/') then
    Result := 'Ollama 모델 다운로드를 위해 인터넷 연결이 필요합니다. 네트워크 연결을 확인한 뒤 설치를 다시 실행하세요.';
end;

function WaitForOllamaServer(): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to 30 do
  begin
    if UrlReachable('http://127.0.0.1:11434/api/tags') then
    begin
      Result := True;
      Exit;
    end;
    Sleep(1000);
  end;
end;

function RunOllamaModelPull(ModelName: String; DisplayName: String): Boolean;
var
  ResultCode: Integer;
  Ok: Boolean;
begin
  Result := False;
  WizardForm.StatusLabel.Caption := DisplayName + ' 다운로드 중... 시간이 걸릴 수 있습니다.';
  WizardForm.Refresh;
  Ok := ExecAsOriginalUser(
    ExpandConstant('{app}\ollama\{#MyOllamaExeName}'),
    'pull ' + ModelName,
    ExpandConstant('{app}\ollama'),
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode);
  if (not Ok) or (ResultCode <> 0) then
  begin
    FailPostInstall(DisplayName + ' 다운로드에 실패했습니다.');
    Exit;
  end;
  Result := True;
end;

function InstallRequiredOllamaModels(): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  WizardForm.StatusLabel.Caption := 'Ollama 서버 시작 중...';
  WizardForm.Refresh;
  ExecAsOriginalUser(
    ExpandConstant('{app}\ollama\{#MyOllamaExeName}'),
    'serve',
    ExpandConstant('{app}\ollama'),
    SW_HIDE,
    ewNoWait,
    ResultCode);

  if not WaitForOllamaServer() then
  begin
    FailPostInstall('Ollama 서버를 시작하지 못했습니다.');
    Exit;
  end;

  if not RunOllamaModelPull('exaone3.5:2.4b', 'EXAONE 기본 모델') then Exit;
  if not RunOllamaModelPull('qwen3.5:4b', 'Qwen 어드바이저 모델') then Exit;
  Result := True;
end;

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

procedure CurStepChanged(CurStep: TSetupStep);
var
  BatPath, BatContent: String;
begin
  if CurStep = ssInstall then
    StopOllamaProcesses();

  if CurStep = ssPostInstall then
  begin
    if not InstallRequiredOllamaModels() then
      Exit;

    if WizardIsTaskSelected('startup') then
    begin
      // Use a .bat launcher (matches native_ui.py _enable_startup).
      BatPath := ExpandConstant('{userstartup}\{#MyAppNameShortNoSpace}.bat');
      BatContent :=
        '@echo off' + #13#10 +
        'start "" "' + ExpandConstant('{app}\{#MyAppExeName}') + '"' + #13#10;
      if not SaveStringToFile(BatPath, BatContent, False) then
        MsgBox('시작 프로그램 바로가기를 만들지 못했습니다.', mbError, MB_OK);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopOllamaProcesses();
end;
