@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  start "" "http://127.0.0.1:8765"
  py -3 app.py
) else (
  where python >nul 2>&1
  if %ERRORLEVEL% EQU 0 (
    start "" "http://127.0.0.1:8765"
    python app.py
  ) else (
    echo Python nao encontrado. Instale Python 3.10 ou superior.
  )
)
pause
