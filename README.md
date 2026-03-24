# 공개 투표판

로그인 없이 닉네임만 입력해서 투표하고, 진행 중인 결과와 댓글을 공개할 수 있는 Flask 프로젝트입니다.

## 핵심 기능

- 로그인 없이 닉네임만으로 투표
- 같은 닉네임으로 **같은 항목은 1번만**, 다른 항목은 여러 개 투표 가능
- 진행 중인 투표 링크 공유 가능
- 실시간 결과 공개
- 댓글/코멘트 기능
- 종료된 투표 결과를 Discord 웹훅으로 전송
- GitHub Actions로 5분마다 종료 체크

## 프로젝트 구조

```text
poll_app/
├─ app.py
├─ schema.sql
├─ requirements.txt
├─ render.yaml
├─ .env.example
├─ README.md
├─ static/
│  └─ style.css
├─ templates/
│  ├─ base.html
│  ├─ index.html
│  ├─ create_poll.html
│  └─ poll_detail.html
└─ .github/
   └─ workflows/
      └─ finalize-polls.yml
```

## 로컬 실행

### 1) 가상환경 생성

```bash
python -m venv .venv
```

### 2) 가상환경 활성화

Windows:

```bash
.venv\Scripts\activate
```

macOS / Linux:

```bash
source .venv/bin/activate
```

### 3) 패키지 설치

```bash
pip install -r requirements.txt
```

### 4) 환경변수 설정

`.env.example` 내용을 참고해서 환경변수를 설정하세요.

Windows PowerShell 예시:

```powershell
$env:SECRET_KEY="local-secret"
$env:FINALIZE_TOKEN="local-finalize-token"
$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxxx/yyyy"
$env:ADMIN_NICKNAME="대재"
```

### 5) 실행

```bash
python app.py
```

브라우저에서 아래 주소로 접속:

```text
http://localhost:5000
```

## 무료 배포(Render)

### 1) GitHub에 업로드

이 프로젝트를 GitHub 저장소로 올립니다.

### 2) Render 가입 후 Web Service 생성

- New → Web Service
- GitHub 저장소 연결
- Build Command:

```text
pip install -r requirements.txt
```

- Start Command:

```text
gunicorn app:app
```

### 3) 환경변수 설정

Render의 Environment에서 아래 값을 설정합니다.

- `SECRET_KEY`
- `FINALIZE_TOKEN`
- `DISCORD_WEBHOOK_URL`
- `ADMIN_NICKNAME`

### 4) 배포 완료 후 URL 확인

예시:

```text
https://public-poll-board.onrender.com
```

## Discord 결과 전송 설정

### 1) 디스코드 서버에서 비공개 채널 생성

결과를 너만 보고 싶다면, 너만 볼 수 있는 비공개 채널을 하나 만듭니다.

### 2) 웹훅 생성

채널 설정 → 연동 → 웹훅 → 새 웹훅 만들기

생성된 URL을 `DISCORD_WEBHOOK_URL`에 넣으면 됩니다.

## GitHub Actions로 자동 종료 체크

무료 호스팅은 항상 켜져 있지 않을 수 있으므로, GitHub Actions가 5분마다 종료된 투표를 확인하도록 구성했습니다.

### GitHub Secrets 설정

저장소 Settings → Secrets and variables → Actions → New repository secret

이름:

```text
FINALIZE_URL
```

값 예시:

```text
https://public-poll-board.onrender.com/internal/finalize?token=네_FINALIZE_TOKEN
```

이후 GitHub Actions가 5분마다 이 URL을 호출해서 종료된 투표를 Discord로 보냅니다.

## 규칙 요약

- 같은 닉네임으로 같은 항목에는 한 번만 투표 가능
- 같은 닉네임으로 다른 항목에는 여러 번 투표 가능
- 댓글은 투표와 별도로 여러 번 등록 가능
- 닉네임 인증이 없으므로, 같은 이름으로 장난칠 수는 있음

## 추천 운영 방식

- 디스코드에 투표 링크를 공유
- 댓글에 부캐/상세 사유 적기
- 결과는 비공개 채널 웹훅으로 받기

## 주의

이 프로젝트는 SQLite를 사용합니다. 개인용/소규모 용도로는 충분하지만, 서버 재배포나 인스턴스 재시작 시 데이터 보존이 중요하다면 Postgres로 바꾸는 것이 더 안전합니다.


## 추가 변경
- 시작 전 투표는 목록에서 클릭 시 팝업 안내
- 투표 생성 시 Discord 웹훅 알림 전송
- 관리자 닉네임 + 여러 관리자 코드로 수동 종료/삭제
- 앱 재시작 시 기존 투표 유지 (테이블 DROP 제거)
- 투표 코멘트 입력 제거
