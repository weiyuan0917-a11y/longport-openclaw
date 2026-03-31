@echo off
setlocal

cd /d "%~dp0"

echo [1/3] Installing PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
  echo [ERROR] Failed to install PyInstaller.
  exit /b 1
)

echo [2/3] Building LongPortLauncher.exe...
python -m PyInstaller --noconfirm --onefile --name LongPortLauncher launcher.py
if errorlevel 1 (
  echo [ERROR] Build failed.
  exit /b 1
)

echo [3/3] Done.
echo Output: dist\LongPortLauncher.exe
endlocal
