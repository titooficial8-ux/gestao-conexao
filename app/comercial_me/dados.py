"""Leitura dos dados importados da aba Comercial ME (app/seed_data/comercial_me)."""
import json
import os
from functools import lru_cache

DADOS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'seed_data', 'comercial_me')
ORDEM_GRUPOS = ['Pedidos', 'Crédito e pagamentos', 'Amostras e cotações', 'Documentação e envios',
                'Volume de vendas', 'Outras planilhas']


@lru_cache(maxsize=1)
def catalogo_completo():
    caminho = os.path.join(DADOS_DIR, 'catalogo.json')
    if not os.path.exists(caminho):
        return {'gerado_em': None, 'tabelas': []}
    with open(caminho, encoding='utf-8') as f:
        return json.load(f)


def catalogo(regiao=None):
    """Todas as tabelas, na ordem dos grupos (Brasil e Guatemala veem as mesmas; 'regiao' so indica a origem do dado)."""
    tabelas = list(catalogo_completo()['tabelas'])
    tabelas.sort(key=lambda t: ORDEM_GRUPOS.index(t['grupo']) if t['grupo'] in ORDEM_GRUPOS else 99)
    return tabelas


@lru_cache(maxsize=64)
def carregar(id_):
    with open(os.path.join(DADOS_DIR, id_ + '.json'), encoding='utf-8') as f:
        return json.load(f)


def existe(id_):
    return any(t['id'] == id_ for t in catalogo_completo()['tabelas'])


def limpar_cache():
    catalogo_completo.cache_clear()
    carregar.cache_clear()
