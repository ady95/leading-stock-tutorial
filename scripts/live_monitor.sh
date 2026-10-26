#!/bin/sh
# 12-5 평일 장중 모니터 (cron 예: 1 9 * * 1-5 $HOME/leading-stock-tutorial/scripts/live_monitor.sh)
# 휴장일이면 스스로 끝난다. 같은 앱키를 다른 프로그램도 쓰면 KIS_RATE_PER_SEC를 낮춘다 (부록 A)
cd "$HOME/leading-stock-tutorial" || exit 1
export PYTHONIOENCODING=utf-8
mkdir -p outputs
exec >> outputs/live_monitor.log 2>&1
echo "=== $(date "+%F %T") 시작"
KIS_RATE_PER_SEC=${KIS_RATE_PER_SEC:-5} flock -n /tmp/leading-stock-live.lock \
  .venv/bin/python -m ch07_realtime.live --interval 300 --until 152000
echo "=== $(date "+%F %T") 종료 (exit $?)"
