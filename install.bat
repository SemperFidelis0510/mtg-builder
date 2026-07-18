@echo off
setlocal EnableDelayedExpansion
set "CONDA_ENV=mtg-rag"
cd /d "%~dp0"

REM --- Parse arguments ---
REM   Usage: install.bat [install|download|build|prices|update|uninstall|reinstall] [--force]
REM   Default (no stage): run all three stages in order.

set "STAGE="
set "FORCE="

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="install"    ( set "STAGE=install"    & shift & goto parse_args )
if /i "%~1"=="download"   ( set "STAGE=download"   & shift & goto parse_args )
if /i "%~1"=="build"      ( set "STAGE=build"      & shift & goto parse_args )
if /i "%~1"=="prices"     ( set "STAGE=prices"     & shift & goto parse_args )
if /i "%~1"=="update"     ( set "STAGE=update"     & shift & goto parse_args )
if /i "%~1"=="uninstall"  ( set "STAGE=uninstall"  & shift & goto parse_args )
if /i "%~1"=="reinstall"  ( set "STAGE=reinstall"  & shift & goto parse_args )
if /i "%~1"=="--force"  ( set "FORCE=1"        & shift & goto parse_args )
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
if /i "%STAGE%"=="install"    ( call :do_install    & if errorlevel 1 exit /b 1 & goto end )
if /i "%STAGE%"=="download"   ( call :do_download   & if errorlevel 1 exit /b 1 & goto end )
if /i "%STAGE%"=="build"      ( call :do_build      & if errorlevel 1 exit /b 1 & goto end )
if /i "%STAGE%"=="prices"     ( call :do_prices     & if errorlevel 1 exit /b 1 & goto end )
if /i "%STAGE%"=="update"     ( call :do_update     & if errorlevel 1 exit /b 1 & goto end )
if /i "%STAGE%"=="uninstall"  ( call :do_uninstall  & if errorlevel 1 exit /b 1 & goto end )
if /i "%STAGE%"=="reinstall"  ( call :do_reinstall  & if errorlevel 1 exit /b 1 & goto end )

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
echo === Installing Python dependencies ===
python -m src.lib.setup --install
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
echo === [build] Building GraphRAG graph, reports, and LanceDB index ===
call "%_ACTIVATE%" %CONDA_ENV%
python -m src.lib.build_rag
if errorlevel 1 (
    echo ERROR: Build failed.
    exit /b 1
)
echo === [build] Done ===
exit /b 0

:do_prices
echo.
echo === [prices] Updating card prices from Scryfall ===
call "%_ACTIVATE%" %CONDA_ENV%
python -m src.lib.prices
if errorlevel 1 (
    echo ERROR: Price update failed.
    exit /b 1
)
echo === [prices] Done ===
exit /b 0

:do_update
echo.
echo === [update] Refreshing card database (download + prices + clean rebuild) ===
call "%_ACTIVATE%" %CONDA_ENV%
python -m src.lib.update_card_database
if errorlevel 1 (
    echo ERROR: Update failed.
    exit /b 1
)
echo === [update] Done ===
echo NOTE: Restart the MCP server and deck editor so they pick up the new card data.
exit /b 0

:do_uninstall
echo.
echo === [uninstall] Removing all installed dependencies ===
call "%_ACTIVATE%" base

REM Remove conda environment
conda env list | findstr /b /c:"%CONDA_ENV% " >nul 2>&1
if !errorlevel!==0 (
    echo Removing conda environment "%CONDA_ENV%"...
    call conda env remove -n %CONDA_ENV% -y
    if errorlevel 1 (
        echo ERROR: Failed to remove conda environment.
        exit /b 1
    )
    echo Conda environment removed.
) else (
    echo Conda environment "%CONDA_ENV%" not found. Skipping.
)

REM Remove downloaded data files
if exist "data\AtomicCards.json" (
    echo Deleting data\AtomicCards.json...
    del "data\AtomicCards.json"
) else (
    echo data\AtomicCards.json not found. Skipping.
)
if exist "data\prices.json" (
    echo Deleting data\prices.json...
    del "data\prices.json"
) else (
    echo data\prices.json not found. Skipping.
)

REM Remove GraphRAG graph and LanceDB artifacts
if exist "data\graphrag" (
    echo Deleting data\graphrag\...
    rmdir /s /q "data\graphrag"
) else (
    echo data\graphrag\ not found. Skipping.
)

echo === [uninstall] Done ===
exit /b 0

:do_reinstall
echo.
echo === [reinstall] Full uninstall then install ===
call :do_uninstall
if errorlevel 1 exit /b 1
call :do_install
if errorlevel 1 exit /b 1
call :do_download
if errorlevel 1 exit /b 1
call :do_build
if errorlevel 1 exit /b 1
echo === [reinstall] Done ===
exit /b 0

:usage
echo.
echo Usage: .\%~nx0 [install^|download^|build^|prices^|update^|uninstall^|reinstall] [--force]
echo.
echo   (no stage)           Run all stages: install, download, build
echo   install              Create conda env and install Python deps
echo   download             Download AtomicCards.json
echo   build                Ingest data and build GraphRAG and LanceDB artifacts
echo   prices               Update card prices from Scryfall to data/prices.json
echo   update               Refresh card database when new cards come out:
echo                          force-download AtomicCards.json, refresh prices,
echo                          and clean-rebuild GraphRAG and LanceDB artifacts.
echo                          Restart MCP server and deck editor afterwards.
echo   uninstall            Remove conda env, downloaded data, and GraphRAG index
echo   reinstall            Full uninstall then full install (install + download + build)
echo.
echo   --force              Skip-checks override:
echo                          install  : remove and recreate the conda env
echo                          download : re-download even if file exists
echo.
exit /b 1

:end
endlocal
