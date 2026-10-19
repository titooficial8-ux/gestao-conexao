@echo off
rem Mantem o Gestao Conexao sempre no ar: se o servidor cair, sobe de novo em 5 segundos.
rem Registrado como tarefa agendada (roda sem ninguem logado). Edite PASTA se o projeto estiver em outro lugar.
set PASTA=C:\gestao-conexao
cd /d "%PASTA%"
:loop
"%PASTA%\venv\Scripts\python.exe" run_producao.py >> "%PASTA%\gestao_log.txt" 2>&1
timeout /t 5 /nobreak >nul
goto loop
