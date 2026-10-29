#!/bin/bash
# ERA5 2015~2025 받기 → 특성 계산 → NCEP과 성능 비교를 한 번에. 끊겨도 다시 실행하면 이어서 한다.
#   맥:     nohup caffeinate -i ./scripts/era5_pipeline.sh > /dev/null 2>&1 &
#   그 밖:  nohup ./scripts/era5_pipeline.sh > /dev/null 2>&1 &
# 진행 기록은 data/env/era5_pipeline.log
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
LOG=data/env/era5_pipeline.log
mkdir -p data/env
echo "시작 $(date)" > "$LOG"
rc=1
for i in 1 2; do
  perl -e 'alarm 21600; exec @ARGV' $PY -u -W ignore -m typhoon.sources.era5 --start 2015 --end 2025 --workers 3 >> "$LOG" 2>&1
  rc=$?
  echo "받기 시도 $i 종료 코드 $rc  $(date)" >> "$LOG"
  [ $rc -eq 0 ] && break
done
if [ $rc -eq 0 ]; then
  $PY -W ignore -m typhoon.env_era5 --no-download >> "$LOG" 2>&1
  $PY -W ignore -m typhoon.era5_compare >> "$LOG" 2>&1
  echo "전체 완료 $(date)" >> "$LOG"
else
  echo "받기 미완료로 중단 $(date)  → 같은 명령을 다시 실행하면 이어서 한다" >> "$LOG"
fi
