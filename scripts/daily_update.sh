#!/bin/sh
# 12-5 평일 장 마감 후 수집과 장 마감 리포트 (cron 예: 30 18 * * 1-5 $HOME/leading-stock-tutorial/scripts/daily_update.sh)
# 앞 단계가 실패하면 뒤 단계는 건너뛴다. flock은 같은 작업이 겹쳐 실행되지 않게 막는다
cd "$HOME/leading-stock-tutorial" || exit 1
export PYTHONIOENCODING=utf-8
mkdir -p outputs
exec >> outputs/daily_update.log 2>&1
echo "=== $(date "+%F %T") 시작"
flock -n /tmp/leading-stock-daily.lock sh -c "
  .venv/bin/python -m ch03_pipeline.master &&
  .venv/bin/python -m ch03_pipeline.daily --update &&
  .venv/bin/python -m ch03_pipeline.minutes --top 300 --days 3 &&
  .venv/bin/python -m ch04_candidates.benchmark &&
  .venv/bin/python -m ch05_themes.themes &&
  .venv/bin/python -m ch09_llm.news --top 300 --days 3 &&
  .venv/bin/python -m ch12_monitor.report --date $(date +%F)
"
echo "=== $(date "+%F %T") 종료 (exit $?)"
