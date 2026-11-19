@echo off
REM One-time setup: creates a private Python environment and installs the free packages.
cd /d "%~dp0"
python --version || (echo Python not found. Install Python 3.12 from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)
python -m venv .venv || (echo Could not create the .venv folder. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo Installing packages failed. Check your internet connection and run setup.bat again. & pause & exit /b 1)
echo.
echo Checking the installation:
python -m pricewise.doctor
if errorlevel 1 (pause & exit /b 1)
echo.
echo Setup finished. Double-click run.bat to start PriceWise.
pause
