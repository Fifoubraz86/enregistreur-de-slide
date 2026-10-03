@echo off
rem Cree dist\CaptureReunion\CaptureReunion.exe (dossier a copier ou partager).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Lancez d'abord installer.bat
  pause
  exit /b 1
)
.venv\Scripts\python -m pip install pyinstaller || goto erreur
.venv\Scripts\python -m PyInstaller --noconfirm --windowed --name CaptureReunion ^
  --collect-all windows_capture --collect-all pyaudiowpatch --collect-all imageio_ffmpeg ^
  --collect-data pptx --collect-data docx ^
  --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime ^
  --collect-all av --collect-all tokenizers ^
  lancer.py || goto erreur
echo.
echo Termine : dist\CaptureReunion\CaptureReunion.exe
pause
exit /b 0
:erreur
echo La creation du .exe a echoue.
pause
exit /b 1
