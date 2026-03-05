@echo off
set "CONDA_ENV=mtg-rag"
cd /d "%~dp0"

REM --- Locate conda's activate.bat ---
set "_ACTIVATE="
if defined CONDA_EXE (
    for %%F in ("%CONDA_EXE%") do (
        if exist "%%~dpFactivate.bat" (
            set "_ACTIVATE=%%~dpFactivate.bat"
            goto found_conda
        )
    )
)
for %%P in (
    "%LOCALAPPDATA%\anaconda3"
    "%LOCALAPPDATA%\miniconda3"
    "%USERPROFILE%\anaconda3"
    "%USERPROFILE%\miniconda3"
    "%ProgramData%\anaconda3"
    "%ProgramData%\miniconda3"
) do (
    if exist "%%~P\Scripts\activate.bat" (
        set "_ACTIVATE=%%~P\Scripts\activate.bat"
        goto found_conda
    )
)
echo ERROR: Could not find a conda installation.
echo Checked CONDA_EXE and common install locations.
exit /b 1
:found_conda

if "%~1"=="" goto usage
if /i "%~1"=="install"  goto install
if /i "%~1"=="download" goto download
if /i "%~1"=="build"    goto build
if /i "%~1"=="serve"    goto serve
goto usage

:install
echo === Creating conda environment "%CONDA_ENV%" ===
call "%_ACTIVATE%" base
call conda create -n %CONDA_ENV% python=3.11 -y
if errorlevel 1 (
    echo Env may already exist, continuing...
)
call conda activate %CONDA_ENV%
echo === Installing dependencies ===
python build_rag.py --install
if errorlevel 1 (
    echo Install failed.
    exit /b 1
)
echo === Install complete ===
goto end

:download
call "%_ACTIVATE%" %CONDA_ENV%
if /i "%~2"=="force" (
    python build_rag.py --download --force
) else (
    python build_rag.py --download
)
if errorlevel 1 (
    echo Download failed.
    exit /b 1
)
goto end

:build
call "%_ACTIVATE%" %CONDA_ENV%
python build_rag.py --build
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)
goto end

:serve
call "%_ACTIVATE%" %CONDA_ENV%
python server.py
if errorlevel 1 (
    echo Server exited with error.
    exit /b 1
)
goto end

:usage
echo Usage: .\%~nx0 install ^| download [force] ^| build ^| serve
echo.
echo   install   - Create conda env "%CONDA_ENV%" and install all dependencies
echo   download  - Download AtomicCards.json (add "force" to re-download)
echo   build     - Ingest data and build ChromaDB vector index
echo   serve     - Start the MCP server (stdio)

:end
