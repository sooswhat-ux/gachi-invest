#!/bin/zsh
# 리나의 주간 칼럼 자동 발행: Claude가 최신 데이터를 읽고 칼럼 작성 → 재생성 → 배포
set -e
cd /Users/han/gongjikja-project
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
claude -p "너는 같이투자(with-invest.com)의 AI 리서처 '리나'다. columns.json을 읽어 기존 칼럼들을 확인하고, index.html의 리드·갈아타기 문구와 holdings CSV, news.json에서 이번 주 데이터를 읽은 뒤, 새 주간 칼럼 1편(한국어, 4~6문단, 데이터 해설 톤, 종목 추천 금지, 검증된 수치만 인용)을 작성해 columns.json 배열 맨 앞에 추가하라. 형식: {d: 오늘날짜, t: 제목, b: HTML 문단}. 그 후 python3 gwanbo_crawler.py parse 를 실행하고, git add -A && git commit -m '주간 칼럼' && git push 로 배포하라." \
 --allowedTools "Read,Write,Edit,Bash" >> column.log 2>&1
