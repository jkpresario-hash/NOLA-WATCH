# NOLA WATCH — 새로 구성한 시작 프로젝트

이 파일은 이전 대화의 ZIP 원본을 복원한 것이 아닙니다. 원본 파일에 접근할 수 없어 요청 내용을 바탕으로 새로 만들었습니다.

## 포함 기능과 범위
- Google News RSS의 Pelicans 검색 결과와 NOLAPelicans Reddit RSS를 수집합니다.
- 제목, 출처와 원문 링크만 표시하며 기사 본문은 복제하지 않습니다.
- Python 표준 라이브러리만 사용합니다. 외부 패키지 설치가 필요 없습니다.
- 한국시간 09:00 / 18:00에 GitHub Actions 실행을 예약합니다. 예약 실행은 지연되거나 비활성화될 수 있습니다.
- 피드 오류를 화면과 data.json에 기록하고 기존 항목은 유지합니다.
- X 자동 수집, 유료 기사 접근, ESPN/The Athletic/Yahoo/CBS/HoopsHype/RealGM 각각의 직접 수집은 구현하지 않았습니다. Google News에 노출되는 기사가 포함될 수 있으나 모든 매체를 보장하지 않습니다.
- 최초에는 빈 시작 화면입니다. 실제 수집은 GitHub Actions에서 실행하세요.

## GitHub 웹으로 설정
1. https://github.com/new 에서 이름 nola-watch, Public, Add README를 선택해 저장소를 만듭니다.
2. ZIP을 풀었다면 내부 파일을 저장소 루트에 추가합니다. ZIP 자체를 업로드하는 것은 설치가 아닙니다.
3. 숨김 폴더 .github 업로드가 어려우면 Add file → Create new file에서 .github/workflows/update.yml 경로를 입력하고 내용을 붙여 넣습니다.
4. Settings → Pages → Build and deployment → Source에서 GitHub Actions를 선택합니다.
5. Actions → Update NOLA WATCH → Run workflow를 실행합니다.
6. Pages 설정에 표시되는 주소로 접속합니다. 처음 게시되기까지 시간이 걸릴 수 있습니다.

## 다운로드 없이 설정
함께 제공한 GITHUB-WEB-SETUP.md를 Codex에서 열고 각 코드 블록을 복사합니다.
저장소의 Add file → Create new file에서 아래 세 파일을 각각 만들고 Commit changes를 누릅니다.
- collect.py
- .github/workflows/update.yml
- index.html
이후 위의 4–6 단계를 진행합니다. data.json은 첫 실행 때 생성됩니다.

## 확인 범위
압축 파일 전체를 다시 열고 각 항목의 CRC 및 압축 해제 결과를 비교했습니다.
수집기 문법과 RSS/Atom 파싱 및 비HTTP URL 거부를 로컬 샘플로 확인했습니다.
실제 외부 피드 접속과 GitHub 배포는 이 환경에서 검증하지 않았습니다.
