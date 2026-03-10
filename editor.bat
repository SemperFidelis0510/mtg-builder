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

call "%_ACTIVATE%" %CONDA_ENV%
python deck_editor.py
if errorlevel 1 (
    echo Deck editor exited with error.
    exit /b 1
)
