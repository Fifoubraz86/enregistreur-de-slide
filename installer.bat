@echo off
rem Installe le logiciel dans un environnement Python isole (.venv). A lancer une seule fois.
cd /d "%~dp0"
where py >nul 2>nul || (
  echo Python est introuvable. Installez-le depuis https://www.python.org/downloads/
  echo en cochant "Add python.exe to PATH", puis relancez ce fichier.
  pause
  exit /b 1
)
py -3 -m venv .venv || goto erreur
.venv\Scripts\python -m pip install --upgrade pip || goto erreur
.venv\Scripts\python -m pip install -r requirements.txt || goto erreur
echo.
echo Installation terminee. Double-cliquez sur lancer.bat pour demarrer.
pause
exit /b 0
:erreur
echo.
echo L'installation a echoue (voir le message ci-dessus).
pause
exit /b 1
