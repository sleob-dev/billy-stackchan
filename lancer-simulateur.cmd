@echo off
rem Ouvre le simulateur StackChan dans sa propre fenetre (mode application de Chrome ou Edge).
rem Si Piper est installe, lance aussi le serveur de voix et ouvre le simulateur depuis lui.
set "DIR=%~dp0"
set "PAGE=%DIR%simulateur\index.html"
set "URL=file:///%PAGE:\=/%"
set "PY=%DIR%serveur\piper\.venv\Scripts\python.exe"

if exist "%PY%" (
  set "URL=http://127.0.0.1:5002/"
  curl -s -o nul http://127.0.0.1:5002/voices || start "Piper StackChan" /min "%PY%" "%DIR%serveur\piper\serveur.py"
  for /l %%i in (1,1,20) do (
    curl -s -o nul http://127.0.0.1:5002/voices && goto ouvrir
    timeout /t 1 /nobreak >nul
  )
)

:ouvrir
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"

if exist "%CHROME%" (
  start "" "%CHROME%" --app="%URL%" --window-size=1180,760
) else if exist "%EDGE%" (
  start "" "%EDGE%" --app="%URL%" --window-size=1180,760
) else (
  start "" "%URL%"
)
