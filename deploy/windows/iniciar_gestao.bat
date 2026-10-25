@echo off
rem Mantem o Gestao Conexao sempre no ar: se o servidor cair, sobe de novo em 5 segundos.
rem Registrado como tarefa agendada (roda sem ninguem logado). Este arquivo deve ficar NA PASTA DO PROJETO (junto de run_producao.py):
rem a pasta e descoberta sozinha, nao precisa editar caminho.
set "PASTA=%~dp0"
if "%PASTA:~-1%"=="\" set "PASTA=%PASTA:~0,-1%"
rem navegador do PDF numa pasta comum (a tarefa roda como SYSTEM, que nao enxerga o AppData do seu usuario)
set PLAYWRIGHT_BROWSERS_PATH=C:\ms-playwright
cd /d "%PASTA%"
:loop
"%PASTA%\venv\Scripts\python.exe" run_producao.py >> "%PASTA%\gestao_log.txt" 2>&1
timeout /t 5 /nobreak >nul
goto loop
