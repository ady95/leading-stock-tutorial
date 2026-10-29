#!/bin/sh
# 12-5 평일 장중 모니터 (cron 예: 1 9 * * 1-5 $HOME/leading-stock-tutorial/scripts/live_monitor.sh)
# 휴장일이면 스스로 끝난다. 호출 속도는 .env의 KIS_RATE_PER_SEC를 따른다 (없으면 실전 초당 15건, 모의투자 1건)
# 같은 앱키를 다른 프로그램도 쓰면 .env에서 낮춘다 (부록 A)
cd "$HOME/leading-stock-tutorial" || exit 1
export PYTHONIOENCODING=utf-8
mkdir -p outputs
exec >> outputs/live_monitor.log 2>&1
echo "=== $(date "+%F %T") 시작"
flock -n /tmp/leading-stock-live.lock \
  .venv/bin/python -m ch07_realtime.live --interval 300 --until 152000
echo "=== $(date "+%F %T") 종료 (exit $?)"
