"""Pais de cada cliente de exportacao -> bandeira (static/img/flags/<codigo>.svg; Brasil e Guatemala usam as imagens do ambiente)."""
import re
import unicodedata

PAISES = {  # codigo da bandeira: (nome, apelidos sem acento em minusculas)
    'cl': ('Chile', ('chile',)), 'py': ('Paraguai', ('paraguay', 'paraguai')), 'co': ('Colômbia', ('colombia',)),
    'gt': ('Guatemala', ('guatemala',)), 'ar': ('Argentina', ('argentina',)), 'uy': ('Uruguai', ('uruguay', 'uruguai')),
    'bo': ('Bolívia', ('bolivia',)), 'pe': ('Peru', ('peru',)), 'ec': ('Equador', ('ecuador', 'equador')),
    've': ('Venezuela', ('venezuela',)), 'br': ('Brasil', ('brasil', 'brazil')),
}
_ALIAS = {a: cod for cod, (_, nomes) in PAISES.items() for a in nomes}


def normalizar(texto: str) -> str:
    t = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]', '', t)


def codigo_pais(nome: str):
    n = unicodedata.normalize('NFKD', str(nome or '')).encode('ascii', 'ignore').decode().lower().strip()
    return _ALIAS.get(n) or next((cod for alias, cod in _ALIAS.items() if alias in n), None)


def pais_do_cliente(cliente: str, tabela: dict):
    """tabela = {nome normalizado: pais}. Casa o nome exato, depois por inicio (ex.: 'BED TIME S.A' ~ 'bedtime'), depois por palavra do pais no nome."""
    n = normalizar(cliente)
    if not n:
        return None
    if n in tabela:
        return codigo_pais(tabela[n])
    for chave, pais in tabela.items():
        if len(chave) >= 5 and (n.startswith(chave) or chave.startswith(n)):
            return codigo_pais(pais)
    return codigo_pais(cliente)               # 'FLEX CHILE' -> Chile mesmo sem cadastro
