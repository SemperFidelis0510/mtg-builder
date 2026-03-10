@echo off
setlocal EnableDelayedExpansion
set "CONDA_ENV=mtg-rag"
cd /d "%~dp0"

REM --- Parse arguments ---
REM   Usage: install.bat [install|download|build] [--force] [--cuda 11|12|cpu]
REM   Default (no stage): run all three stages in order.

set "STAGE="
set "FORCE="
set "CUDA=12"

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="install"  ( set "STAGE=install"  & shift & goto parse_args )
if /i "%~1"=="download" ( set "STAGE=download" & shift & goto parse_args )
if /i "%~1"=="build"    ( set "STAGE=build"    & shift & goto parse_args )
if /i "%~1"=="--force"  ( set "FORCE=1"        & shift & goto parse_args )
if /i "%~1"=="--cuda"   (
    if "%~2"=="" (
        echo ERROR: --cuda requires a value: 11, 12, or cpu
        exit /b 1
    )
    set "CUDA=%~2"
    shift & shift & goto parse_args
)
echo ERROR: Unknown argument: %~1
goto usage
:args_done

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

REM --- Run stage(s) ---
if "%STAGE%"=="" (
    call :do_install
    if errorlevel 1 exit /b 1
    call :do_download
    if errorlevel 1 exit /b 1
    call :do_build
    if errorlevel 1 exit /b 1
    goto end
)
if /i "%STAGE%"=="install"  ( call :do_install  & if errorlevel 1 exit /b 1 & goto end )
if /i "%STAGE%"=="download" ( call :do_download & if errorlevel 1 exit /b 1 & goto end )
if /i "%STAGE%"=="build"    ( call :do_build    & if errorlevel 1 exit /b 1 & goto end )

:do_install
echo.
echo === [install] Setting up conda environment "%CONDA_ENV%" ===
call "%_ACTIVATE%" base

REM Check if the env already exists
conda env list | findstr /b /c:"%CONDA_ENV% " >nul 2>&1
if !errorlevel!==0 (
    if "%FORCE%"=="1" (
        echo --force: removing existing environment "%CONDA_ENV%"...
        call conda env remove -n %CONDA_ENV% -y
        if errorlevel 1 (
            echo ERROR: Failed to remove existing environment.
            exit /b 1
        )
    ) else (
        echo Conda env "%CONDA_ENV%" already exists. Skipping creation. Use --force to recreate.
    )
) else (
    echo Creating conda environment "%CONDA_ENV%" with Python 3.11...
    call conda create -n %CONDA_ENV% python=3.11 -y
    if errorlevel 1 (
        echo ERROR: Failed to create conda environment.
        exit /b 1
    )
)

call conda activate %CONDA_ENV%
echo === Installing Python dependencies (CUDA=%CUDA%) ===
python -m src.lib.setup --install --cuda %CUDA%
if errorlevel 1 (
    echo ERROR: Dependency installation failed.
    exit /b 1
)
echo === [install] Done ===
exit /b 0

:do_download
echo.
echo === [download] Downloading AtomicCards.json ===
call "%_ACTIVATE%" %CONDA_ENV%
if "%FORCE%"=="1" (
    python -m src.lib.setup --download --force
) else (
    python -m src.lib.setup --download
)
if errorlevel 1 (
    echo ERROR: Download failed.
    exit /b 1
)
echo === [download] Done ===
exit /b 0

:do_build
echo.
echo === [build] Building ChromaDB vector index ===
call "%_ACTIVATE%" %CONDA_ENV%
python -m src.lib.build_rag --build
if errorlevel 1 (
    echo ERROR: Build failed.
    exit /b 1
)
echo === [build] Done ===
exit /b 0

:usage
echo.
echo Usage: .\%~nx0 [install^|download^|build] [--force] [--cuda 11^|12^|cpu]
echo.
echo   (no stage)           Run all stages: install, download, build
echo   install              Create conda env and install Python deps
echo   download             Download AtomicCards.json
echo   build                Ingest data and build ChromaDB vector index
echo.
echo   --force              Skip-checks override:
echo                          install  : remove and recreate the conda env
echo                          download : re-download even if file exists
echo   --cuda 11^|12^|cpu    PyTorch variant for install stage (default: 12)
echo                          11  = CUDA 11.8 (PyTorch 2.1.2)
echo                          12  = CUDA 12.8 (latest PyTorch, default)
echo                          cpu = CPU-only (no GPU)
echo.
exit /b 1

:end
endlocal
