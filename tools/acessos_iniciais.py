r"""Senha provisoria para todos (menos o Christian) + acessos do Edmar e do Luiz Lago.

Uso (na VPS, na pasta do projeto, com a Gestao rodando ou nao):
    1) no .env, uma linha:  SENHA_PADRAO=<senha provisoria que voce escolher>   (min. 8 caracteres, 1 maiuscula, 1 numero, 1 simbolo)
    2) venv\Scripts\python tools\acessos_iniciais.py

O que faz:
  - todos os usuarios, exceto christian.yarzon@..., passam a ter SENHA_PADRAO e sao obrigados a trocar no proximo acesso;
  - cria/atualiza o Edmar (Controladoria, cargo Gestao Controladoria, verificado, ve tudo) com SENHA_PADRAO;
  - cria/atualiza o Luiz Lago (sem setor, cargo Diretoria, ve tudo) com uma senha provisoria ALEATORIA, mostrada uma unica vez no final.
A senha padrao fica so no .env (nunca no GitHub). Ao terminar, apague a linha SENHA_PADRAO do .env.
"""
import os
import re
import secrets
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.chdir(RAIZ)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(RAIZ / '.env')

EXCETO = {'christian.yarzon@conexaomalhas.com.br'}


def forte(s: str) -> bool:
    return len(s) >= 8 and re.search(r'[A-Z]', s) and re.search(r'[0-9]', s) and re.search(r'[^A-Za-z0-9]', s)


def main():
    padrao = os.environ.get('SENHA_PADRAO', '')
    if not forte(padrao):
        sys.exit('Defina SENHA_PADRAO no .env (min. 8 caracteres, 1 maiuscula, 1 numero e 1 simbolo) e rode de novo.')

    from app import create_app, db
    from app.models import Role, Sector, User

    app = create_app()
    with app.app_context():
        total = User.query.count()
        print(f'{total} usuarios no banco. Isto vai trocar a senha de todos, exceto {", ".join(sorted(EXCETO))}.')
        if input('Digite SIM para continuar: ').strip() != 'SIM':
            sys.exit('Cancelado, nada foi alterado.')

        def cargo(nome):
            r = Role.query.filter_by(name=nome).first()
            if not r:
                r = Role(name=nome)
                db.session.add(r)
                db.session.flush()
            return r

        def setor(nome):
            s = Sector.query.filter_by(name=nome).first()
            if not s:
                s = Sector(name=nome)
                db.session.add(s)
                db.session.flush()
            return s

        n = 0
        for u in User.query.all():
            if u.email.lower() in EXCETO:
                continue
            u.set_password(padrao)
            u.deve_trocar_senha = True
            n += 1

        ctrl = setor('Controladoria')
        todos = Sector.query.all()

        def garantir(email, nome, senha, setor_obj, nome_cargo, verificado):
            u = User.query.filter(db.func.lower(User.email) == email).first()
            if not u:
                u = User(email=email, name=nome, country='BR')
                u.set_password(senha)
                db.session.add(u)
            u.name = u.name or nome
            u.status = 'ativo'
            u.is_admin = True            # ve todas as paginas, inclusive Usuarios e Acessos
            u.verificado = verificado
            u.sectors = todos
            u.setor_id = setor_obj.id if setor_obj else None
            u.role_id = cargo(nome_cargo).id
            u.set_password(senha)
            u.deve_trocar_senha = True

        garantir('edmar.santos@conexaomalhas.com.br', 'Edmar', padrao, ctrl, 'Gestão Controladoria', True)
        senha_luiz = secrets.token_urlsafe(9) + '#7A'
        garantir('luiz.lago@conexaomalhas.com.br', 'Luiz Lago', senha_luiz, None, 'Diretoria', False)
        db.session.commit()

        print(f'\nPronto: {n} usuarios com a senha padrao (troca obrigatoria no primeiro acesso).')
        print('Edmar: edmar.santos@conexaomalhas.com.br  -> senha padrao do .env')
        print(f'Luiz Lago: luiz.lago@conexaomalhas.com.br -> senha provisoria: {senha_luiz}')
        print('(guarde esta senha agora; ela nao aparece de novo). Depois apague SENHA_PADRAO do .env.')


if __name__ == '__main__':
    main()
