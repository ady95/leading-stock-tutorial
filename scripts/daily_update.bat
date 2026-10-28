@echo off
rem Ch.12-5 after-close collection and closing report for Windows. Register in Task Scheduler: weekdays 18:30.
rem If a step fails, the following steps are skipped.
cd /d "%~dp0.." || exit /b 1
set PYTHONIOENCODING=utf-8
set PY=.venv\Scripts\python.exe
if not exist outputs mkdir outputs
echo === %date% %time% start>> outputs\daily_update.log
(%PY% -m ch03_pipeline.master && %PY% -m ch03_pipeline.daily --update && %PY% -m ch03_pipeline.minutes --top 300 --days 3 && %PY% -m ch04_candidates.benchmark && %PY% -m ch05_themes.themes && %PY% -m ch09_llm.news --top 300 --days 3 && %PY% -m ch12_monitor.report) >> outputs\daily_update.log 2>&1
echo === %date% %time% end (exit %errorlevel%)>> outputs\daily_update.log
