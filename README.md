# DAILY BRIEF CHECK (Market-Brief-Korea)

아침(전일 미국 증시)·16:10(국내 마감) 시황을 자동 수집해 카톡으로 공유하는 사이트.

## 구성
- index.html : 화면 + 공유 버튼 3개 (1 시황만 / 2 시황+초안 의견 / 3 의견 직접 작성)
- scripts/collect.py : 지수·섹터(yfinance), 뉴스(RSS) 수집, 핵심 뉴스 3건 선정·검증, 의견 초안
- .github/workflows/brief.yml : 매일 06:00~23:00 KST 1시간 간격(매시 정각) 자동 실행 (GitHub 예약은 지연될 수 있음)
- data/*.json : 현재는 샘플 데이터 (첫 실행 후 실제 데이터로 교체)

## 선택: AI 요약 (무료)
Settings > Secrets > Actions 에 GEMINI_API_KEY 등록 시
영문 뉴스 제목 한국어 요약 + 의견 문장 다듬기 사용. 없으면 규칙 기반 초안으로 동작.
