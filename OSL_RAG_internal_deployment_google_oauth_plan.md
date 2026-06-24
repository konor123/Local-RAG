# OSL RAG 사내 배포 별도 프로젝트 계획

## 1\. 전제

* 대상은 외부 판매용이 아니라 사내 동료용 배포판이다.
* 각 사용자 PC에서 독립 실행한다.
* 외부 공개 DNS, `slim.sh`, 외부 터널은 사용하지 않는다.
* AI provider는 Google만 지원한다.
* 인증은 API key 입력이 아니라 Google OAuth만 사용한다.
* OpenAI 지원은 이번 별도 프로젝트 범위에서 제외한다.
* LibreOffice는 설치 프로그램에 포함한다.
* Streamlit은 v1에서 로컬 UI로 유지하되, 파일/폴더 열기는 local helper API로 처리한다.
* 기존 버전을 유지하며 별도의 프로젝트로 진행한다.

## 2\. 최종 목표 구조

```text
사용자 PC
├─ OSL RAG Tray App
├─ Local UI, initially Streamlit on 127.0.0.1
├─ Local helper API for opening files/folders
├─ Background file scanner / embedder
├─ TurboVec local index
├─ LibreOffice bundled or installed by installer
├─ Google OAuth login
└─ Local encrypted token/config storage
```

## 3\. Phase 0 — 보안 정리

### 목표

배포 전에 하드코딩된 key/token을 제거한다.

### 작업

* `gemini\_client.py`의 하드코딩 Gemini API key 제거.
* `tray\_server.py`의 Telegram token/chat id 제거 또는 비활성화.
* `ingest.py`의 Telegram token/chat id 제거 또는 비활성화.
* 이미 노출된 Gemini API key는 폐기/revoke.
* 대화 로그와 인덱싱 로그에 token, OAuth code, refresh token이 기록되지 않도록 정책화.

### 검증

```text
AIza
BOT\_TOKEN
CHAT\_ID
GEMINI\_API\_KEY 기본값
refresh\_token
access\_token
```

위 민감 문자열이 코드와 로그에 남지 않는지 확인한다.

## 4\. Phase 1 — slim 제거와 localhost-only 실행

### 목표

사내 사용자 PC에서만 동작하는 로컬 앱으로 전환한다.

### 작업

* `start.bat`에서 `slim share` 실행 제거.
* `tray\_server.py`에서 slim URL 감시, URL 복사 메뉴, slim 상태 메뉴 제거.
* Streamlit은 `127.0.0.1:8501`에만 바인딩.
* 트레이 메뉴에 “OSL RAG 열기” 추가.
* 트레이 메뉴에서 로컬 URL 복사 기능 제공.

### 검증

* `start\_silent.vbs` 또는 설치된 바로가기에서 실행 가능.
* `\*.slim.show` URL이 생성되지 않음.
* `http://127.0.0.1:8501` 접속 가능.
* 외부 네트워크에서 접근 불가.

## 5\. Phase 2 — 로컬 UI와 파일/폴더 열기 UX

### 목표

검색 결과에서 파일 또는 디렉토리를 클릭해 바로 열 수 있게 한다.

### 배경

Streamlit은 브라우저 기반 UI라서 Windows 파일 경로를 직접 열기에 불편하다. 현재 UI는 경로 클릭 시 클립보드 복사 중심으로 동작하며, `file://` 링크는 브라우저 보안 정책, 한글/공백/UNC 경로, 네트워크 드라이브 처리에서 일관성이 떨어질 수 있다.

### v1 권장 방식

Streamlit을 유지하되, 파일/폴더 열기는 별도의 localhost helper가 처리한다.

```text
Streamlit UI
→ 127.0.0.1 local helper API
→ Windows os.startfile / explorer 실행
```

### 검색 결과 버튼

각 파일 검색 결과와 RAG 참조 문서에 아래 액션을 제공한다.

* 파일 열기
* 폴더 열기
* 탐색기에서 위치 보기
* 경로 복사

### Windows 구현 방식

파일 기본 앱으로 열기:

```python
os.startfile(file_path)
```

폴더 열기:

```python
os.startfile(folder_path)
```

탐색기에서 파일 선택:

```python
subprocess.run(["explorer", "/select,", file_path], shell=False)
```

### helper API 보안 규칙

* `127.0.0.1`에만 바인딩한다.
* 인덱싱 대상 root 하위 경로만 열기 허용한다.
* 임의 명령 실행 API를 제공하지 않는다.
* `shell=True`를 사용하지 않는다.
* path traversal을 차단한다.
* 요청마다 session nonce 또는 local API token을 검증한다.
* 민감한 전체 경로를 불필요하게 원격 로그로 전송하지 않는다.

### 중장기 UI 방향

