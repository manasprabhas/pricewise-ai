@echo off
REM Starts PriceWise so your phone can open it over your HOME Wi-Fi.
REM Anyone on the same Wi-Fi could open it too, so don't use this on public Wi-Fi.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo.
echo On your phone (same Wi-Fi), open:  http://YOUR-LAPTOP-IP:8501
echo Your laptop's IP address is the "IPv4 Address" below:
ipconfig | findstr /i "IPv4"
echo.
echo If Windows asks about the firewall, allow access on PRIVATE networks only.
streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless false
