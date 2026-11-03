"""NF XML (Financeiro): gera o XML de DUIMP no layout que o TOTVS importa, a partir do XML de NF-e do despachante (JMR), do extrato da DUIMP (PDF) e do DANFE.

Fluxo na tela: o usuario envia os arquivos, o sistema le o que consegue (NF-e em XML e texto do extrato), mostra tudo num formulario para conferir/corrigir e so entao gera o XML
(layout, arredondamento pelo maior resto e validacoes da habilidade 'duimp-xml-totvs'), com a lista de pontos para conferir antes de importar no TOTVS."""
from __future__ import annotations

import io
import re
import unicodedata
from decimal import Decimal as D, ROUND_HALF_UP, ROUND_FLOOR, InvalidOperation
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

from app.models import Tab

duimp_bp = Blueprint('duimp', __name__, url_prefix='/duimp')

CAB = ['NUMERODUIMP', 'DATADUIMP', 'DATADESEMBARACO', 'UFDESEMBARACO', 'LOCALDESEMBARACO', 'COTACAODOLAR', 'TAXASISCOMEX', 'DESPESASADUANEIRAS', 'FRETE', 'SEGURO', 'IPI', 'PIS', 'COFINS', 'II', 'ICMS',
       'TOTALMATERIAISDOLAR', 'TOTALMATERIAISREAIS', 'TOTALMATERIAISCIF', 'INFOCOMPLEMENTARES', 'PRODUTOS']
ITM = ['NUMEROADICAO', 'NUMEROITEM', 'CODIGO', 'DESCRICAO', 'NCM', 'UNIDADE', 'QUANTIDADE', 'VALORUNITARIODOLAR', 'ALIQUOTAPIS', 'VALORPIS', 'ALIQUOTACOFINS', 'VALORCOFINS', 'ALIQUOTAIPI', 'VALORIPI',
       'ALIQUOTAII', 'VALORII', 'EXPORTADOR', 'FABRICANTE']
DESPACHANTES = ['ROBERTO DE FREITAS DIAS - CPF: 106.775.578-09', 'WILLIAM PAIXAO MACENA DA SILVA - CPF: 342.685.528-39', 'LUCAS DO CARMO MARTINES - CPF: 462.818.188-81', 'ADRIANO C. MARTINES - CPF: 254.265.948-64']
CENT = D('0.01')


def _autorizar():
    tab = Tab.query.filter_by(slug='nf-xml').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


# ---------------------------------------------------------------- numeros
def num(v, padrao=D('0')) -> D:
    """Aceita 1.234,56 / 1234.56 / 'R$ 1.234,56' e devolve Decimal."""
    if isinstance(v, D):
        return v
    t = str(v if v is not None else '').strip().replace('R$', '').replace('US$', '').replace(' ', '')
    if not t:
        return padrao
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    try:
        return D(t)
    except InvalidOperation:
        return padrao


def q2(x) -> D:
    return D(x).quantize(CENT, ROUND_HALF_UP)


def br(x) -> str:
    """433702 -> 433.702,00"""
    return f'{q2(x):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def pct_txt(p) -> str:
    p = D(p)
    return (f'{p:.2f}'.rstrip('0').rstrip('.') if p == p.to_integral_value() else f'{p:.2f}'.rstrip('0')).replace('.', ',')


def _norm(t) -> str:
    return unicodedata.normalize('NFKD', str(t or '')).encode('ascii', 'ignore').decode().upper()


# ---------------------------------------------------------------- leitura da NF-e (XML)
def _sem_assinatura(b: bytes) -> str:
    t = b.decode('utf-8', 'ignore')
    return re.sub(r'<Signature\b.*?</Signature>', '', t, flags=re.S)                  # a assinatura vem de outra empresa e nao serve ao TOTVS


def _t(e, tag):
    """texto do primeiro descendente com essa tag (ignora namespace)."""
    if e is None:
        return ''
    for x in e.iter():
        if x.tag.split('}')[-1] == tag:
            return (x.text or '').strip()
    return ''


def _todos(e, tag):
    return [x for x in e.iter() if x.tag.split('}')[-1] == tag]