v1에서는 Streamlit + local helper API를 사용한다. 검색 결과 탐색, 미리보기, 드래그앤드롭, 키보드 내비게이션 등이 중요해지면 PySide6 또는 Tauri 기반 native UI 전환을 검토한다.

### 검증

* 파일 열기 버튼이 기본 앱으로 파일을 연다.
* 폴더 열기 버튼이 Explorer로 폴더를 연다.
* 위치 보기 버튼이 Explorer에서 파일을 선택한다.
* 허용되지 않은 경로 요청은 거부된다.
* 브라우저 새로고침/재시작 후에도 helper API token이 유효하게 관리된다.

## 6\. Phase 3 — LibreOffice 포함 설치

### 목표

사용자 PC에 LibreOffice가 없어도 `.doc`, `.ppt` 등 변환 기능이 동작하게 한다.

### 권장 방식

1순위는 LibreOffice 공식 installer를 OSL RAG installer에 포함하고 silent install하는 방식이다.

```text
OSL-RAG-Setup.exe
├─ OSL RAG app
├─ Python/runtime bundle
├─ TurboVec dependency
├─ LibreOffice installer
└─ default config/templates
```

### 설치 흐름

1. LibreOffice 설치 여부 확인.
2. 설치되어 있으면 `soffice.exe` 경로 확인.
3. 없으면 bundled LibreOffice installer를 silent mode로 실행.
4. 최종 `soffice.exe` 경로를 config에 저장.

### 후보 경로

```text
C:\\Program Files\\LibreOffice\\program\\soffice.exe
```

### 검증

* clean Windows VM에서 LibreOffice 없이 설치 성공.
* 설치 후 `soffice.exe` 경로 자동 인식.
* `.doc`, `.ppt` 변환 smoke test 성공.
* LibreOffice 라이선스 고지 포함.

## 7\. Phase 4 — 설정 시스템

### 목표

사용자별 설정을 소스 폴더가 아니라 사용자 프로필 아래 저장한다.

### 새 모듈 후보

```text
config\_manager.py
token\_store.py
```

### 설정 위치

```text
%APPDATA%\\OSL RAG\\config.json
```

### 민감정보 저장 위치

* Google OAuth access token / refresh token은 config에 직접 저장하지 않는다.
* Windows Credential Manager 또는 DPAPI 암호화 저장을 사용한다.

### config 예시

```json
{
  "ai\_provider": "google",
  "auth": {
    "method": "google\_oauth"
  },
  "libreoffice\_path": "C:/Program Files/LibreOffice/program/soffice.exe",
  "streamlit": {
    "host": "127.0.0.1",
    "port": 8501
  },
  "vector": {
    "backend": "turbovec",
    "index\_dir": "%LOCALAPPDATA%/OSL RAG/turbovec\_index",
    "processed\_files\_path": "%LOCALAPPDATA%/OSL RAG/processed\_files\_turbovec.txt"
  },
  "scan": {
    "scan\_local\_drives": true,
    "scan\_network\_drives": true,
    "include\_paths": \[],
    "exclude\_paths": \[
      "C:/Windows",
      "C:/Program Files",
      "C:/Program Files (x86)",
      "C:/Users/\*/AppData"
    ],
    "exclude\_patterns": \[
      ".git",
      "node\_modules",
      ".venv",
      "\_\_pycache\_\_",
      "\*.env",
      "\*.pem",
      "\*.key",
      "\*.pfx"
    ],
    "max\_file\_size\_mb": 50
  }
}
```

## 8\. Phase 5 — Google OAuth 전용 Provider

### 목표

AI provider를 Google OAuth 기반으로만 제공한다.

### 제외

* OpenAI provider 제외.
* Anthropic provider 제외.
* 개인 API key 기본 입력 방식 제외.

### 새 구조 후보

```text
ai\_providers/
├─ base.py
├─ google\_oauth.py
├─ provider\_manager.py
└─ token\_store.py
```

### OAuth 방식

우선순위:

1. Local loopback OAuth + PKCE
2. 필요 시 device code flow 검토

### Local loopback 흐름

```text
앱에서 Google 로그인 클릭
→ 기본 브라우저 오픈
→ 개인 Google 계정 로그인
→ 127.0.0.1:<random\_port>/oauth/callback 으로 authorization code 수신
→ access token / refresh token 획득
→ token\_store에 암호화 저장
```

### 중요한 결정 사항

Google OAuth만으로 Gemini 호출을 어떻게 할지 확정해야 한다.

권장 방향:

* 장기 안정성: Vertex AI Gemini + Google OAuth / Google Cloud 권한 사용.
* 단기 검증: Google OAuth flow, token 저장, auth status UI 먼저 구현.

Gemini Developer API key 방식은 이번 프로젝트의 기본 경로에서 제외한다.

