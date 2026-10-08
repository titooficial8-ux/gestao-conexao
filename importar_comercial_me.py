"""Importa as planilhas de EXPORTACAO (pasta 'Planilhas de Exportação') para a aba
'Comercial ME' do Gestao Conexao.

Le as abas VISIVEIS de cada planilha e grava arquivos JSON em
app/seed_data/comercial_me/ (um por tabela + um catalogo.json). As abas ocultas
(lista de contatos, dados bancarios etc.) NAO sao importadas.

Uso (na pasta do projeto, com a venv ativa):
    python importar_comercial_me.py
Depois de atualizar as planilhas, rode de novo e de um 'git add/commit/push'.
"""
import datetime
import glob
import json
import os
import re
import warnings

import openpyxl
from openpyxl.utils import get_column_letter

warnings.filterwarnings('ignore')

RAIZ = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(RAIZ, 'app', 'seed_data', 'comercial_me')

# (trecho do nome do arquivo, nome da aba sem espacos nas pontas) -> configuracao
# tipo 'tabela': header = linha do cabecalho (1 = primeira linha); 'planilha': mostra a aba como ela e.
# regiao: BR, GT (aparece so naquele ambiente) ou ALL.
G_PED, G_CRE, G_AMO, G_DOC, G_VOL, G_OUT = (
    'Pedidos', 'Crédito e pagamentos', 'Amostras e cotações', 'Documentação e envios', 'Volume de vendas', 'Outras planilhas')

BR, GT = 'PEDIDOS BR', 'GUATEMALA'
CRED, AMO, PROC, PROF, VOL = 'Gestao de Credito', 'PEDIDOS E COTA', 'Processos', 'Proform', 'Volume Vendas'

