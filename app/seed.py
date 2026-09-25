import csv
import os

from app import db
from app.models import User, Sector, Category, Tab, Role, Machine, MachineCapacity

SEED_DATA_DIR = os.path.join(os.path.dirname(__file__), 'seed_data')

SECTOR_NAMES = [
    'Diretoria',
    'PCP',
    'Produção',
    'Comercial',
    'Financeiro',
    'Qualidade',
    'Supply Chain',
    'Desenvolvimento',
    'TI/Sistema',
    'RH',
]

# Cargo: define em quais categorias o usuario pode editar/movimentar itens.
# 'Desenvolvedor' e so informativo aqui — o acesso total de verdade vem do
# campo is_admin do usuario.
ROLES_DATA = [
    ('Desenvolvedor', ['planejamento', 'producao', 'desenvolvimento', 'sistema']),
    ('Gestão Produção', ['producao']),
    ('Líder Produção', ['producao']),
    ('Desenvolvimento', ['desenvolvimento']),
    ('Gestão Planejamento', ['planejamento', 'desenvolvimento']),
    ('Analista', ['planejamento', 'desenvolvimento']),
]

# Estrutura real replicada do PCP Planning (Conexão Malhas).
# cada tab: (slug, nome_pt, nome_es, icone, cor_do_icone, novo?)
CATEGORIES_DATA = [
    {
        'slug': 'planejamento',
        'name_pt': 'Gestão Planejamento',
        'name_es': 'Gestión Planificación',
        'icon': '\U0001F4C5',
        'order': 1,
        'sector': 'PCP',
        'tabs': [
            ('visao-geral', 'Visão Geral', 'Vista General', '\U0001F4CA', '#2f7fe0', False),
            ('programacao', 'Programação', 'Programación', '\U0001F5D3', '#e0972f', False),
            ('historico', 'Histórico', 'Historial', '\U0001F551', '#5b6b7c', True),
            ('relatorio-pedidos', 'Relatório Pedidos', 'Informe de Pedidos', '\U0001F9FE', '#2f7fe0', False),
            ('pre-programacao', 'Pré-Programação', 'Pre-Programación', '\U0001F5C2', '#e0972f', False),
        ],
    },
    {
        'slug': 'producao',
        'name_pt': 'Gestão Produção',
        'name_es': 'Gestión Producción',
        'icon': '\U0001F3ED',
        'order': 2,
        'sector': 'Produção',
        'tabs': [
            ('gestao-malharia', 'Gestão da Malharia', 'Gestión de Tejeduría', '\U0001F9F6', '#1f9d8f', False),
            ('controle-producao', 'Controle de Produção', 'Control de Producción', '\U0001F39B', '#2f7fe0', False),
            ('relatorios-producao', 'Relatórios', 'Informes', '\U0001F4C4', '#5c6bc0', True),
            ('dashboard-tempo-real', 'Dashboard Tempo Real', 'Panel en Tiempo Real', '⏱', '#e5484d', True),
        ],
    },
    {
        'slug': 'comercial',
        'name_pt': 'Gestão Comercial',
        'name_es': 'Gestión Comercial',
        'icon': '\U0001F4BC',
        'order': 2,
        'sector': 'Comercial',
        'tabs': [
            ('dashboard-comercial', 'Dashboard Comercial', 'Dashboard Comercial', '\U0001F4CA', '#2f7fe0', True),
            ('pedidos-comercial-mi', 'Pedidos Comercial (MI)', 'Pedidos Comercial (MI)', '\U0001F1E7\U0001F1F7', '#3fa34d', True),
            ('pedidos-comercial-me', 'Pedidos Comercial (ME)', 'Pedidos Comercial (ME)', '\U0001F30E', '#e0972f', True),
        ],
    },
    {
        'slug': 'financeira',
        'name_pt': 'Gestão Financeira',
        'name_es': 'Gestión Financiera',
        'icon': '\U0001F4B0',
        'order': 3,
        'sector': 'Financeiro',
        'tabs': [
            ('painel-financeiro', 'Painel Financeiro', 'Panel Financiero', '\U0001F4B9', '#3fa34d', True),
        ],
    },
    {
        'slug': 'qualidade',
        'name_pt': 'Gestão Qualidade',
        'name_es': 'Gestión Calidad',
        'icon': '✅',
        'order': 4,
        'sector': 'Qualidade',
        'tabs': [
            ('painel-qualidade', 'Painel Qualidade', 'Panel de Calidad', '\U0001F50E', '#e0972f', True),
        ],
    },
    {
        'slug': 'estoque',
        'name_pt': 'Gestão Estoque',
        'name_es': 'Gestión Stock',
        'icon': '\U0001F4E6',
        'order': 5,
        'sector': 'Supply Chain',
        'tabs': [
            ('dashboard-estoque', 'Supply Chain', 'Supply Chain', '\U0001F4CA', '#8a5fe0', True),
            ('controle-estoque', 'Controle do Estoque (Almoxarifado)', 'Control de Stock (Almacén)', '\U0001F3D7\U0000FE0F', '#3fa34d', True),
        ],
    },
    {
        'slug': 'desenvolvimento',
        'name_pt': 'Gestão Desenvolvimento',
        'name_es': 'Gestión Desarrollo',
        'icon': '\U0001F9EA',
        'order': 6,
        'sector': 'Desenvolvimento',
        'tabs': [
            ('saved-drawing', 'Saved Drawing', 'Diseños Guardados', '✏', '#8a5fe0', False),
            ('amostras-manutencoes', 'Amostras - Manutenções', 'Muestras - Mantenimientos', '\U0001F9F5', '#8a5fe0', False),
        ],
    },
    {
        'slug': 'sistema',
        'name_pt': 'Gestão Sistema',
        'name_es': 'Gestión Sistema',
        'icon': '⚙',
        'order': 7,
        'sector': 'TI/Sistema',
        'tabs': [
            ('controle-acessos', 'Controle de Acessos', 'Control de Accesos', '\U0001F464', '#2f7fe0', False),
            ('base-de-dados', 'Base de Dados', 'Base de Datos', '\U0001F5C4', '#5b6b7c', False),
            ('downloads', 'Downloads', 'Descargas', '⬇', '#3fa34d', False),
        ],
    },
]

