"""Pedidos de venda de anos anteriores (2023, 2024 e 2025) lidos da planilha "Pedido de venda" (uma aba por mes: Fev_23 ... Jan_26).

A planilha tem tabelas dinamicas que o openpyxl nao abre, entao o arquivo .xlsx e lido direto (zip + xml).
Em cada aba mensal: DATA (A), PEDIDO (B), Cliente - Fantasia (I), LOCAL MI/ME (J), Metros (H) e VALOR R$ (K, ja convertido em reais).
O resultado fica em instance/totvs/pedidos_planilha.json (so no computador do Gestao; a planilha em si nao e guardada).
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from . import api

RAIZ = Path(__file__).resolve().parents[2]                 # pasta do Gestao
ARQUIVO = api.OUT_DIR / 'pedidos_planilha.json'
VERSAO = 5                                                # sobe quando muda o que e lido da planilha: o arquivo e lido de novo mesmo sem ter sido trocado
ANOS = (2023, 2024, 2025)                                       # os anos que vem da planilha (2026 vem do TOTVS)
_NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main', 'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
_MES = re.compile(r'^(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\w*_(\d{2})$', re.I)


def _abas(z):
    ss = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', _NS):
            ss.append(''.join(t.text or '' for t in si.iter('{%s}t' % _NS['m'])))
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = {r.get('Id'): r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
    abas = []
    for sh in wb.find('m:sheets', _NS):
        alvo = rels[sh.get('{%s}id' % _NS['r'])].lstrip('/')
        abas.append((sh.get('name').strip(), alvo if alvo.startswith('xl/') else 'xl/' + alvo))
    return ss, abas


def _linhas(z, ss, caminho):
    for row in ET.fromstring(z.read(caminho)).find('m:sheetData', _NS):
        d = {}
        for c in row:
            col = re.match(r'[A-Z]+', c.get('r')).group()
            v, t = c.find('m:v', _NS), c.get('t')
            if v is None:
                continue
            if t == 's':
                d[col] = ss[int(v.text)]
            elif t in ('str', 'b', 'e'):
                d[col] = v.text
            else:
                try:
                    d[col] = float(v.text)
                except ValueError:
                    pass
        yield int(row.get('r')), d


def localizar() -> Path | None:
    """A planilha "Pedido de venda" na pasta do Gestao (ou em instance/): o .xlsx mais recente cujo nome contenha pedido+venda.
    PEDIDOS_PLANILHA no .env pode apontar para um caminho especifico."""
    if os.getenv('PEDIDOS_PLANILHA'):
        p = Path(os.getenv('PEDIDOS_PLANILHA'))
        return p if p.is_file() else None
    achados = []
    for pasta in (RAIZ, RAIZ / 'instance', api.OUT_DIR):
        try:
            for f in pasta.iterdir():
                nome = re.sub(r'[\s_\-]+', '', f.name.lower())
                if f.is_file() and nome.endswith('.xlsx') and not f.name.startswith('~$') and 'pedido' in nome and 'venda' in nome:
                    achados.append(f)
        except OSError:
            pass
    return max(achados, key=lambda f: f.stat().st_mtime) if achados else None


def sincronizar() -> bool:
    """Se ha uma planilha na pasta e ela e nova (ou foi trocada), le de novo. Devolve True se leu agora."""
    f = localizar()
    if not f:
        return False
    try:
        mt = round(f.stat().st_mtime)
        atual = ler()
        if atual.get('arquivo') == f.name and atual.get('mtime') == mt and atual.get('versao') == VERSAO:
            return False
        importar(f, origem=(f.name, mt))
        return True
    except Exception as e:  # noqa: BLE001  - planilha aberta/corrompida: segue com o que ja tem
        print(f'[pedidos] nao consegui ler a planilha {f.name}: {e}', flush=True)
        return False


def _carteira(z, ss, caminho, mes: int):
    """Tabela 'Pedido de Venda - Carteira' de uma aba de 2026: {ano: {dia: valor}} para 2023 e 2024 (colunas do cabecalho com o ano)."""
    base = date(1899, 12, 30)
    linhas = list(_linhas(z, ss, caminho))
    cab = None
    for r, d in linhas:
        if r <= 6:
            for col, v in d.items():
                if v == 'Data PV':
                    cab = (r, col, d)
    if not cab:
        return {}, {}, {}
    r0, col_data, dh = cab
    colunas = {c: int(v) for c, v in dh.items() if isinstance(v, float) and int(v) in ANOS}
    # metas em metros (colunas Meta/Real/%mts/%Meta da tabela): a linha 1 traz o saldo herdado (Meta), a meta do periodo (Real) e o % herdado (%Meta)
    nomes = {str(v).strip(): c for c, v in dh.items() if isinstance(v, str)}
    l1 = next((d for r, d in linhas if r == 1), {})
    metas = {}
    if nomes.get('Real') and isinstance(l1.get(nomes['Real']), float) and l1[nomes['Real']] > 0:
        metas = dict(meta=l1[nomes['Real']], saldo=l1.get(nomes.get('Meta')) if isinstance(l1.get(nomes.get('Meta')), float) else 0.0,
                     pct=l1.get(nomes.get('%Meta')) if isinstance(l1.get(nomes.get('%Meta')), float) else None)
    saida = {a: {} for a in colunas.values()}
    totais, achou_total = {}, False
    for r, d in linhas:
        if r <= r0:
            continue
        if not isinstance(d.get(col_data), float):                # a 1a linha sem data, com valores grandes, e o TOTAL da planilha (SOMA da coluna)
            if not achou_total and any(isinstance(d.get(c), float) and d[c] > 1000 for c in colunas):
                achou_total = True
                totais = {a: round(d[c], 2) for c, a in colunas.items() if isinstance(d.get(c), float)}
            continue
        dia = base + timedelta(days=int(d[col_data]))
        if dia.month != mes:                                   # linhas que sobram de outro mes
            continue
        for c, a in colunas.items():
            if isinstance(d.get(c), float) and d[c]:
                saida[a][str(dia.day)] = round(d[c], 2)
    return saida, totais, metas


def importar(arquivo, origem=None) -> dict:
    """Le a planilha (caminho ou arquivo aberto), guarda os pedidos de ANOS e devolve um resumo."""
    z = zipfile.ZipFile(arquivo)
    ss, abas = _abas(z)
    base = date(1899, 12, 30)
    pedidos, vistos, lidas = [], set(), []
    for nome, caminho in abas:
        m = _MES.match(nome)
        if not m or 2000 + int(m.group(2)) not in ANOS:
            continue
        lidas.append(nome)
        cab, cols = None, {}
        for r, d in _linhas(z, ss, caminho):
            if cab is None:                                       # o cabecalho muda de lugar/colunas entre os anos: acha pela linha que tem "DATA"
                if r <= 3 and any(str(v).strip().upper() == 'DATA' for v in d.values() if isinstance(v, str)):
                    cab = r
                    for col, v in d.items():
                        h = re.sub(r'\s+', '', str(v)).upper() if isinstance(v, str) else ''
                        if h == 'DATA': cols.setdefault('data', col)
                        elif h == 'PEDIDO': cols.setdefault('pedido', col)
                        elif h == 'PED.CLI': cols.setdefault('pedcli', col)
                        elif h.startswith('CLIENTE'): cols.setdefault('cliente', col)
                        elif h == 'FANTASIA': cols.setdefault('fantasia', col)
                        elif h == 'LOCAL': cols.setdefault('local', col)
                        elif h in ('VALORR$', 'R$VALOR'): cols.setdefault('valor', col)
                        elif h == 'METROS': cols.setdefault('metros', col)
                continue
            if r <= cab or 'data' not in cols or 'valor' not in cols:
                continue
            dt, val = d.get(cols['data']), d.get(cols['valor'])
            if not isinstance(dt, float) or not isinstance(val, float):
                continue
            if 'AMOSTRA' in str(d.get(cols.get('pedcli', ''), '')).upper():
                continue                                          # amostra nao e pedido de venda
            dia = base + timedelta(days=int(dt))
            ped = d.get(cols.get('pedido', ''))
            chave = (dia.year, int(ped)) if isinstance(ped, float) else None
            if dia.year not in ANOS or (chave and chave in vistos):
                continue
            if chave:
                vistos.add(chave)
            local = 'ME' if str(d.get(cols.get('local', ''), '')).strip().upper() == 'ME' else 'MI'
            met = d.get(cols.get('metros', ''))
            cli = str(d.get(cols.get('cliente', '')) or d.get(cols.get('fantasia', '')) or '').strip()[:60]
            pedidos.append([dia.isoformat(), round(val, 2), round(met, 2) if isinstance(met, float) else 0.0, local, cli])
    totais_planilha = {str(a): {} for a in ANOS}
    metas_planilha = {}
    carteira = {str(a): {} for a in ANOS}                      # 2023/2024: valores da tabela Carteira de cada aba de 2026 (como a planilha mostra)
    for nome, caminho in abas:
        m = _MES.match(nome)
        if m and 2000 + int(m.group(2)) == 2026:
            mes = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'].index(m.group(1).lower()) + 1
            dias_c, tot_c, metas_c = _carteira(z, ss, caminho, mes)
            if metas_c:
                metas_planilha[str(mes)] = metas_c
            for a, dias in dias_c.items():
                if dias:
                    carteira[str(a)][str(mes)] = dias
                    if a in tot_c:
                        totais_planilha[str(a)][str(mes)] = tot_c[a]
    if not pedidos:
        raise ValueError('Não achei pedidos de 2023 a 2025 nas abas mensais (ex.: Out_24). É a planilha "Pedido de venda"?')
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO.write_text(json.dumps(dict(versao=VERSAO, atualizado=datetime.now().isoformat(timespec='seconds'), anos=list(ANOS), arquivo=origem[0] if origem else None, mtime=origem[1] if origem else None, carteira=carteira, totais=totais_planilha, metas=metas_planilha, pedidos=pedidos), ensure_ascii=False), encoding='utf-8')
    return dict(abas=len(lidas), pedidos=len(pedidos), meses_carteira={a: sorted(int(m) for m in v) for a, v in carteira.items()}, por_ano={str(a): sum(1 for p in pedidos if p[0].startswith(str(a))) for a in ANOS})


def ler() -> dict:
    try:
        return json.loads(ARQUIVO.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
