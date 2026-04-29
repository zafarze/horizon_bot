@echo off
REM Установка School-bot как Windows-сервис через NSSM.
REM Требования:
REM   1) Скачать NSSM: https://nssm.cc/download и положить nssm.exe в PATH
REM   2) Создать виртуальное окружение в .venv и поставить requirements.txt
REM
REM Запускать от Администратора.

set ROOT=%~dp0..
set PYTHON=%ROOT%\.venv\Scripts\python.exe
set MAIN=%ROOT%\main.py
set LOGS=%ROOT%\logs

if not exist "%PYTHON%" (
    echo [ERROR] Не найден %PYTHON%. Создайте venv:
    echo   python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

nssm install SchoolBot "%PYTHON%" "%MAIN%"
nssm set SchoolBot AppDirectory "%ROOT%"
nssm set SchoolBot Start SERVICE_AUTO_START
nssm set SchoolBot AppStdout "%LOGS%\service_stdout.log"
nssm set SchoolBot AppStderr "%LOGS%\service_stderr.log"
nssm set SchoolBot AppRotateFiles 1
nssm set SchoolBot AppRotateBytes 10485760
nssm set SchoolBot AppExit Default Restart
nssm set SchoolBot AppRestartDelay 5000

REM Watchdog как отдельный сервис
nssm install SchoolBotWatchdog "%PYTHON%" "%ROOT%\service\watchdog.py"
nssm set SchoolBotWatchdog AppDirectory "%ROOT%"
nssm set SchoolBotWatchdog Start SERVICE_AUTO_START

REM Запретить системе уходить в сон, пока сервис активен
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

nssm start SchoolBot
nssm start SchoolBotWatchdog

echo.
echo [OK] Сервисы установлены. Управление:
echo   nssm status SchoolBot
echo   nssm restart SchoolBot
echo   nssm remove  SchoolBot confirm
