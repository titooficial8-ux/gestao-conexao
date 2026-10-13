"""PDF identico a tela: abre o Slide mensal do proprio Gestao num Chromium invisivel e imprime (mesmo layout do botao Imprimir)."""
from __future__ import annotations

import os
import secrets
import time

_TOKENS: dict[str, tuple[int, float]] = {}      # token de uso unico -> (id do usuario, validade)


def novo_token(user_id: int) -> str:
    agora = time.time()
    for k in [k for k, v in _TOKENS.items() if v[1] < agora]:
        _TOKENS.pop(k, None)
    t = secrets.token_urlsafe(32)
    _TOKENS[t] = (user_id, agora + 120)
    return t


def consumir_token(t: str):
    v = _TOKENS.pop(t or '', None)
    return v[0] if v and v[1] >= time.time() else None


def disponivel() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return os.getenv('RELATORIO_FAT_CAPTURA', '1') != '0'
    except ImportError:
        return False


def gerar_pdf(base_url: str, user_id: int, ano, mes) -> bytes:
    from playwright.sync_api import sync_playwright
    base = base_url.rstrip('/')
    q = ''.join(f'&{k}={v}' for k, v in (('ano', ano), ('mes', mes)) if v)
    with sync_playwright() as pw:
        nav = pw.chromium.launch(executable_path=os.getenv('RELATORIO_FAT_CHROMIUM') or None)
        try:
            pg = nav.new_page(viewport={'width': 1400, 'height': 900})
            pg.goto(f'{base}/relatorio-faturamento/entrar?t={novo_token(user_id)}{q}', wait_until='domcontentloaded', timeout=60000)
            pg.wait_for_selector('#sl-int', timeout=90000)                 # a ultima tabela do slide so existe depois dos dados carregarem
            pg.wait_for_load_state('networkidle', timeout=60000)
            pg.wait_for_timeout(2500)                                       # animacao dos graficos
            pg.evaluate("window.dispatchEvent(new Event('beforeprint'))")  # preenche titulo/"Gerado em" como no botao Imprimir
            pg.emulate_media(media='print')
            pdf = pg.pdf(prefer_css_page_size=True, print_background=True)
            return pdf
        finally:
            nav.close()