def ler_nfe(conteudo: bytes) -> dict:
    root = ET.fromstring(_sem_assinatura(conteudo))
    itens = []
    for det in _todos(root, 'det'):
        prod = next((c for c in det if c.tag.split('}')[-1] == 'prod'), None)
        imp = next((c for c in det if c.tag.split('}')[-1] == 'imposto'), None)
        di = next(iter(_todos(prod, 'DI')), None) if prod is not None else None
        ad = next(iter(_todos(prod, 'adi')), None) if prod is not None else None
        itens.append(dict(n=det.get('nItem'), cod=_t(prod, 'cProd'), desc=_t(prod, 'xProd'), ncm=_t(prod, 'NCM'), qtd=_t(prod, 'qCom'), unit=_t(prod, 'vUnCom'), vprod=_t(prod, 'vProd'),
                          voutro=_t(prod, 'vOutro'), vfrete=_t(prod, 'vFrete'), ad=_t(ad, 'nAdicao') or '1', fab=_t(ad, 'cFabricante'),
                          vII=_t(next(iter(_todos(imp, 'II')), None), 'vII') if imp is not None else '', pPIS=_t(next(iter(_todos(imp, 'PIS')), None), 'pPIS') if imp is not None else '',
                          vPIS=_t(next(iter(_todos(imp, 'PIS')), None), 'vPIS') if imp is not None else '', pCOFINS=_t(next(iter(_todos(imp, 'COFINS')), None), 'pCOFINS') if imp is not None else '',
                          vCOFINS=_t(next(iter(_todos(imp, 'COFINS')), None), 'vCOFINS') if imp is not None else '', vICMS=_t(next(iter(_todos(imp, 'ICMS')), None), 'vICMS') if imp is not None else '',
                          vAFRMM=_t(prod, 'vAFRMM'), vDespAdu=_t(prod, 'vDespAdu')))
    tot = next(iter(_todos(root, 'ICMSTot')), None)
    di0 = next(iter(_todos(root, 'DI')), None)
    return dict(nnf=_t(root, 'nNF'), emissao=_t(root, 'dhEmi') or _t(root, 'dEmi'), emitente=_t(next(iter(_todos(root, 'emit')), None), 'xNome'), nDI=_t(di0, 'nDI'), dDI=_t(di0, 'dDI'),
                local_desemb=_t(di0, 'xLocDesemb'), uf_desemb=_t(di0, 'UFDesemb'), d_desemb=_t(di0, 'dDesemb'),
                totais=dict(vProd=_t(tot, 'vProd'), vII=_t(tot, 'vII'), vPIS=_t(tot, 'vPIS'), vCOFINS=_t(tot, 'vCOFINS'), vICMS=_t(tot, 'vICMS'), vOutro=_t(tot, 'vOutro'), vFrete=_t(tot, 'vFrete'), vSeg=_t(tot, 'vSeg'),
                            vNF=_t(tot, 'vNF')),
                info_adic=_t(root, 'infCpl'), itens=itens)


