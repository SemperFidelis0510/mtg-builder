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
if /i "%~1"=="install"      goto install
if /i "%~1"=="download"     goto download
if /i "%~1"=="build"       goto build
if /i "%~1"=="serve"       goto serve
if /i "%~1"=="deck-editor" goto deck_editor
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
if "%~2"=="" (
    python -m src.lib.build_rag --install
) else (
    python -m src.lib.build_rag --install --cuda %~2
)
if errorlevel 1 (
    echo Install failed.
    exit /b 1
)
echo === Install complete ===
goto end

:download
call "%_ACTIVATE%" %CONDA_ENV%
if /i "%~2"=="force" (
    python -m src.lib.build_rag --download --force
) else (
    python -m src.lib.build_rag --download
)
if errorlevel 1 (
    echo Download failed.
    exit /b 1
)
goto end

:build
call "%_ACTIVATE%" %CONDA_ENV%
python -m src.lib.build_rag --build
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

:deck_editor
call "%_ACTIVATE%" %CONDA_ENV%
python deck_editor.py
if errorlevel 1 (
    echo Deck editor exited with error.
    exit /b 1
)
goto end

:usage
echo Usage: .\%~nx0 install [11^|12^|cpu] ^| download [force] ^| build ^| serve ^| deck-editor
echo.
echo   install [11^|12^|cpu] - Create conda env and install deps (default: CUDA 12)
echo                          11  = CUDA 11.8 (PyTorch 2.1.2)
echo                          12  = CUDA 12.8 (latest PyTorch, default)
echo                          cpu = CPU-only (latest PyTorch, no GPU)
echo   download [force]     - Download AtomicCards.json
echo   build                - Ingest data and build ChromaDB vector index
echo   serve                - Start the MCP server (stdio)
echo   deck-editor          - Start the deck editor web server (http://127.0.0.1:8000)

:end
