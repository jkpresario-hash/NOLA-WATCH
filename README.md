# NOLA WATCH

뉴올리언스 펠리컨스 뉴스와 Reddit을 한국어로 읽는 GitHub Pages 피드.

## 기능
- Google News RSS와 r/NOLAPelicans RSS 수집, 중복·중계 스팸 제거
- 한국어 제목, 기사 본문의 주요 문장 3개를 발췌한 한국어 요약
- Reddit 텍스트 본문과 상위 댓글 최대 10개 번역, 댓글별 원문 링크
- 원문 OG 이미지·Reddit 미리보기 썸네일 (없는 이미지나 로딩 실패 이미지 숨김)
- 뉴스/Reddit 필터, 한·영 검색, 모바일 레이아웃, 20개씩 더 보기
- 한국시간 09:00 / 18:00 GitHub Actions 수집 및 Pages 배포

## 실행
Python 3.12 환경에서:
```
pip install -r requirements.txt
python -m unittest discover -s tests
python collect.py --limit 40
python -m http.server 8000
```
브라우저에서 http://localhost:8000 을 엽니다. `collect.py`는 data.json만 갱신하므로 화면 파일을 덮어쓰지 않습니다.

## 운영상 제한
번역에는 API 키 없이 접근 가능한 Google Translate 웹 엔드포인트를 사용합니다. 정식 SLA가 있는 API가 아니며 이용량 제한이나 서비스 변경으로 실패할 수 있습니다. 실패는 원문 링크와 함께 표시되며 다음 수집에서 재시도합니다. 상용 규모 운영에는 정식 번역 API로 교체하는 것을 권장합니다.

기사 요약은 생성형 요약이 아닌 주요 문장 발췌 번역입니다. 유료벽·차단·본문 추출 실패 시 제목만 번역될 수 있습니다. Google News 원문 주소 해석도 외부 서비스 동작에 의존합니다. Reddit JSON API 실패 시 공개 댓글 RSS로 대체 수집합니다. 두 경로 모두 실행 환경 IP에 따라 403/429를 반환할 수 있으며 이 경우 본문·댓글 번역이 제공되지 않습니다. RSS는 추천 수를 제공하지 않으며 피드에 포함된 댓글 최대 10개를 표시합니다. 전체 댓글이 아니라 API가 반환하는 상위 댓글 최대 10개입니다.

실행당 40개를 처리하고 기존 성공 번역과 번역 캐시를 재사용합니다. Reddit 댓글은 6시간 이상 경과하면 다시 수집합니다. data.json에는 최근 200개까지 유지하며 수집 실패 시 기존 데이터를 유지합니다. 실행 일정은 GitHub에 의해 지연될 수 있습니다.

GitHub Pages 설정은 기존처럼 Source: GitHub Actions를 유지합니다. 변경 커밋 후 Actions → Update NOLA WATCH → Run workflow를 실행하거나 정기 실행을 기다립니다. 번역 캐시는 GitHub Actions cache에 저장되며 공개 사이트 파일에 포함되지 않습니다.
