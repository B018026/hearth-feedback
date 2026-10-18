@echo off
chcp 65001 > nul
cd /d "%~dp0"
python hsreview.py sync
python hsreview.py stats
echo.
echo 取り込みが終わりました。Claude Code で、このフォルダを開いて「直近の試合を振り返って」と頼んでください。
pause
