@echo off
REM Starts PriceWise on this computer only. Opens in your browser.
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo PriceWise isn't set up yet. Double-click setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pricewise.doctor > "%TEMP%\pricewise-doctor.txt"
if errorlevel 1 (type "%TEMP%\pricewise-doctor.txt" & echo. & echo Fix the item marked FAIL above, then run this again. & pause & exit /b 1)
streamlit run app.py --server.address localhost --browser.serverAddress localhost --server.headless false