DEFAULT_ADMIN_EMAIL = 'luciano@conexaomalhas.com.br'
DEFAULT_ADMIN_PASSWORD = 'Conexao@2026'


def seed_data():
    sectors = {}
    for name in SECTOR_NAMES:
        sector = Sector(name=name)
        db.session.add(sector)
        sectors[name] = sector
    db.session.flush()

    for cat_data in CATEGORIES_DATA:
        category = Category(
            slug=cat_data['slug'],
            name_pt=cat_data['name_pt'],
            name_es=cat_data['name_es'],
            icon=cat_data['icon'],
            order=cat_data['order'],
        )
        db.session.add(category)
        db.session.flush()

        default_sector = sectors.get(cat_data['sector'])
        for index, (tab_slug, name_pt, name_es, icon, icon_color, is_new) in enumerate(cat_data['tabs']):
            tab = Tab(
                category_id=category.id,
                slug=tab_slug,
                name_pt=name_pt,
                name_es=name_es,
                icon=icon,
                icon_color=icon_color,
                is_new=is_new,
                order=index,
            )
            if default_sector:
                tab.sectors.append(default_sector)
            tab.sectors.append(sectors['Diretoria'])
            db.session.add(tab)

    categories_by_slug = {c.slug: c for c in Category.query.all()}
    for role_name, category_slugs in ROLES_DATA:
        role = Role(name=role_name)
        role.edit_categories = [categories_by_slug[slug] for slug in category_slugs if slug in categories_by_slug]
        db.session.add(role)

    admin_user = User(
        name='Luciano (Administrador)',
        email=DEFAULT_ADMIN_EMAIL,
        country='BR',
        status='ativo',
        is_admin=True,
    )
    admin_user.set_password(DEFAULT_ADMIN_PASSWORD)
    admin_user.sectors = list(sectors.values())
    db.session.add(admin_user)

    db.session.commit()

    seed_machine_capacities()


def _parse_bool(value):
    return str(value).strip().lower() == 'true'


def seed_machine_capacities():
    """Le app/seed_data/maquinas_capacidade.csv e cria as Maquinas e o banco
    de capacidade (estrutura/largura/gramatura por maquina). Pode ser chamado
    de novo com seguranca: so adiciona o que ainda nao existe."""
    csv_path = os.path.join(SEED_DATA_DIR, 'maquinas_capacidade.csv')
    if not os.path.exists(csv_path):
        return

    machines_by_number = {m.number: m for m in Machine.query.all()}
    existing_caps = {
        (c.machine_id, c.estrutura, c.largura1, c.largura2, c.largura3)
        for c in MachineCapacity.query.all()
    }

    with open(csv_path, encoding='utf-8') as f:
        reader = csv.reader(f, delimiter=',')
        next(reader, None)  # cabecalho
        for row in reader:
            if not row or len(row) < 8:
                continue
            maq_field, estrutura, l1, l2, l3, gmin, gmax, ativa = row[:8]
            numero = int(maq_field.split(';')[-1])

            machine = machines_by_number.get(numero)
            if not machine:
                machine = Machine(number=numero)
                db.session.add(machine)
                db.session.flush()
                machines_by_number[numero] = machine

            l1, l2, l3 = float(l1), float(l2), float(l3)
            key = (machine.id, estrutura.strip().upper(), l1, l2, l3)
            if key in existing_caps:
                continue
            existing_caps.add(key)

            db.session.add(MachineCapacity(
                machine_id=machine.id,
                estrutura=estrutura.strip().upper(),
                largura1=l1,
                largura2=l2,
                largura3=l3,
                gramatura_min=float(gmin),
                gramatura_max=float(gmax),
                ativa=_parse_bool(ativa),
            ))

    db.session.commit()
