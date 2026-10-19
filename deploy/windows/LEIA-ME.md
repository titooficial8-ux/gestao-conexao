# Gestão Conexão na VPS Windows (24 h, com HTTPS)

Passo a passo para colocar o Gestão no ar. Rode **um comando por vez** no *Prompt de Comando (Administrador)*.

## O que precisa antes
- Endereço (domínio) do site, ex.: `gestao.conexaomalhas.com.br`, com o DNS apontando para o IP da VPS.
- Python 3.11+ instalado na VPS (`python --version`) e Git (`git --version`).
- Portas 80 e 443 liberadas no firewall da VPS.
- O mesmo Caddy que já roda na VPS (não suba um segundo Caddy, só acrescente o bloco abaixo).

## 1. Baixar o projeto
```
cd C:\
git clone https://github.com/titooficial8-ux/gestao-conexao.git
cd gestao-conexao
git checkout main
```

## 2. Ambiente Python
```
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python -m playwright install chromium
```
(o Chromium é para o PDF dos relatórios.)

## 3. Levar os dados e as senhas do computador atual
Copie do PC onde o Gestão roda hoje para `C:\gestao-conexao\` na VPS (pasta compartilhada, pendrive ou RDP):
- `.env`  (credenciais do TOTVS, e-mail e demais configurações)
- `gestao_conexao.db`  (usuários, programação, histórico)
- a pasta `instance\`  (caches do TOTVS, controle do envio das 08h)
- a planilha `Pedido de venda.xlsx`, se for usada na Entrada de Pedidos

No `.env` da VPS confira/inclua:
```
SECRET_KEY=uma-chave-longa-e-secreta-diferente-da-de-teste
APP_URL=http://127.0.0.1:5000
PORTA=5000
```
`APP_URL` fica em 127.0.0.1 de propósito: o Chromium do PDF abre o Gestão por dentro da VPS.
**Nunca** suba o `.env` ou o `.db` para o GitHub.

## 4. Testar na própria VPS
```
venv\Scripts\python run_producao.py
```
Abra `http://127.0.0.1:5000` no navegador da VPS. Funcionou? Feche com Ctrl+C.

## 5. Deixar rodando 24 h (sobrevive a fechar o RDP e a reiniciar)
```
copy deploy\windows\iniciar_gestao.bat C:\gestao-conexao\iniciar_gestao.bat
schtasks /create /tn "GestaoConexao" /tr "C:\gestao-conexao\iniciar_gestao.bat" /sc onstart /ru SYSTEM /rl HIGHEST /f
schtasks /run /tn "GestaoConexao"
netstat -ano | findstr ":5000"
```
O último comando deve mostrar `LISTENING`. O log fica em `C:\gestao-conexao\gestao_log.txt`.

## 6. Colocar no ar com HTTPS (Caddy)
Abra o Caddyfile que já está em uso (`type C:\caminho\Caddyfile`), acrescente o bloco de `deploy\windows\Caddyfile.exemplo` (com o seu domínio) **no fim do arquivo**, salve e valide:
```
cd /d C:\caminho\do\caddy
caddy.exe validate
taskkill /IM caddy.exe /F
schtasks /run /tn "NomeDaTarefaDoCaddy"
curl -sk -o NUL -w "Gestao: %{http_code}\n" https://gestao.seudominio.com.br
```
Esperado: `200` (ou `302`, que é o redirecionamento para o login). Depois, no navegador, Ctrl+Shift+R.

## 7. Atualizar o site depois
```
cd C:\gestao-conexao
git pull origin main
venv\Scripts\python -m pip install -r requirements.txt
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *"   (cuidado: derruba todo python da VPS; prefira reiniciar só a tarefa)
schtasks /end /tn "GestaoConexao"
schtasks /run /tn "GestaoConexao"
```

## Cuidados
- **Só uma cópia do Gestão deve rodar com os agendadores ligados** (e-mail das 08h, pesagens, pedidos). Quando a VPS assumir, **pare** o `python run.py` do computador antigo, senão o e-mail das 08h pode sair duas vezes.
- O site fica público na internet: use senhas fortes nos usuários e mantenha a `SECRET_KEY` secreta.
- O IP da VPS precisa conseguir acessar `apitotvsmoda.bhan.com.br` (a TOTVS pode exigir liberar o IP novo).

## Gestão em pasta de rede (ex.: `Z:\Rede-PCP\...\GestaoConexao`)
- Use sempre o caminho entre aspas: `cd /d "Z:\Rede-PCP\06- PCP - COLCHÃO\Christian\GestaoConexao"`.
- A pasta `venv` não sobrevive a mudança de lugar: apague-a e crie de novo (`python -m venv venv` e `venv\Scripts\python -m pip install -r requirements.txt`).
- Se o Git reclamar de "dubious ownership": `git config --global --add safe.directory "Z:/Rede-PCP/06- PCP - COLCHÃO/Christian/GestaoConexao"`.
- **Banco SQLite em rede é arriscado.** O Gestão detecta pasta de rede e desliga o modo WAL sozinho, mas o ideal é deixar o banco em disco local: no `.env`, `GESTAO_DB=C:\GestaoConexaoDados\gestao_conexao.db` (copie o `gestao_conexao.db` para lá).
- Tarefa agendada (SYSTEM) **não enxerga a letra `Z:`**; use o caminho de rede completo (`\\servidor\Rede-PCP\...`) ou rode a tarefa com o usuário que tem o mapeamento.
- Rode o Gestão em **um só computador** por vez (e-mail das 08h, pesagens e pedidos).
