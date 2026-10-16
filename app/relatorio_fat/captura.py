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


AJUDA = 'Instale o navegador do relatório uma vez, no computador do Gestão: python -m pip install -r requirements.txt  e depois  python -m playwright install chromium'


def gerar_pdf(base_url: str, user_id: int, ano, mes, visao: str = '') -> bytes:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError('playwright não instalado. ' + AJUDA) from e
    try:
        return _gerar(sync_playwright, base_url, user_id, ano, mes, visao)
    except Exception as e:  # noqa: BLE001
        if "Executable doesn't exist" in str(e) or 'playwright install' in str(e):
            raise RuntimeError('Chromium não instalado. ' + AJUDA) from e
        raise


def _gerar(sync_playwright, base_url: str, user_id: int, ano, mes, visao: str = '') -> bytes:
    base = base_url.rstrip('/')
    q = ''.join(f'&{k}={v}' for k, v in (('ano', ano), ('mes', mes), ('visao', visao)) if v)
    with sync_playwright() as pw:
        nav = pw.chromium.launch(executable_path=os.getenv('RELATORIO_FAT_CHROMIUM') or None)
        try:
            pg = nav.new_page(viewport={'width': 1400, 'height': 900})
            pg.goto(f'{base}/relatorio-faturamento/entrar?t={novo_token(user_id)}{q}', wait_until='domcontentloaded', timeout=60000)
            if visao == 'pedidos':                                          # Entrada de pedidos: espera os graficos e as duas secoes consultadas no TOTVS (alterados, precos divergentes e cancelados/duplicados)
                pg.wait_for_function("document.querySelectorAll('[data-pronto=\"1\"]').length >= 3 && document.querySelector('#biPedCorpo canvas')", timeout=180000)
            else:
                pg.wait_for_selector('#sl-int', timeout=90000)             # a ultima tabela do slide so existe depois dos dados carregarem
            pg.wait_for_load_state('networkidle', timeout=60000)
            pg.wait_for_timeout(2500)                                       # animacao dos graficos
            pg.evaluate("window.dispatchEvent(new Event('beforeprint'))")  # preenche titulo/"Gerado em" como no botao Imprimir
            pg.emulate_media(media='print')
            pdf = pg.pdf(prefer_css_page_size=True, print_background=True)
            return pdf
        finally:
            nav.close()