CONFIG = [
    # ---- Brasil: pedidos ----
    (BR, 'FOLLOW-UP PEDIDOS', dict(id='br_followup', titulo='Follow-up de pedidos', grupo=G_PED, regiao='BR', tipo='tabela', header=5)),
    (BR, 'Detalhes  pedidos', dict(id='br_detalhes', titulo='Detalhe dos pedidos (itens)', grupo=G_PED, regiao='BR', tipo='tabela', header=4)),
    (BR, 'Sequência interna invoicing', dict(id='br_sequencia', titulo='Sequência interna de invoices', grupo=G_PED, regiao='BR', tipo='tabela', header=3)),
    (BR, 'S&OP Entregas-Produção', dict(id='br_sop', titulo='S&OP: entregas e produção', grupo=G_PED, regiao='BR', tipo='planilha')),
    (BR, 'VOLUME-VALOR-AVRG PRICE', dict(id='br_volume_valor', titulo='Volume, valor e preço médio', grupo=G_VOL, regiao='BR', tipo='planilha')),
    (BR, 'calculos diversos Flexivel', dict(id='br_calculos', titulo='Cálculos diversos (Flexível)', grupo=G_OUT, regiao='BR', tipo='planilha')),
    # ---- Guatemala: pedidos ----
    (GT, 'FOLLOW-UP DIÁRIO PEDIDOS', dict(id='gt_followup', titulo='Follow-up diário de pedidos', grupo=G_PED, regiao='GT', tipo='tabela', header=5)),
    (GT, 'Detalhes  pedidos', dict(id='gt_detalhes', titulo='Detalle de pedidos (ítems)', grupo=G_PED, regiao='GT', tipo='tabela', header=4)),
    (GT, 'Sequência interna invoicing', dict(id='gt_sequencia', titulo='Secuencia interna de invoices', grupo=G_PED, regiao='GT', tipo='tabela', header=3)),
    (GT, 'ULTRA-Sep26', dict(id='gt_ultra', titulo='ULTRA (set/26)', grupo=G_PED, regiao='GT', tipo='planilha')),
    (GT, 'VOL-VALOR - AVRG PRICE', dict(id='gt_volume_valor', titulo='Volumen, valor y precio promedio', grupo=G_VOL, regiao='GT', tipo='planilha')),
    (GT, 'Consumo x Sku', dict(id='gt_consumo', titulo='Consumo por SKU', grupo=G_VOL, regiao='GT', tipo='planilha')),
    (GT, 'Levanta datos historicos 2024', dict(id='gt_historico', titulo='Datos históricos 2024', grupo=G_VOL, regiao='GT', tipo='planilha')),
    # ---- Credito (Brasil) ----
    (CRED, 'GESTAO CREDITO E PAGAMENTOS', dict(id='cred_gestao', titulo='Gestão de crédito e pagamentos', grupo=G_CRE, regiao='BR', tipo='tabela', header=3)),
    (CRED, 'SUMMARY', dict(id='cred_summary', titulo='Resumo do fluxo de crédito', grupo=G_CRE, regiao='BR', tipo='planilha')),
    (CRED, 'notificação clientes', dict(id='cred_notif', titulo='Notificação de clientes', grupo=G_CRE, regiao='BR', tipo='tabela', header=3)),
    (CRED, 'SUNLOCK SPA -flujo crédito1906', dict(id='cred_sunlock', titulo='Sunlock SPA: fluxo de crédito', grupo=G_CRE, regiao='BR', tipo='tabela', header=4)),
    (CRED, 'POLYMAX -flujo de crédito 1805', dict(id='cred_polymax', titulo='Polymax: fluxo de crédito', grupo=G_CRE, regiao='BR', tipo='tabela', header=4)),
    # ---- Amostras ----
    (AMO, 'COTAÇÃO AMOSTRAS', dict(id='am_cotacao', titulo='Cotações de amostras', grupo=G_AMO, regiao='SPLIT', tipo='tabela', header=7, coluna_regiao='BR/GT')),
    (AMO, 'PEDIDOS AMOSTRAS TOTVS', dict(id='am_pedidos', titulo='Pedidos de amostras (TOTVS)', grupo=G_AMO, regiao='BR', tipo='tabela', header=4)),
    (AMO, 'DINAMICA COTAÇÕES-STATUS', dict(id='am_dinamica_cot', titulo='Cotações por cliente e status', grupo=G_AMO, regiao='BR', tipo='planilha')),
    (AMO, 'DINAMICA PEDIDOS AMOSTRAS', dict(id='am_dinamica_ped', titulo='Pedidos de amostras por cliente', grupo=G_AMO, regiao='BR', tipo='planilha')),
    (AMO, 'SPRING JUL26', dict(id='am_spring', titulo='Spring (jul/26)', grupo=G_AMO, regiao='BR', tipo='planilha')),
    (AMO, 'FRAZADAS JUN2026', dict(id='am_frazadas', titulo='Frazadas (jun/26)', grupo=G_AMO, regiao='BR', tipo='planilha')),
    (AMO, 'BED TIME - PROYECTO ESPECIAL', dict(id='am_bedtime', titulo='Bed Time: projeto especial', grupo=G_AMO, regiao='BR', tipo='planilha')),
    (AMO, 'FADESSA', dict(id='am_fadessa', titulo='Fadessa', grupo=G_AMO, regiao='BR', tipo='planilha')),
    (AMO, 'FRAZADAS - PE', dict(id='am_frazadas_pe', titulo='Frazadas: projeto especial', grupo=G_AMO, regiao='BR', tipo='planilha')),
    (AMO, 'PROHFOAM - Act Precios Abr26', dict(id='am_prohfoam', titulo='Prohfoam: atualização de preços (abr/26)', grupo=G_AMO, regiao='BR', tipo='planilha')),
    (AMO, 'Dist Caseros Marzo2026', dict(id='am_caseros', titulo='Dist. Caseros (mar/26)', grupo=G_AMO, regiao='BR', tipo='planilha')),
    (AMO, 'Flex Chile Black V. 2', dict(id='am_flexchile', titulo='Flex Chile: Black V2', grupo=G_AMO, regiao='BR', tipo='planilha')),
    (AMO, 'FACENCO', dict(id='am_facenco', titulo='Facenco', grupo=G_AMO, regiao='BR', tipo='planilha')),
    (AMO, 'Limansky Amostras liberad Jan26', dict(id='am_limansky', titulo='Limansky: amostras liberadas (jan/26)', grupo=G_AMO, regiao='BR', tipo='planilha')),
    # ---- Documentacao e envios ----
    (PROC, 'Pedidos', dict(id='doc_pedidos', titulo='Documentação dos pedidos', grupo=G_DOC, regiao='BR', tipo='tabela', header=1)),
    (PROC, 'Enviados', dict(id='doc_enviados', titulo='Pedidos enviados', grupo=G_DOC, regiao='BR', tipo='tabela', header=1)),
    (PROC, 'Transportadoras', dict(id='doc_transportadoras', titulo='Transportadoras', grupo=G_DOC, regiao='BR', tipo='planilha')),
    (PROC, 'Bandeiras', dict(id='doc_bandeiras', titulo='Bandeiras', grupo=G_DOC, regiao='BR', tipo='tabela', header=1)),
    (PROF, 'Proform Rev. 2026', dict(id='prof_modelo', titulo='Proforma-Invoice (modelo)', grupo=G_DOC, regiao='BR', tipo='planilha')),
    (PROF, 'Order Records', dict(id='prof_registros', titulo='Registro de pedidos (modelo)', grupo=G_DOC, regiao='BR', tipo='planilha')),
    # ---- Volume de vendas ----
    (VOL, '2024-2026 up to SEP 26', dict(id='vol_geral', titulo='Volume por cliente 2024–2026', grupo=G_VOL, regiao='BR', tipo='planilha')),
    (VOL, '2026 CY vs SPYA', dict(id='vol_cy', titulo='2026 vs mesmo período de 2025', grupo=G_VOL, regiao='BR', tipo='planilha')),
    (VOL, 'DIVINO-UY', dict(id='vol_divino', titulo='Divino (Uruguai)', grupo=G_VOL, regiao='BR', tipo='planilha')),
    (VOL, 'BED TIME-ARG', dict(id='vol_bedtime', titulo='Bed Time (Argentina)', grupo=G_VOL, regiao='BR', tipo='planilha')),
    (VOL, 'FLEX-CH', dict(id='vol_flex', titulo='Flex (Chile)', grupo=G_VOL, regiao='BR', tipo='planilha')),
    (VOL, 'SUNLOCK SPA-CH', dict(id='vol_sunlock', titulo='Sunlock SPA (Chile)', grupo=G_VOL, regiao='BR', tipo='planilha')),
    (VOL, 'SIMMONS-ARG', dict(id='vol_simmons', titulo='Simmons (Argentina)', grupo=G_VOL, regiao='BR', tipo='planilha')),
    (VOL, 'Flexible Clientes div.', dict(id='vol_flexible', titulo='Flexible: clientes diversos', grupo=G_VOL, regiao='BR', tipo='planilha')),
    (VOL, 'Flex-Hist-Pais', dict(id='vol_flex_hist', titulo='Flex: histórico por país', grupo=G_VOL, regiao='BR', tipo='planilha')),
]

