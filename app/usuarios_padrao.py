"""Contas iniciais por setor (criadas uma unica vez, em bancos novos ou ja existentes).

Todos entram como BRASIL, ativos, com a senha padrao abaixo (trocar no primeiro acesso).
Nao mexe em conta que ja existe (senha, setor e cargo ficam como estao).
"""
from app import db
from app.models import User, Sector, Role, Category

SENHA_PADRAO = 'Sistema123'

# cargo -> categorias que o cargo pode EDITAR (todos veem tudo, via todos os setores)
CARGOS = {
    'Planejamento/Vendas': ['planejamento', 'producao', 'comercial'],
    'Desenvolvimento': ['desenvolvimento'],
}

USUARIOS = [
    # (email, nome, cargo)
    ('luciane.frias@conexaomalhas.com.br', 'Luciane - Coordenadora PCP/Expedição', 'Planejamento/Vendas'),
    ('amabile.moretti@conexaomalhas.com.br', 'Amabile - Analista de PCP', 'Planejamento/Vendas'),
    ('ezequiel.silva@conexaomalhas.com.br', 'Ezequiel - Analista de PCP', 'Planejamento/Vendas'),
    ('kaio.batista@conexaomalhas.com.br', 'Kaio - Analista de PCP', 'Planejamento/Vendas'),
    ('marco@conexaomalhas.com.br', 'Marco - Gerente Comercial', 'Planejamento/Vendas'),
    ('mauricio.stanczyk@conexaomalhas.com.br', 'Maurício', 'Desenvolvimento'),
    ('gustavo.bonfim@conexaomalhas.com.br', 'Gustavo', 'Desenvolvimento'),
    ('diogo.gomes@conexaomalhas.com.br', 'Diogo', 'Desenvolvimento'),
    ('amanda.marthos@conexaomalhas.com.br', 'Amanda', 'Desenvolvimento'),
    ('dora.appolinario@conexaomalhas.com.br', 'Dora', 'Desenvolvimento'),
    ('jose.alves@conexaomalhas.com.br', 'José', 'Desenvolvimento'),
]


def criar_usuarios_padrao():
    categorias = {c.slug: c for c in Category.query.all()}
    cargos = {}
    for nome, slugs in CARGOS.items():
        cargo = Role.query.filter_by(name=nome).first()
        if not cargo:
            cargo = Role(name=nome)
            db.session.add(cargo)
        cargo.edit_categories = [categorias[s] for s in slugs if s in categorias]
        cargos[nome] = cargo
    db.session.flush()

    setores = Sector.query.all()  # ver tudo
    criados = 0
    for email, nome, cargo in USUARIOS:
        if User.query.filter(db.func.lower(User.email) == email).first():
            continue
        u = User(name=nome, email=email, country='BR', status='ativo', is_admin=False, role_id=cargos[cargo].id)
        u.set_password(SENHA_PADRAO)
        u.sectors = list(setores)
        db.session.add(u)
        criados += 1
    db.session.commit()
    return criados
