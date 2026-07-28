#!/bin/zsh
# 같이투자 일일 갱신: 신규 관보/국회공보 → 시세 → 뉴스·공시 → 페이지 재생성 → 배포
set -e
cd /Users/han/gongjikja-project
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
LOG=update.log
echo "===== $(date '+%F %T') 갱신 시작 =====" >> $LOG
python3 gwanbo_crawler.py index          >> $LOG 2>&1
python3 gwanbo_crawler.py download --from=2020 --to=2026 >> $LOG 2>&1
python3 gwanbo_crawler.py assembly       >> $LOG 2>&1
python3 gwanbo_crawler.py price          >> $LOG 2>&1 || true
python3 gwanbo_crawler.py news           >> $LOG 2>&1 || true
python3 gwanbo_crawler.py parse          >> $LOG 2>&1
if ! git diff --quiet -- index.html stocks.html people.html *.csv news.json prices.json; then
  git add -A
  git commit -m "자동 갱신: $(date '+%F')" >> $LOG 2>&1
  git push >> $LOG 2>&1
  echo "→ 배포됨" >> $LOG
else
  echo "→ 변경 없음(배포 생략)" >> $LOG
fi
