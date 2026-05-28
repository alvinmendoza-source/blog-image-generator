@echo off
cd /d "%~dp0"
echo Starting Blog Image Generator...
py -X utf8 -m streamlit run app.py --server.headless false --browser.gatherUsageStats false
pause