## 9\. Phase 6 — 기존 Gemini 호출부 교체

### 목표

`gemini\_client.py` 직접 의존을 Google OAuth provider interface로 전환한다.

### 수정 대상

* `gemini\_client.py`
* `agent\_engine.py`
* `unified\_engine.py`
* `model\_smoke\_test.py`

### 공통 인터페이스 후보

```python
class AIProvider:
    def auth\_status(self): ...
    def login(self): ...
    def logout(self): ...
    def refresh\_token(self): ...
    def plan(self, question: str): ...
    def synthesize(self, question: str, context: str): ...
    def agent\_response(self, question: str, chat\_history: list): ...
```

### 검증

* 로그인 전에는 설정/로그인 안내 표시.
* 로그인 후 계획 수립, 답변 종합, agent tool-calling 경로 동작.
* token 만료 시 refresh 또는 재로그인 안내.
* logout 시 저장 token 삭제.

## 10\. Phase 7 — 드라이브 자동 탐지와 포함/제외 설정

### 목표

하드코딩된 `X:/`, `Y:/`, `Z:/` 의존을 제거한다.

### 수정 대상

* `cache\_manager.py`
* `tools.py`
* `ingest.py`
* `agent\_engine.py`
* `app.py`

### 정책

* 모든 로컬 드라이브 자동 탐지.
* 모든 네트워크 드라이브 자동 탐지.
* 시스템/민감/개발 캐시 경로 기본 제외.
* UI에서 포함/제외 경로 수정 가능.

### 구현 주의

제외 경로는 결과 필터링이 아니라 `os.walk()` 진입 전 `dirs\[:]` 수정으로 차단한다.

## 11\. Phase 8 — 설치 프로그램

### 목표

소스 폴더 없이 설치 가능한 사내 배포 패키지를 만든다.

### 권장 방식

```text
PyInstaller + Inno Setup
```

### 새 디렉토리 후보

```text
packaging/
├─ osl\_rag.spec
├─ installer.iss
├─ build.ps1
└─ README.md
```

### installer 기능

* OSL RAG 앱 설치.
* LibreOffice 포함 설치 또는 검증.
* `%APPDATA%\\OSL RAG` 생성.
* `%LOCALAPPDATA%\\OSL RAG` 생성.
* 시작 메뉴 바로가기 생성.
* 시작프로그램 등록 옵션.
* 최초 실행 시 Google OAuth 로그인 유도.
* uninstall 시 데이터 보존/삭제 선택.

### 검증

* clean Windows VM에서 설치.
* LibreOffice 없는 환경에서 설치/변환 성공.
* Google OAuth 로그인 성공.
* localhost UI 접속 성공.
* TurboVec index 생성 성공.
* uninstall 후 데이터 처리 옵션 확인.

## 12\. Phase 9 — Rollout

### Alpha

* slim 제거.
* localhost-only.
* Streamlit 유지.
* local helper API 기반 파일/폴더 열기.
* LibreOffice bundled install.
* Google OAuth login skeleton.
* TurboVec local rebuild.

### Beta

* Google provider 실제 호출 연동.
* config UI.
* 검색 결과 파일 액션 UX 개선.
* 드라이브 자동 탐지/제외 설정.
* installer 배포.

### Internal Release

* OAuth 안정화.
* LibreOffice 변환 안정화.
* 파일/폴더 열기 보안 정책 안정화.
* 사용자별 설정/로그/인덱스 저장소 분리.
* 사내 배포 문서 작성.

## 13\. 주요 리스크

|영역|리스크|대응|
|-|-|-|
|Google OAuth|Gemini 호출에 필요한 실제 API/권한 구조 복잡|Vertex AI 기반 검토|
|LibreOffice|설치 용량 증가|사내 공유 배포, silent install|
|Token 저장|refresh token 유출 위험|Windows Credential Manager/DPAPI|
|전체 드라이브 스캔|성능/민감정보 리스크|기본 제외 목록, 크기 제한, UI 설정|
|Streamlit packaging|PyInstaller 난이도|v1은 유지, 빌드 검증 후 필요 시 UI 교체|
|파일/폴더 열기 UX|브라우저 보안 정책으로 직접 열기 불안정|localhost helper API 또는 native UI|

## 14\. 다음 프로젝트 시작 시 첫 작업

1. Google OAuth 방식 확정: Vertex AI 기반인지 별도 Gemini OAuth 가능성 검증인지 결정.
2. LibreOffice installer 포함 방식 결정: silent installer vs portable bundle.
3. `config\_manager.py`와 `token\_store.py` 설계.
4. local helper API 기반 파일/폴더 열기 UX 설계.
5. slim 제거 및 localhost-only 실행부터 구현.
6. 하드코딩 secret 제거 및 key 폐기.
