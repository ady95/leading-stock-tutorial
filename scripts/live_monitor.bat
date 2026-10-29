@echo off
rem Ch.12-5 live monitor for Windows. Register in Task Scheduler: weekdays 09:01.
rem Exits by itself on market holidays. Call rate follows KIS_RATE_PER_SEC in .env (default: real 15/s, paper 1/s).
rem Set it lower in .env if another program shares the app key (Appendix A).
cd /d "%~dp0.." || exit /b 1
set PYTHONIOENCODING=utf-8
if not exist outputs mkdir outputs
echo === %date% %time% start>> outputs\live_monitor.log
.venv\Scripts\python.exe -m ch07_realtime.live --interval 300 --until 152000 >> outputs\live_monitor.log 2>&1
echo === %date% %time% end (exit %errorlevel%)>> outputs\live_monitor.log
