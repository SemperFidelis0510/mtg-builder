@echo off
setlocal enabledelayedexpansion

REM Single entrypoint to run all tests (pytest + Playwright).
REM Usage:
REM   test.bat
REM   test.bat -q
REM   test.bat --headed
REM Note: extra args are forwarded to pytest only.

pushd "%~dp0"

set "PYTHONPATH=%CD%"
if "%MTG_LOG_LEVEL%"=="" set "MTG_LOG_LEVEL=WARNING"
if "%MTG_DISABLE_RAG_STARTUP%"=="" set "MTG_DISABLE_RAG_STARTUP=1"
if "%KMP_DUPLICATE_LIB_OK%"=="" set "KMP_DUPLICATE_LIB_OK=TRUE"
set "PYTHONDONTWRITEBYTECODE=1"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

REM Ensure Python test dependencies exist in the currently selected interpreter.
%PY% -c "import pytest" >nul 2>&1
if errorlevel 1 (
  echo pytest not found in this Python environment; installing requirements...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Failed to install Python requirements.
    popd
    exit /b 1
  )
)

echo [1/2] Running pytest...
%PY% -m pytest %*
if errorlevel 1 (
  echo.
  echo Pytest failed.
  popd
  exit /b 1
)

if not exist "node_modules" (
  echo node_modules not found; running npm install...
  call npm install
  if errorlevel 1 (
    popd
    exit /b 1
  )
)

echo.
echo [2/2] Running Playwright E2E...
call npx playwright test
if errorlevel 1 (
  echo.
  echo Playwright tests failed.
  popd
  exit /b 1
)

echo.
echo All tests passed.
popd
exit /b 0