ERROS = {'#N/A', '#REF!', '#DIV/0!', '#VALUE!', '#NAME?', '#NULL!', '#NUM!'}
MAX_COLS = 60
LINHAS_VAZIAS_PARA_PARAR = 300


def _txt(v):
    return re.sub(r'\s+', ' ', str(v)).strip()


def _valor(cell):
    v = cell.value
    if v is None:
        return None
    if isinstance(v, bool):
        return 'Sim' if v else 'Não'
    if isinstance(v, datetime.datetime):
        return v.strftime('%d/%m/%Y') if (v.hour, v.minute, v.second) == (0, 0, 0) else v.strftime('%d/%m/%Y %H:%M')
    if isinstance(v, datetime.date):
        return v.strftime('%d/%m/%Y')
    if isinstance(v, datetime.time):
        return v.strftime('%H:%M')
    if isinstance(v, (int, float)):
        fmt = getattr(cell, 'number_format', '') or ''
        if '%' in fmt:
            return f'{v * 100:.1f}%'.replace('.0%', '%')
        if isinstance(v, float):
            if v == int(v) and abs(v) < 1e15:
                return int(v)
            return round(v, 4)
        return v
    t = _txt(v)
    if t in ERROS or t == '':
        return None
    return t


def _ler_aba(ws):
    """Devolve (linhas, atualizado_em): linhas = [(numero_da_linha, [valores ate MAX_COLS])]."""
    linhas, vazias = [], 0
    for i, row in enumerate(ws.iter_rows(max_col=MAX_COLS), start=1):
        vals = [_valor(c) for c in row]
        if any(v is not None for v in vals):
            linhas.append((i, vals))
            vazias = 0
        else:
            vazias += 1
            if vazias > LINHAS_VAZIAS_PARA_PARAR and i > 300:
                break
    return linhas


