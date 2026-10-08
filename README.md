# Gestão Conexão

SaaS de controle operacional de produção (estilo PCP Planning), construído do zero em **Python (Flask)** no backend e **HTML/Jinja2** no frontend, pronto para depois migrar para um VPS.

## Como rodar localmente

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Acesse: http://localhost:5000

Na primeira execução o sistema cria automaticamente o banco `gestao_conexao.db` (SQLite) já populado com:

- 6 categorias de gestão (com as abas reais do PCP Planning): Planejamento, Produção, Comercial/Pedidos, Qualidade, Desenvolvimento e Sistema
- Setores padrão: Diretoria, PCP, Produção, Comercial, Qualidade, Desenvolvimento, TI/Sistema, RH
- Um usuário administrador inicial:
  - **E-mail:** luciano@conexaomalhas.com.br
  - **Senha:** Conexao@2026 (troque em **Trocar Senha** assim que entrar)

## Fluxo de entrada

1. **`/`** — tela escura "Escolha seu tipo de acesso": **Acesso ao Planejamento** (PCP Planning) ou **Chamados / Recursos** (suporte de TI).
2. Ao escolher um tipo, a pessoa seleciona o **ambiente**: Brasil 🇧🇷 ou Guatemala 🇬🇹 — isso já define o idioma (PT/ES) das telas seguintes.
3. Cai na tela de **login** (ou pode ir em "Cadastre-se" para criar uma conta nova).
4. Login válido entra no módulo escolhido (Planejamento ou Chamados). Dá pra trocar de área a qualquer momento pelo seletor no topo da barra lateral.

## Como funciona o controle de acesso

1. Qualquer pessoa pode se cadastrar em `/cadastro` (Nome, E-mail, Senha, País: Brasil ou Guatemala).
2. O cadastro entra com status **Pendente** e não consegue logar ainda.
3. Um administrador (ou alguém do setor **TI/Sistema**) abre o link **Administração** (aparece no rodapé da barra lateral só pra quem tem essa permissão), aprova o usuário (muda status para **Ativo**), marca os **setores** dele e salva.
4. A partir daí, o usuário só enxerga as abas (dentro das 6 categorias de Gestão) que estiverem liberadas para o(s) setor(es) dele. Isso é configurado em **Administração → Permissões**, numa matriz Aba x Setor.
5. Quem tem a flag **Administrador geral** enxerga e acessa tudo, independente de setor.
6. Setores podem ser criados/removidos em **Administração → Setores**.
7. O módulo **Chamados / Recursos** é liberado para qualquer usuário logado (é o helpdesk interno); só a aba **Auditoria Sistêmica** dentro dele exige admin/TI.

## Idiomas / países

- A escolha de ambiente (BR/GT) na entrada já define o idioma inicial (Português/Espanhol).
- Dentro do sistema, dá pra trocar o ambiente a qualquer momento pelos botões **BR / GUATEMALA** no rodapé da barra lateral.

## Estrutura do projeto

```
GestaoConexao/
  run.py                    -> ponto de entrada (python run.py)
  requirements.txt
  gestao_conexao.db         -> criado automaticamente (SQLite)
  app/
    __init__.py              -> app factory, registra blueprints, cria/seed do banco
    models.py                -> User, Sector, Category, Tab (+ tabelas de associação)
    i18n.py                  -> dicionário de textos PT/ES
    seed.py                  -> dados iniciais (categorias/abas reais, setores, admin)
    decorators.py            -> @admin_required (admin geral OU setor TI/Sistema)
    gateway/routes.py        -> tela de tipo de acesso + escolha de ambiente (BR/GT)
    auth/routes.py           -> login, cadastro, logout, trocar senha
    main/routes.py           -> dashboard e páginas de módulo (abas) do Planejamento
    admin/routes.py          -> usuários, setores, permissões
    chamados/routes.py       -> módulo de Chamados / Recursos (helpdesk interno)
    comercial_me/routes.py   -> aba Comercial ME (planilhas de exportação importadas)
    seed_data/comercial_me/  -> tabelas geradas por importar_comercial_me.py (JSON)
  importar_pcp_hub.py        -> carrega a fotografia do PCP Hub (Planejamento e Produção)
  importar_comercial_me.py   -> lê "Planilhas de Exportação/*.xlsx" e gera a aba Comercial ME
    templates/               -> HTML (Jinja2)
    static/css/style.css     -> tema escuro na entrada, branco/azul (Conexão) no app
    static/js/main.js        -> menu lateral (accordion + toggle mobile)
```

## Atualizar a aba Comercial ME

Quando as planilhas de exportação forem atualizadas, substitua os arquivos na pasta
`Planilhas de Exportação` e rode, na pasta do projeto com a venv ativa:

```
python importar_comercial_me.py
```

Ele lê só as abas visíveis (abas ocultas, como contatos e dados bancários, não são importadas),
gera os arquivos em `app/seed_data/comercial_me/` e a aba passa a mostrar os dados novos.
O que cada ambiente vê: Brasil mostra as planilhas do Brasil (e as amostras marcadas BR);
Guatemala mostra as da Guatemala (e as amostras marcadas GT).

## Próximos passos sugeridos

- Trocar `SECRET_KEY` (variável de ambiente) antes de qualquer deploy.
- Trocar SQLite por PostgreSQL ao migrar para o VPS (só muda `SQLALCHEMY_DATABASE_URI`).
- Implementar o conteúdo real de cada módulo (hoje cada aba é uma página "em construção").
- Implementar de fato o módulo de Chamados (abrir/acompanhar chamados, indicadores).
- Adicionar tela de "esqueci minha senha".
- Colocar o projeto em produção atrás de HTTPS (ex.: Caddy/Nginx) e usar um servidor WSGI (gunicorn/waitress) em vez do `flask run`.