# ---------------------------------------------------------------- leitura do extrato (PDF) - melhor esforco; tudo e conferido pelo usuario
def texto_pdf(conteudo: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # noqa: BLE001
        raise RuntimeError('Biblioteca pypdf não instalada neste computador. Rode: python -m pip install -r requirements.txt') from e
    r = PdfReader(io.BytesIO(conteudo))
    return '\n'.join((p.extract_text() or '') for p in r.pages)


def _achar(t, *padroes, grupo=1):
    for p in padroes:
        m = re.search(p, t, re.I | re.S)
        if m:
            return re.sub(r'\s+', ' ', m.group(grupo)).strip()
    return ''


def ler_extrato(texto: str) -> dict:
    n = _achar(texto, r'(\d{2}BR\d{10}-?\d)')
    return dict(
        numero=n.replace('-', ''),
        data_registro=_achar(texto, r'registr\w*[^\d]{0,40}(\d{2}/\d{2}/\d{4}[ \d:]*)', r'Data[^\d]{0,20}Registro[^\d]{0,20}(\d{2}/\d{2}/\d{4}[ \d:]*)'),
        taxa_dolar=_achar(texto, r'(?:taxa|cota[cç][aã]o)[^\d]{0,40}(\d[\d.,]{4,})'),
        navio=_achar(texto, r'navio[:\s]+([A-Z0-9][A-Z0-9 .\-]{2,40})'),
        bl=_achar(texto, r'(?:B/?L|conhecimento)[^\w]{0,20}(?:master)?[:\s]+([A-Z0-9]{6,25})'),
        manifesto=_achar(texto, r'manifesto[^\d]{0,15}(\d{8,})'),
        recinto=_achar(texto, r'recinto[^:]{0,20}:\s*([^\n]{3,60})'),
        incoterm=_achar(texto, r'incoterm[^A-Z]{0,20}(CFR|FOB|CIF|CPT|EXW|FCA|CIP|DAP|DPU|DDP)'),
        volumes=_achar(texto, r'volumes?[^\d]{0,15}(\d+)'),
        exportador=_achar(texto, r'exportador[^:\n]{0,30}:?\s*\n?\s*([A-Z][^\n]{3,80})'),
    )


# ---------------------------------------------------------------- calculo e geracao
def _distribui(itens, aliq_key, total, base_key='vad'):
    """Imposto por item pelo maior resto: a soma dos itens fecha exatamente com o total recolhido. Devolve {indice: valor} e os itens ajustados."""
    ex = [i[base_key] * i[aliq_key] for i in itens]
    fl = [(e * 100).to_integral_value(rounding=ROUND_FLOOR) / 100 for e in ex]
    falta = int((total - sum(fl)) * 100)
    ajustados = []
    ordem = sorted(range(len(itens)), key=lambda k: -(ex[k] - fl[k]))
    if falta > 0:
        for k in ordem[:falta]:
            fl[k] += CENT
            ajustados.append(k)
    return [q2(v) for v in fl], ex, ajustados


def _info(d, itens, cab) -> str:
    """Texto de INFOCOMPLEMENTARES no padrao da JMR."""
    cnt = ' / '.join(f'{c.strip()} 40HC' if '40HC' not in c.upper() else c.strip() for c in re.split(r'[;,\n]+', d.get('containers') or '') if c.strip())
    L = ['DOCUMENTOS QUE INSTRUEM ESTA DUIMP:', f'B/L MASTER: {d.get("bl", "")}' + (f'   HBL: {d["hbl"]}' if d.get('hbl') else ''), '.', f'FATURA: {d.get("fatura", "")}', '.', 'PACKING LIST: SEM NUMERO', '.',
         'TAXA(S) UTILIZADA(S):', f'US$: {d.get("taxa_dolar", "")} DE {d.get("data_taxa", "")}', '.', f'REFERENCIA DO CLIENTE: {d.get("exportador", "")} - {d.get("fatura", "")}', f'NOSSA REFERENCIA: {d.get("nossa_ref", "")}', '.',
         f'NAVIO: {d.get("navio", "")}', f'TRANSPORTADOR: {d.get("armador", "")}', '.', 'ROTEIRO DA VIAGEM: (PORTO/DATA/NAVIO)', f'{d.get("origem", "China")} - {d.get("data_embarque", "")} - {d.get("navio", "")}',
         f'{cab["local"]} - {d.get("data_chegada", "")} - {d.get("navio", "")}', '.', f'DATA DE OPERACAO: {d.get("data_operacao", "")}', '.', f'PRESENCA DE CARGA: CEMERCANTE{d.get("presenca_numero", "")} DATA: {d.get("presenca_data", "")}', '.',
         f'MANIFESTO {d.get("manifesto", "")}', '.', f'U.R.F. ENTRADA: PORTO DE {cab["local"].upper()}', f'RECINTO ALFANDEGADO: {d.get("recinto", "")}', '.', f'INCOTERM: {d.get("incoterm", "")}', '.',
         f'VOLUMES: {d.get("volumes", "")} CAIXAS', '.', f'CNTR.(S): {cnt}', '.', f'MERCADORIA:          US$  {br(cab["fob_usd"])}    R$  {br(cab["fob_brl"])}',
         f'FRETE:               US$  {br(d.get("frete_usd", 0) and num(d.get("frete_usd")))}  R$  {br(cab["frete"])}', '.', f'VALOR ADUANEIRO:                       R$  {br(cab["cif"])}', '.', 'DESPACHANTES CREDENCIADOS:'] + DESPACHANTES + \
        ['.', f'DESPESAS ADUANEIRAS (TRIBUTADAS): R$ {br(cab["desp"])}', '.', 'DEMONSTRATIVO DE CALCULO:', '.']
    for n, i in enumerate(itens, 1):
        L += [f'ITEM {n} NCM: {i["ncm"][:4]}.{i["ncm"][4:6]}.{i["ncm"][6:8]}', f'VALOR ADUANEIRO      :       R$ {br(i["vad"])}', f'DESPESAS ADUANEIRAS:         R$ {br(i["desp_item"])}',
              f'II                  ({pct_txt(i["al_II"] * 100):>5}%): R$ {br(i["II"])}', f'IPI                 (    0%): R$ {br(0)}', f'PIS                 ({pct_txt(i["al_PIS"] * 100):>5}%): R$ {br(i["PIS"])}',
              f'COFINS              ({pct_txt(i["al_COFINS"] * 100):>5}%): R$ {br(i["COFINS"])}', f'ICMS                ({pct_txt(i["al_ICMS"] * 100):>5}%): R$ {br(i["ICMS_item"])}', '.']
    L.append(f'ICMS A RECOLHER               R$ {br(cab["icms"])}')
    return '\n'.join(L)


def gerar(d: dict) -> dict:
    """d: formulario conferido pelo usuario (ver duimp_xml.html). Devolve {ok, xml, arquivo, erros, avisos, ajustes, pontos}."""
    erros, avisos, ajustes = [], [], []
    num_duimp = re.sub(r'[^0-9A-Za-z]', '', str(d.get('numero') or '')).upper()
    if len(num_duimp) != 15:
        erros.append(f'NUMERODUIMP precisa ter 15 caracteres sem hífen (tem {len(num_duimp)}).')
    itens_in = d.get('itens') or []
    if not itens_in:
        erros.append('Nenhum item informado.')
    al = dict(II=num(d.get('al_ii'), D('18')) / 100, PIS=num(d.get('al_pis'), D('2.10')) / 100, COFINS=num(d.get('al_cofins'), D('10.25')) / 100, ICMS=num(d.get('al_icms'), D('18')) / 100)
    itens = []
    for k, i in enumerate(itens_in, 1):
        ncm = re.sub(r'\D', '', str(i.get('ncm') or ''))
        if len(ncm) != 8:
            erros.append(f'Item {k}: NCM precisa ter 8 dígitos.')
        cod_orig = str(i.get('cod') or '').strip()
        itens.append(dict(ad=int(num(i.get('ad'), D('1'))), cod=re.sub(r'\D', '', cod_orig).zfill(6)[-6:] if re.sub(r'\D', '', cod_orig) else cod_orig, cod_orig=cod_orig, desc=str(i.get('desc') or '').strip(), ncm=ncm,
                          qtd=f'{num(i.get("qtd")):.5f}', unit=f'{num(i.get("unit")):.5f}', vad=q2(num(i.get('vad'))),
                          al_II=num(i.get('al_ii'), al['II'] * 100) / 100 if str(i.get('al_ii') or '').strip() else al['II'], al_PIS=num(i.get('al_pis'), al['PIS'] * 100) / 100 if str(i.get('al_pis') or '').strip() else al['PIS'],
                          al_COFINS=num(i.get('al_cofins'), al['COFINS'] * 100) / 100 if str(i.get('al_cofins') or '').strip() else al['COFINS'], al_ICMS=al['ICMS']))
    if erros:
        return dict(ok=False, erros=erros, avisos=avisos)
    tot = dict(II=q2(num(d.get('tot_ii'))), PIS=q2(num(d.get('tot_pis'))), COFINS=q2(num(d.get('tot_cofins'))))
    sisc, desp = q2(num(d.get('siscomex'))), q2(num(d.get('despesas')))
    frete, seguro = q2(num(d.get('frete'))), q2(num(d.get('seguro')))
    fob_usd, fob_brl = q2(num(d.get('fob_usd'))), q2(num(d.get('fob_brl')))
    cif = q2(num(d.get('cif'))) or (fob_brl + frete + seguro)
    icms_total = q2(num(d.get('icms_total')))
    # impostos por item fechando com o total recolhido (maior resto)
    for t in ('II', 'PIS', 'COFINS'):
        for it in itens:
            it['_al'] = it['al_' + t]
        valores, ex, ajust = _distribui(itens, 'al_' + t, tot[t])
        for it, v in zip(itens, valores):
            it[t] = v
        for k in ajust:
            ajustes.append(f'{t}: item {k + 1} recebeu +0,01 (valor puro {ex[k]:.4f} → {valores[k]}), para a soma dos itens fechar com o total recolhido de {tot[t]}.')
        soma = sum(valores)
        if soma != tot[t]:
            avisos.append(f'{t}: a soma dos itens ({soma}) não fecha com o total informado ({tot[t]}). Confira alíquotas e valores aduaneiros.')
    va_total = sum(i['vad'] for i in itens) or D('1')
    for i in itens:                                                        # Siscomex e despesas rateados pelo valor aduaneiro (so para o demonstrativo do texto)
        i['sisc_item'] = q2(sisc * i['vad'] / va_total)
        i['desp_item'] = q2(desp * i['vad'] / va_total)
        base = (i['vad'] + i['II'] + 0 + i['PIS'] + i['COFINS'] + i['sisc_item'] + i['desp_item']) / (1 - i['al_ICMS'])
        i['ICMS_item'] = q2(base * i['al_ICMS'])
    cab = dict(local=str(d.get('local') or 'Santos'), fob_usd=fob_usd, fob_brl=fob_brl, frete=frete, cif=cif, desp=desp, icms=icms_total)
    info = d.get('info') if str(d.get('info') or '').strip() else _info(d, itens, cab)
    f4 = lambda v: f'{D(v):.4f}'
    exp = escape(str(d.get('exportador') or '').strip())
    fab = escape(str(d.get('fabricante') or d.get('exportador') or '').strip())
    seq, partes = {}, []
    for n, i in enumerate(itens, 1):
        seq[i['ad']] = seq.get(i['ad'], 0) + 1
        partes.append(f"""    <ITEM IT="{n}">
      <NUMEROADICAO>{i['ad']:03d}</NUMEROADICAO>
      <NUMEROITEM>{seq[i['ad']]:04d}</NUMEROITEM>
      <CODIGO>{i['cod']}</CODIGO>
      <DESCRICAO>{escape(i['desc'])}</DESCRICAO>
      <NCM>{i['ncm']}</NCM>
      <UNIDADE>QUILOGRAMA</UNIDADE>
      <QUANTIDADE>{i['qtd']}</QUANTIDADE>
      <VALORUNITARIODOLAR>{i['unit']}</VALORUNITARIODOLAR>
      <ALIQUOTAPIS>{i['al_PIS'] * 100:.2f}</ALIQUOTAPIS>
      <VALORPIS>{i['PIS']}</VALORPIS>
      <ALIQUOTACOFINS>{i['al_COFINS'] * 100:.2f}</ALIQUOTACOFINS>
      <VALORCOFINS>{i['COFINS']}</VALORCOFINS>
      <ALIQUOTAIPI>0.00</ALIQUOTAIPI>
      <VALORIPI>0.00</VALORIPI>
      <ALIQUOTAII>{i['al_II'] * 100:.2f}</ALIQUOTAII>
      <VALORII>{i['II']}</VALORII>
      <EXPORTADOR>{exp}</EXPORTADOR>
      <FABRICANTE>{fab}</FABRICANTE>
    </ITEM>
""")
    xml = f"""<?xml version='1.0' encoding='utf-8'?>
<DUIMP>
  <NUMERODUIMP>{num_duimp}</NUMERODUIMP>
  <DATADUIMP>{escape(str(d.get('data_duimp') or '').strip())}</DATADUIMP>
  <DATADESEMBARACO>{escape(str(d.get('data_desembaraco') or '').strip())}</DATADESEMBARACO>
  <UFDESEMBARACO>{escape(str(d.get('uf') or 'SP').strip())}</UFDESEMBARACO>
  <LOCALDESEMBARACO>{escape(cab['local'])}</LOCALDESEMBARACO>
  <COTACAODOLAR>0.0000</COTACAODOLAR>
  <TAXASISCOMEX>{f4(sisc)}</TAXASISCOMEX>
  <DESPESASADUANEIRAS>{f4(desp)}</DESPESASADUANEIRAS>
  <FRETE>{f4(frete)}</FRETE>
  <SEGURO>{f4(seguro)}</SEGURO>
  <IPI>0.0000</IPI>
  <PIS>{f4(tot['PIS'])}</PIS>
  <COFINS>{f4(tot['COFINS'])}</COFINS>
  <II>{f4(tot['II'])}</II>
  <ICMS>{f4(icms_total)}</ICMS>
  <TOTALMATERIAISDOLAR>{f4(fob_usd)}</TOTALMATERIAISDOLAR>
  <TOTALMATERIAISREAIS>{f4(fob_brl)}</TOTALMATERIAISREAIS>
  <TOTALMATERIAISCIF>{f4(cif)}</TOTALMATERIAISCIF>
  <INFOCOMPLEMENTARES>{escape(info)}</INFOCOMPLEMENTARES>
  <PRODUTOS>
{''.join(partes)}  </PRODUTOS>
</DUIMP>
"""
    # validacao (mesmas conferencias da habilidade)
    r = ET.fromstring(xml.split('\n', 1)[1])
    its = list(r.find('PRODUTOS'))
    g = lambda e, t: D(e.findtext(t))
    if [c.tag for c in r] != CAB:
        erros.append('Ordem das tags do cabeçalho diferente do layout TOTVS.')
    if any([c.tag for c in i] != ITM for i in its):
        erros.append('Ordem das tags dos itens diferente do layout TOTVS.')
    for t in ('PIS', 'COFINS', 'II', 'IPI'):
        if g(r, t) != sum(g(i, 'VALOR' + t) for i in its):
            erros.append(f'{t} do cabeçalho ≠ soma dos itens.')
    if g(r, 'TOTALMATERIAISCIF') != g(r, 'TOTALMATERIAISREAIS') + g(r, 'FRETE') + g(r, 'SEGURO'):
        avisos.append(f'CIF ({g(r, "TOTALMATERIAISCIF")}) ≠ FOB R$ + frete + seguro ({g(r, "TOTALMATERIAISREAIS") + g(r, "FRETE") + g(r, "SEGURO")}). Confira o valor aduaneiro do extrato.')
    soma_usd = q2(sum(num(i['qtd']) * num(i['unit']) for i in itens))
    if abs(soma_usd - fob_usd) > D('0.05'):
        avisos.append(f'Quantidade × valor unitário dá US$ {soma_usd}, e o FOB informado é US$ {fob_usd} (diferença de mais de 5 centavos; se o incoterm é CFR, o unitário inclui o frete).')
    if sum(i['vad'] for i in itens) != cif:
        avisos.append(f'A soma dos valores aduaneiros dos itens ({sum(i["vad"] for i in itens)}) não é igual ao valor aduaneiro total ({cif}).')
    sem_desemb = not str(d.get('data_desembaraco') or '').strip()
    pontos = []
    if ajustes:
        pontos.append('Ajustes de centavos feitos: ' + ' | '.join(ajustes))
    if sem_desemb:
        pontos.append('DATADESEMBARACO está vazio (carga ainda em análise de risco). Preencha só depois de desembaraçada no extrato.')
    conv = [f'item {n}: {i["cod_orig"] or "(vazio)"} → {i["cod"]}' for n, i in enumerate(itens, 1) if i['cod_orig'] != i['cod']]
    if conv:
        pontos.append('Códigos de produto convertidos para 6 dígitos (conferir no cadastro SB1): ' + '; '.join(conv))
    pontos.append(f'Exportador/fabricante: {d.get("exportador") or "(vazio)"} / {d.get("fabricante") or "(o mesmo)"}. Se for novo ou diferente das importações anteriores, confira no cadastro do TOTVS com endereço completo (cidade, estado, país); fornecedor com município inválido é rejeitado.')
    pontos.append('Compare com o extrato e o DANFE: datas conflitantes (texto do despachante × campo estruturado da carga → use o campo estruturado), descrições truncadas, "fabricante desconhecido", <Signature> de outra empresa e erros de digitação da JMR.')
    pontos.append('ICMS por item do demonstrativo foi recalculado a 18% por dentro com Siscomex e despesas rateados pelo valor aduaneiro; o ICMS total do cabeçalho é o informado ("ICMS a recolher" do extrato).')
    return dict(ok=not erros, erros=erros, avisos=avisos, ajustes=ajustes, pontos=pontos, xml=xml, arquivo=f'DUIMP_-_{num_duimp}_TOTVS.xml', info=info)


# ---------------------------------------------------------------- rotas
@duimp_bp.route('/ler', methods=['POST'])
@login_required
def ler():
    _autorizar()
    f_xml, f_pdf = request.files.get('nfe'), request.files.get('extrato')
    if not f_xml or not f_xml.filename.lower().endswith('.xml'):
        return jsonify(ok=False, erro='Envie o XML da NF-e do despachante (.xml).'), 400
    try:
        nfe = ler_nfe(f_xml.read())
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, erro=f'Não consegui ler o XML da NF-e: {str(e)[:160]}'), 400
    ext, texto = {}, ''
    if f_pdf and f_pdf.filename:
        try:
            texto = texto_pdf(f_pdf.read())
            ext = ler_extrato(texto)
        except Exception as e:  # noqa: BLE001
            return jsonify(ok=False, erro=f'Não consegui ler o PDF do extrato: {str(e)[:200]}'), 400
    totais = nfe['totais']
    itens = [dict(ad=i['ad'] or '1', cod=i['cod'], desc=i['desc'], ncm=i['ncm'], qtd=i['qtd'], unit=i['unit'], vad=i['vprod'], al_pis=i['pPIS'], al_cofins=i['pCOFINS']) for i in nfe['itens']]
    form = dict(numero=ext.get('numero') or re.sub(r'\D', '', nfe['nDI']) and nfe['nDI'] or '', data_duimp=ext.get('data_registro') or '', data_desembaraco='', uf=nfe['uf_desemb'] or 'SP', local=nfe['local_desemb'] or 'Santos',
                siscomex=totais['vOutro'], despesas=str(sum(num(i['vDespAdu']) + num(i['vAFRMM']) for i in nfe['itens'])), frete=totais['vFrete'], seguro=totais['vSeg'], fob_usd='', fob_brl='', cif=totais['vProd'],
                icms_total=totais['vICMS'], tot_ii=totais['vII'], tot_pis=totais['vPIS'], tot_cofins=totais['vCOFINS'], al_ii='18', al_pis='2.10', al_cofins='10.25', al_icms='18',
                exportador='', fabricante='', taxa_dolar=ext.get('taxa_dolar', ''), navio=ext.get('navio', ''), bl=ext.get('bl', ''), manifesto=ext.get('manifesto', ''), recinto=ext.get('recinto', ''), incoterm=ext.get('incoterm', ''),
                volumes=ext.get('volumes', ''), fatura='', nossa_ref='', armador='', data_taxa='', data_embarque='', data_chegada='', data_operacao='', presenca_numero='', presenca_data='', containers='', frete_usd='')
    return jsonify(ok=True, form=form, itens=itens, nfe=dict(numero=nfe['nnf'], emissao=nfe['emissao'], emitente=nfe['emitente']), extrato_lido=bool(texto), texto_extrato=texto[:20000])


@duimp_bp.route('/gerar', methods=['POST'])
@login_required
def gerar_rota():
    _autorizar()
    d = request.get_json(silent=True) or {}
    try:
        return jsonify(gerar(d))
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, erros=[f'Não consegui gerar o XML: {str(e)[:200]}']), 400