def _aparar_colunas(linhas):
    """Remove colunas totalmente vazias; devolve (indices_mantidos, linhas)."""
    largura = max((len(v) for _, v in linhas), default=0)
    usadas = [j for j in range(largura) if any(j < len(v) and v[j] is not None for _, v in linhas)]
    return usadas, [(n, [v[j] if j < len(v) else None for j in usadas]) for n, v in linhas]


def _atualizado_em(linhas_acima):
    for _, vals in linhas_acima:
        for v in vals:
            if isinstance(v, str) and re.match(r'\d{2}/\d{2}/\d{4}', v):
                return v[:10]
    return None


# colunas que sao IDENTIFICADORES (pedido, codigo, OP, NF, telefone): ficam como texto, sem ponto de milhar
_ID = re.compile(r'(PEDIDO|\bCOD|CODE|CODIGO|NBR|NÚM|\bNUM\b|ORDER|INVOICE|\bNF\b|NOTA FISCAL|FONE|TELF|\bID\b|\bOP\b|NÚMERO|# )', re.I)
_VALOR = re.compile(r'(TOTAL|DIF|US\$|PAGAR|CREDITO|CRÉDITO|METROS|VALOR|PREÇO|PRECIO|QUANT|MTS|KGS)', re.I)


def _texto_id(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    return str(int(v)) if float(v) == int(v) else str(v)


def montar(ws, cfg):
    linhas = _ler_aba(ws)
    if not linhas:
        return None
    if cfg['tipo'] == 'tabela':
        h = cfg['header']
        acima = [(n, v) for n, v in linhas if n < h]
        cab_linha = next((v for n, v in linhas if n == h), None)
        dados = [(n, v) for n, v in linhas if n > h]
        if cab_linha is None or not dados:
            return None
        todas = [(h, cab_linha)] + dados
        usadas, todas = _aparar_colunas(todas)
        colunas, vistos = [], {}
        for j, nome in zip(usadas, todas[0][1]):
            nome = _txt(nome) if nome is not None else get_column_letter(j + 1)
            vistos[nome] = vistos.get(nome, 0) + 1
            colunas.append(nome if vistos[nome] == 1 else f'{nome} ({vistos[nome]})')
        rows = [v for _, v in todas[1:]]
        for j, nome in enumerate(colunas):
            if _ID.search(nome) and not _VALOR.search(nome):
                for r in rows:
                    r[j] = _texto_id(r[j])
        return dict(colunas=colunas, linhas=rows, atualizado_em=_atualizado_em(acima))
    usadas, linhas = _aparar_colunas(linhas)
    colunas = [get_column_letter(j + 1) for j in usadas]
    return dict(colunas=colunas, linhas=[v for _, v in linhas], atualizado_em=_atualizado_em(linhas[:6]))


def main():
    pasta = next(iter(glob.glob(os.path.join(RAIZ, 'Planilhas de Exporta*'))), None)
    if not pasta:
        raise SystemExit("Pasta 'Planilhas de Exportação' não encontrada na pasta do projeto.")
    arquivos = sorted(glob.glob(os.path.join(pasta, '*.xlsx')))
    os.makedirs(SAIDA, exist_ok=True)
    for antigo in glob.glob(os.path.join(SAIDA, '*.json')):
        os.remove(antigo)

    catalogo, ignoradas = [], []
    for trecho, aba, cfg in CONFIG:
        arq = next((a for a in arquivos if trecho.lower() in os.path.basename(a).lower()), None)
        if not arq:
            ignoradas.append(f'{trecho} / {aba}: arquivo não encontrado')
            continue
    por_arquivo = {}
    for trecho, aba, cfg in CONFIG:
        arq = next((a for a in arquivos if trecho.lower() in os.path.basename(a).lower()), None)
        if arq:
            por_arquivo.setdefault(arq, []).append((aba, cfg))

    for arq, abas in por_arquivo.items():
        print(f'\n{os.path.basename(arq)}')
        wb = openpyxl.load_workbook(arq, read_only=True, data_only=True)
        nomes = {ws.title.strip(): ws for ws in wb.worksheets}
        for aba, cfg in abas:
            ws = nomes.get(aba.strip())
            if ws is None or ws.sheet_state != 'visible':
                ignoradas.append(f'{os.path.basename(arq)} / {aba}: aba não encontrada ou oculta')
                continue
            dados = montar(ws, cfg)
            if not dados:
                ignoradas.append(f'{os.path.basename(arq)} / {aba}: sem dados')
                continue
            partes = [(cfg['id'], cfg['titulo'], cfg['regiao'], dados['linhas'])]
            if cfg['regiao'] == 'SPLIT':
                col = dados['colunas'].index(cfg['coluna_regiao'])
                gt = [r for r in dados['linhas'] if str(r[col] or '').strip().upper() == 'GT']
                br = [r for r in dados['linhas'] if str(r[col] or '').strip().upper() != 'GT']
                partes = [(cfg['id'] + '_br', cfg['titulo'], 'BR', br), (cfg['id'] + '_gt', cfg['titulo'], 'GT', gt)]
            for id_, titulo, regiao, linhas in partes:
                if not linhas:
                    continue
                item = dict(id=id_, titulo=titulo, grupo=cfg['grupo'], regiao=regiao, tipo=cfg['tipo'],
                            arquivo=os.path.basename(arq), aba=aba.strip(), atualizado_em=dados['atualizado_em'],
                            colunas=dados['colunas'], linhas=linhas)
                with open(os.path.join(SAIDA, id_ + '.json'), 'w', encoding='utf-8') as f:
                    json.dump(item, f, ensure_ascii=False, separators=(',', ':'))
                catalogo.append({k: item[k] for k in ('id', 'titulo', 'grupo', 'regiao', 'tipo', 'arquivo', 'aba', 'atualizado_em')}
                                | dict(n_linhas=len(linhas), n_colunas=len(dados['colunas'])))
                print(f'  {id_:<22} {len(linhas):>5} linhas x {len(dados["colunas"]):>2} colunas  [{regiao}]  {titulo}')
        wb.close()

    with open(os.path.join(SAIDA, 'catalogo.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(gerado_em=datetime.datetime.now().strftime('%d/%m/%Y %H:%M'), tabelas=catalogo), f, ensure_ascii=False, indent=1)
    print(f'\n{len(catalogo)} tabelas importadas.')
    for i in ignoradas:
        print('  (ignorada)', i)


if __name__ == '__main__':
    main()
