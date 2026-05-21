@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  준공서류 검토 포털 - Flask 실행
echo ============================================
echo.
echo [1/2] 필요한 패키지 설치 (처음 1회만 시간 걸림)...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo *** pip install 실패. Python 이 설치되어 있는지 확인하세요. ***
    pause
    exit /b 1
)

echo.
echo [2/2] Flask 서버 시작
echo.
echo  → 브라우저에서 다음 주소로 접속하세요:
echo    http://127.0.0.1:5000
echo.
echo  → 종료하려면 이 창에서 Ctrl+C 또는 창 닫기
echo ============================================
echo.

python app.py

echo.
echo *** 서버가 종료되었습니다. ***
pause
