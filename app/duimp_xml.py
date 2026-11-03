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
    return num(x).quantize(CENT, ROUND_HALF_UP)


def br(x) -> str:
    """433702 -> 433.702,00"""
    return f'{q2(x):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def pct_txt(p) -> str:
    p = D(p)
    return (f'{p:.2f}'.rstrip('0').rstrip('.') if p == p.to_integral_value() else f'{p:.2f}'.rstrip('0')).replace('.', ',')


def _norm(t) -> str:
    return unicodedata.normalize('NFKD', str(t or '')).encode('ascii', 'ignore').decode().upper()


def norm_datahora(v) -> str:
    """dd/mm/aaaa hh:mm:ss (aceita dd/mm/aaaa hh:mm, ISO e data sozinha com 00:00:00). Vazio se nao reconhecer."""
    t = str(v or '').strip()
    m = re.match(r'(\d{2})/(\d{2})/(\d{4})(?:[ T,]+(\d{2}):(\d{2})(?::(\d{2}))?)?', t)
    if m:
        return f'{m.group(1)}/{m.group(2)}/{m.group(3)} {m.group(4) or "00"}:{m.group(5) or "00"}:{m.group(6) or "00"}'
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?', t)
    if m:
        return f'{m.group(3)}/{m.group(2)}/{m.group(1)} {m.group(4) or "00"}:{m.group(5) or "00"}:{m.group(6) or "00"}'
    return ''


def norm_data(v) -> str:
    x = norm_datahora(v)
    return x[:10] if x else ''


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
def _pypdf():
    """pypdf instalada no computador ou, se nao houver, a copia que acompanha o Gestao (app/_vendor): assim nao precisa instalar nada na VPS."""
    try:
        import pypdf
        return pypdf
    except ImportError:
        import os
        import sys
        raiz = os.path.join(os.path.dirname(__file__), '_vendor')
        if raiz not in sys.path:
            sys.path.insert(0, raiz)
        import pypdf
        return pypdf


def texto_pdf(conteudo: bytes) -> str:
    try:
        pypdf = _pypdf()
    except ImportError as e:  # noqa: BLE001
        raise RuntimeError('Não consegui carregar a leitura de PDF (pypdf).') from e
    r = pypdf.PdfReader(io.BytesIO(conteudo))
    return '\n'.join((p.extract_text() or '') for p in r.pages)


def _achar(t, *padroes, grupo=1):
    for p in padroes:
        m = re.search(p, t, re.I | re.S)
        if m:
            return re.sub(r'\s+', ' ', m.group(grupo)).strip()
    return ''


_LIXO_EXP = re.compile(r'\b(endere[cç]o|pa[ií]s|cidade|cnpj|cpf|fabricante|produtor|logradouro|c[oó]digo|tin|situa[cç][aã]o)\b.*$', re.I)


def _limpa_nome(v: str) -> str:
    v = re.sub(r'\s+', ' ', str(v or '')).strip(' :-;')
    v = _LIXO_EXP.sub('', v).strip(' :-;,')
    return v if len(v) >= 3 else ''


def achar_exportador(texto: str) -> tuple[str, str]:
    """(exportador, fabricante) lidos do extrato. Procura o rotulo (Exportador, Exportador estrangeiro, Fornecedor) e pega o nome na mesma linha ou na linha seguinte ('Nome:' / 'Razao social:')."""
    t = texto or ''
    exp = ''
    for pad in (r'Exportador(?:\s+Estrangeiro)?[^\n:]{0,25}:\s*([^\n]{3,140})',
                r'Exportador(?:\s+Estrangeiro)?[^\n]{0,25}\n\s*(?:Nome|Raz[aã]o\s+Social)?\s*:?\s*([^\n]{3,140})',
                r'Fornecedor(?:\s+Estrangeiro)?[^\n:]{0,25}:\s*([^\n]{3,140})',
                r'(?:Nome|Raz[aã]o\s+Social)\s*:\s*([A-Z0-9][^\n]{3,120})(?=[\s\S]{0,200}(?:Endere|Pa[ií]s))'):
        for m in re.finditer(pad, t, re.I):
            nome = _limpa_nome(m.group(1))
            if re.match(r'(?:Fornecedor|Fabricante|Endere[cç]o|Pa[ií]s|Nome\s*:?)\s*:', m.group(1).strip(), re.I) and not pad.startswith('Fornecedor'):
                continue
            if nome and not re.fullmatch(r'(?:n[aã]o informado|[-–]+|\d+)', nome, re.I):
                exp = nome
                break
        if exp:
            break
    fab = ''
    m = re.search(r'Fabricante(?:\s*/\s*Produtor)?[^\n:]{0,25}:\s*([^\n]{3,140})', t, re.I) or re.search(r'Fabricante(?:\s*/\s*Produtor)?[^\n]{0,25}\n\s*([^\n]{3,140})', t, re.I)
    if m:
        v = m.group(1)
        fab = exp if re.search(r'exportador|mesmo|pr[oó]prio', v, re.I) else _limpa_nome(v)
    return exp, fab


def limpa_pdf(t: str) -> str:
    """O PDF do extrato separa 'L' e 'T' por um espaco (L TDA, MUL TILOG, AL TO): junta de volta."""
    return re.sub(r'(?<=[A-Z]L) (?=T[A-Z])', '', re.sub(r'(?<=\bL) (?=T[A-Z])', '', t or ''))


def _apos(t: str, rotulo: str, ate_linha=True) -> str:
    """Valor que vem na linha seguinte ao rotulo ('Rotulo:\nvalor') ou na mesma linha ('ROTULO : valor')."""
    m = re.search(re.escape(rotulo) + r'\s*:?[ \t]*\n([^\n]+)', t, re.I) or re.search(re.escape(rotulo) + r'[ \t]*:[ \t]*([^\n]+)', t, re.I)
    return re.sub(r'\s+', ' ', m.group(1)).strip() if m else ''


def _nome_ope(v: str) -> str:
    """'OPE_21 - LANDI GROUP COMPANY LIMITED' -> 'LANDI GROUP COMPANY LIMITED'"""
    return re.sub(r'^\s*[A-Z]{2,4}_?\d+\s*-\s*', '', v or '').strip()


def ler_extrato(texto: str) -> dict:
    """Le o Extrato da DUIMP (PDF da Receita) no layout do Portal Unico: cabecalho, resumo de valores, adicoes (impostos) e itens (produto, NCM, fabricante, exportador, quantidade, valor)."""
    t = limpa_pdf(texto)
    if not t.strip():
        return {}
    n = _achar(t, r'Extrato da Duimp\s+(\d{2}BR\d{10}-?\d)', r'(\d{2}BR\d{10}-?\d)')
    sit = _apos(t, 'Situação da Duimp')
    reg = re.search(r'(\d{2}/\d{2}/\d{4}),?\s*\n?\s*(\d{2}:\d{2})(?::(\d{2}))?\s*\n?\s*Declara[cç][aã]o registrada', t, re.I)
    des = re.search(r'(\d{2}/\d{2}/\d{4}),?\s*\n?\s*\d{2}:\d{2}(?::\d{2})?\s*\n?\s*(?:Declara[cç][aã]o\s+)?desembara[cç]', t, re.I)
    num_ = lambda v: re.sub(r'[^\d,]', '', v).replace(',', '.') if v else ''
    fob = re.search(r'FOB\s*:\s*([\d.,]+)\s+USD:\s*([\d.,]+)\s+R\$\s*([\d.,]+)', t)
    fre = re.search(r'FRETE\s*:\s*([\d.,]+)\s+USD:\s*[\d.,]+\s+R\$\s*([\d.,]+)', t)
    va = re.search(r'VALOR AD\.\s*:\s*[\d.,]+\s+USD:\s*[\d.,]+\s+R\$\s*([\d.,]+)', t)
    # adicoes
    adicoes = []
    for bl in re.split(r'(?=Adi[cç][aã]o:\s*\d+\s+NCM)', t)[1:]:
        g = lambda pad: _achar(bl, pad)
        adicoes.append(dict(ad=g(r'Adi[cç][aã]o:\s*(\d+)'), ncm=g(r'NCM\s*:\s*(\d{8})'), va=num_(g(r'Valor Aduaneiro\s*:\s*R\$\s*([\d.,]+)')), seguro=num_(g(r'Total SEGURO\s*:\s*R\$\s*([\d.,]+)')),
                            al_ii=num_(g(r'I\.I\.\s*:\s*([\d.,]+)%')), ii=num_(g(r'I\.I\.\s*:[^\n]*R\$\s*([\d.,]+)')), ipi=num_(g(r'I\.P\s*\.I\.\s*:[^\n]*R\$\s*([\d.,]+)')),
                            desp=num_(g(r'Despesas Aduaneiras\s*:\s*R\$\s*([\d.,]+)')), siscomex=num_(g(r'Taxa Siscomex\s*:\s*R\$\s*([\d.,]+)')),
                            al_pis=num_(g(r'(?m)^PIS\s*:\s*([\d.,]+)%')), pis=num_(g(r'(?m)^PIS\s*:[^\n]*R\$\s*([\d.,]+)')), al_cofins=num_(g(r'(?m)^COFINS\s*:\s*([\d.,]+)%')), cofins=num_(g(r'(?m)^COFINS\s*:[^\n]*R\$\s*([\d.,]+)')),
                            base_icms=num_(g(r'Base de Calculo ICMS\s*:\s*R\$\s*([\d.,]+)')), al_icms=num_(g(r'(?m)^ICMS\s*:\s*([\d.,]+)%')), icms=num_(g(r'(?m)^ICMS\s*:[^\n]*R\$\s*([\d.,]+)'))))
    # itens
    itens = []
    for bl in re.split(r'(?=Extrato da Duimp[^\n]*:\s*Item\s+\d+)', t)[1:]:
        g = lambda rot: _apos(bl, rot)
        det = re.search(r'Detalhamento do Produto:\s*\n(.*?)\n\s*Descri[cç][aã]o complementar', bl, re.S | re.I)
        mcn = re.search(r'Código do produto:\s*\n(.*?)\nVers[aã]o:', bl, re.S)
        cod_nome = re.sub(r'\s+', ' ', mcn.group(1)).strip() if mcn else g('Código do produto')
        m = re.match(r'\s*(\d+)\s*-\s*(.+)', cod_nome)
        fab = _nome_ope(g('Código do Fabricante/Produtor'))
        exp = _nome_ope(g('Código do Exportador Estrangeiro'))
        rel = g('Relação entre exportador e fabricante/produtor')
        if re.search(r'[eé]\s+o\s+fabricante|mesmo|pr[oó]prio', rel, re.I) and not re.search(r'diferente', rel, re.I):
            fab = exp
        nome = re.sub(r'\s*-\s*[^-]*$', '', m.group(2).strip()) if (m and ' - ' in m.group(2)) else (m.group(2).strip() if m else '')
        detalhe = re.sub(r'\s+', ' ', det.group(1)).strip() if det else ''
        itens.append(dict(cod=m.group(1) if m else '', nome_produto=m.group(2).strip() if m else cod_nome, desc=(f'{nome} {detalhe}'.strip() if detalhe else (m.group(2).strip() if m else cod_nome)),
                          ncm=re.sub(r'\D', '', g('NCM'))[:8], fabricante=fab, exportador=exp, qtd=num_(g('Quantidade na unidade comercializada')), unit=num_(g('Valor unitário na condição de venda')),
                          total_usd=num_(g('Valor total na condição de venda')), relacao=rel))
    conts = re.findall(r'([A-Z]{4}\d{7})\s*-\s*SELO\s*[\w]*\s*-\s*(\d+)\s*CAIXAS', t)
    taxa = re.search(r'FOB\s*:[^\n]*USD:\s*([\d.,]+)', t)
    local = _achar(t, r'U\.R\.F\.\s*ENTRADA\s*:\s*([^\n]+)')
    chegada_est = _apos(t, 'Data/hora de chegada')
    mce = re.search(r'(?m)^Identifica[cç][aã]o da carga:\s*\n(\d{10,})', t)
    ce = mce.group(1) if mce else ''
    return dict(
        numero=n.replace('-', ''), situacao=sit, em_analise=bool(re.search(r'aguardando', sit, re.I)), data_registro=(f'{reg.group(1)} {reg.group(2)}:{reg.group(3) or "00"}' if reg else ''),
        data_desembaraco=(des.group(1) if des else ''), taxa_dolar=(taxa.group(1).replace('.', '').replace(',', '.') if taxa else ''),
        fob_usd=num_(fob.group(1)) if fob else '', fob_brl=num_(fob.group(3)) if fob else '', frete_usd=num_(fre.group(1)) if fre else '', frete=num_(fre.group(2)) if fre else '', cif=num_(va.group(1)) if va else '',
        siscomex=num_(_achar(t, r'TX\. SISCOMEX\s*:\s*([\d.,]+)')), despesas=str(sum(num(a['desp']) for a in adicoes)) if adicoes else '', seguro=str(sum(num(a['seguro']) for a in adicoes)) if adicoes else '0',
        tot_ii=str(sum(num(a['ii']) for a in adicoes)) if adicoes else '', tot_pis=str(sum(num(a['pis']) for a in adicoes)) if adicoes else '', tot_cofins=str(sum(num(a['cofins']) for a in adicoes)) if adicoes else '',
        icms_total=str(sum(num(a['icms']) for a in adicoes)) if adicoes else '',
        bl=_apos(t, 'B/L MASTER'), manifesto=_apos(t, 'Manifesto de Carga'), navio=_apos(t, 'NAVIO'), armador=_apos(t, 'NOME DO TRANSPORTADOR'), recinto=re.sub(r'^\d+\s*-\s*', '', _apos(t, 'RECINTO ALFANDEGADO')),
        data_embarque=_apos(t, 'DATA DO EMBARQUE'), data_chegada=(chegada_est[:10] if re.match(r'\d{2}/\d{2}/\d{4}', chegada_est) else _apos(t, 'DATA DE CHEGADA')), data_chegada_texto=_apos(t, 'DATA DE CHEGADA'),
        presenca_numero=ce or re.sub(r'^CEMERCANTE', '', _apos(t, 'PRESENCA DE CARGA')), presenca_data=(chegada_est[:10] if re.match(r'\d{2}/\d{2}/\d{4}', chegada_est) else ''),
        fatura=_apos(t, 'FATURA COMERCIAL'), ref_cliente=_apos(t, 'REF. CLIENTE'), nossa_ref=_apos(t, 'NOSSA REFERENCIA'), incoterm=_achar(t, r'TOTAL\s+(CFR|FOB|CIF|CPT|EXW|FCA|CIP|DAP|DPU|DDP)\b'),
        local=re.sub(r'^PORTO\s+DE\s+', '', local, flags=re.I).title() if local else '', volumes=str(sum(int(q) for _, q in conts)) if conts else '', containers=', '.join(c for c, _ in conts),
        exportador=(itens[0]['exportador'] if itens else ''), fabricante=(itens[0]['fabricante'] if itens else ''), adicoes=adicoes, itens=itens)


def ler_infcpl(txt: str) -> dict:
    """O despachante (JMR) escreve nas informacoes complementares da NF-e o mesmo texto que vai para o TOTVS (B/L, fatura, taxa, navio, manifesto, containers, valores...). Le cada campo pelo rotulo."""
    t = (txt or '').replace('\r', '')
    if 'DOCUMENTOS QUE INSTRUEM' not in t.upper():
        return {}
    g = lambda *pad: _achar(t, *pad)
    brn = lambda v: re.sub(r'[^\d,]', '', v).replace(',', '.') if v else ''
    rot = re.search(r'ROTEIRO DA VIAGEM[^\n]*\n([^\n]+)\n([^\n]+)', t, re.I)
    emb = re.search(r'-\s*(\d{2}/\d{2}/\d{4})\s*-', rot.group(1)) if rot else None
    che = re.search(r'-\s*(\d{2}/\d{2}/\d{4})\s*-', rot.group(2)) if rot else None
    ref = g(r'REFERENCIA DO CLIENTE:\s*([^\n]+)')
    fat = g(r'FATURA:\s*([^\n]+)')
    exportador = (ref[: -len(fat)].rstrip(' -').strip() if fat and ref.endswith(fat) else (ref.rsplit(' - ', 1)[0].strip() if ' - ' in ref else ref)) if ref else ''
    sisc_info = {}
    out = dict(
        bl=g(r'B/L MASTER:\s*([^\s\n]+)'), hbl=g(r'HBL:\s*([^\s\n]+)'), fatura=g(r'FATURA:\s*([^\n]+)'),
        taxa_dolar=g(r'US\$:\s*([\d.,]+)\s+DE'), data_taxa=g(r'US\$:\s*[\d.,]+\s+DE\s+(\d{2}/\d{2}/\d{4})'),
        exportador=exportador, nossa_ref=g(r'NOSSA REFERENCIA:\s*([^\n]+)'), navio=(re.search(r'^NAVIO:\s*(.+)$', t, re.M | re.I).group(1).strip() if re.search(r'^NAVIO:\s*(.+)$', t, re.M | re.I) else ''),
        armador=g(r'TRANSPORTADOR:\s*([^\n]+)'), data_embarque=emb.group(1) if emb else '', data_chegada=che.group(1) if che else '',
        data_operacao=g(r'DATA DE OPERACAO:\s*(\d{2}/\d{2}/\d{4})'), presenca_numero=g(r'CEMERCANTE\s*([\d]+)'), presenca_data=g(r'CEMERCANTE\s*\d+\s*DATA:\s*(\d{2}/\d{2}/\d{4})'),
        manifesto=g(r'MANIFESTO\s+([\w.\-]+)'), recinto=g(r'RECINTO ALFANDEGADO:\s*([^\n]+)'), incoterm=g(r'INCOTERM:\s*(\w+)'), volumes=g(r'VOLUMES:\s*(\d+)'),
        containers=', '.join(re.sub(r'\s*\d{2}\w{0,2}$', '', c.strip()).strip() for c in re.split(r'/', g(r'CNTR\.?\(S\):\s*([^\n]+)')) if c.strip()),
        fob_usd=brn(g(r'MERCADORIA:\s*US\$\s*([\d.,]+)')), fob_brl=brn(g(r'MERCADORIA:\s*US\$\s*[\d.,]+\s*R\$\s*([\d.,]+)')), frete_usd=brn(g(r'FRETE:\s*US\$\s*([\d.,]+)')),
        frete=brn(g(r'FRETE:\s*US\$\s*[\d.,]+\s*R\$\s*([\d.,]+)')), cif=brn(g(r'VALOR ADUANEIRO:\s*R\$\s*([\d.,]+)')), despesas=brn(g(r'DESPESAS ADUANEIRAS \(TRIBUTADAS\):\s*R\$\s*([\d.,]+)')),
        icms_total=brn(g(r'ICMS A RECOLHER\s*R\$\s*([\d.,]+)')))
    out['containers'] = ', '.join(re.sub(r'\s+40HC$', '', c.strip(), flags=re.I) for c in re.split(r'/', g(r'CNTR\.?\(S\):\s*([^\n]+)')) if c.strip())
    return {k: v for k, v in out.items() if v}


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


def _pi(a) -> str:
    v = D(a) * 100
    return f'{v:.0f}' if v == v.to_integral_value() else f'{v:.2f}'.replace('.', ',')


def _p2(a) -> str:
    return f'{D(a) * 100:.2f}'.replace('.', ',')


def _info(d, itens, cab) -> str:
    """Texto de INFOCOMPLEMENTARES no padrao da JMR."""
    cnt = ' / '.join(f'{c.strip()} 40HC' if '40HC' not in c.upper() else c.strip() for c in re.split(r'[;,\n]+', d.get('containers') or '') if c.strip())
    L = ['DOCUMENTOS QUE INSTRUEM ESTA DUIMP:', f'B/L MASTER: {d.get("bl", "")}' + (f'   HBL: {d["hbl"]}' if d.get('hbl') else ''), '.', f'FATURA: {d.get("fatura", "")}', '.', 'PACKING LIST: SEM NUMERO', '.',
         'TAXA(S) UTILIZADA(S):', f'US$: {d.get("taxa_dolar", "")} DE {d.get("data_taxa", "")}', '.', f'REFERENCIA DO CLIENTE: {d.get("exportador", "")} - {d.get("fatura", "")}', f'NOSSA REFERENCIA: {d.get("nossa_ref", "")}', '.',
         f'NAVIO: {d.get("navio", "")}', f'TRANSPORTADOR: {d.get("armador", "")}', '.', 'ROTEIRO DA VIAGEM: (PORTO/DATA/NAVIO)', f'{d.get("origem", "China")} - {d.get("data_embarque", "")} - {d.get("navio", "")}',
         f'{cab["local"]} - {d.get("data_chegada", "")} - {d.get("navio", "")}', '.', f'DATA DE OPERACAO: {d.get("data_operacao", "")}', '.', f'PRESENCA DE CARGA: CEMERCANTE{d.get("presenca_numero", "")} DATA: {d.get("presenca_data", "")}', '.',
         f'MANIFESTO {d.get("manifesto", "")}', '.', f'U.R.F. ENTRADA: PORTO DE {cab["local"].upper()}', f'RECINTO ALFANDEGADO: {d.get("recinto", "")}', '.', f'INCOTERM: {d.get("incoterm", "")}', '.',
         f'VOLUMES: {d.get("volumes", "")} CAIXAS', '.', f'CNTR.(S): {cnt}', '.', f'MERCADORIA:          US$  {br(cab["fob_usd"])}    R$  {br(cab["fob_brl"])}',
         f'FRETE:               US$  {br(num(d.get("frete_usd")))}  R$  {br(cab["frete"])}', '.', f'VALOR ADUANEIRO:                       R$  {br(cab["cif"])}', '.', 'DESPACHANTES CREDENCIADOS:'] + DESPACHANTES + \
        ['.', f'DESPESAS ADUANEIRAS (TRIBUTADAS): R$ {br(cab["desp"])}', '.', 'DEMONSTRATIVO DE CALCULO:', '.']
    for n, i in enumerate(itens, 1):
        L += [f'ITEM {n} NCM: {i["ncm"][:4]}.{i["ncm"][4:6]}.{i["ncm"][6:8]}', f'VALOR ADUANEIRO      :       R$ {br(i["vad"])}', f'DESPESAS ADUANEIRAS:         R$ {br(i["desp_item"])}',
              f'II                  ({_pi(i["al_II"]):>5}%): R$ {br(i["II"])}', f'IPI                 (    0%): R$ {br(0)}', f'PIS                 ({_p2(i["al_PIS"]):>5}%): R$ {br(i["PIS"])}',
              f'COFINS              ({_p2(i["al_COFINS"]):>5}%): R$ {br(i["COFINS"])}', f'ICMS                ({_pi(i["al_ICMS"]):>5}%): R$ {br(i["ICMS_item"])}', '.']
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
    if not str(d.get('exportador') or '').strip():
        erros.append('EXPORTADOR não informado: o nome do exportador vem do extrato da DUIMP (o TOTVS rejeita o XML sem ele). Preencha o campo Exportador.')
    data_duimp = norm_datahora(d.get('data_duimp'))
    if not data_duimp:
        erros.append('DATADUIMP inválida ou vazia: use a data/hora do registro do extrato (dd/mm/aaaa hh:mm:ss).')
    desemb_txt = str(d.get('data_desembaraco') or '').strip()
    data_desemb = norm_data(desemb_txt) if desemb_txt else ''
    if desemb_txt and not data_desemb:
        erros.append('DATADESEMBARACO inválida (use dd/mm/aaaa) ou deixe vazia se a carga ainda está em análise.')
    uf = str(d.get('uf') or '').strip().upper()
    if not re.fullmatch(r'[A-Z]{2}', uf):
        erros.append('UFDESEMBARACO precisa ter 2 letras (ex.: SP, SC).')
    if not str(d.get('local') or '').strip():
        erros.append('LOCALDESEMBARACO não informado (ex.: Santos, Itajaí).')
    al = dict(II=num(d.get('al_ii'), D('18')) / 100, PIS=num(d.get('al_pis'), D('2.10')) / 100, COFINS=num(d.get('al_cofins'), D('10.25')) / 100, ICMS=num(d.get('al_icms'), D('18')) / 100)
    itens = []
    for k, i in enumerate(itens_in, 1):
        ncm = re.sub(r'\D', '', str(i.get('ncm') or ''))
        if len(ncm) != 8:
            erros.append(f'Item {k}: NCM precisa ter 8 dígitos.')
        if not str(i.get('desc') or '').strip():
            erros.append(f'Item {k}: descrição vazia (use a descrição completa do extrato).')
        if not re.sub(r'\D', '', str(i.get('cod') or '')):
            erros.append(f'Item {k}: código do produto vazio.')
        if num(i.get('qtd')) <= 0 or num(i.get('unit')) <= 0 or num(i.get('vad')) <= 0:
            erros.append(f'Item {k}: quantidade, valor unitário e valor aduaneiro precisam ser maiores que zero.')
        cod_orig = str(i.get('cod') or '').strip()
        itens.append(dict(ad=int(num(i.get('ad'), D('1'))), cod=re.sub(r'\D', '', cod_orig).zfill(6)[-6:] if re.sub(r'\D', '', cod_orig) else cod_orig, cod_orig=cod_orig, desc=str(i.get('desc') or '').strip(), ncm=ncm,
                          qtd=f'{num(i.get("qtd")):.5f}', unit=f'{num(i.get("unit")):.5f}', vad=q2(num(i.get('vad'))),
                          al_II=num(i.get('al_ii'), al['II'] * 100) / 100 if str(i.get('al_ii') or '').strip() else al['II'], al_PIS=num(i.get('al_pis'), al['PIS'] * 100) / 100 if str(i.get('al_pis') or '').strip() else al['PIS'],
                          al_COFINS=num(i.get('al_cofins'), al['COFINS'] * 100) / 100 if str(i.get('al_cofins') or '').strip() else al['COFINS'], al_ICMS=al['ICMS'],
                          exp=str(i.get('exp') or d.get('exportador') or '').strip(), fab=str(i.get('fab') or d.get('fabricante') or i.get('exp') or d.get('exportador') or '').strip()))
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
            if valores[k] != q2(ex[k]):                                  # so conta como ajuste quando difere do arredondamento normal
                ajustes.append(f'{t}: item {k + 1} recebeu +0,01 (valor puro {ex[k]:.4f} → {valores[k]}), para a soma dos itens fechar com o total recolhido de {tot[t]}.')
        soma = sum(valores)
        if soma != tot[t]:
            avisos.append(f'{t}: a soma dos itens ({soma}) não fecha com o total informado ({tot[t]}). Confira alíquotas e valores aduaneiros (se a DUIMP tem NCMs com alíquotas diferentes, por exemplo COFINS 9,65% e 10,25%, preencha a alíquota de cada item na tabela).')
    va_total = sum(i['vad'] for i in itens) or D('1')
    for i in itens:                                                        # Siscomex e despesas rateados pelo valor aduaneiro (so para o demonstrativo do texto)
        i['sisc_item'] = q2(sisc * i['vad'] / va_total)
        i['desp_item'] = q2(desp * i['vad'] / va_total)
        base = (i['vad'] + i['II'] + 0 + i['PIS'] + i['COFINS'] + i['sisc_item'] + i['desp_item']) / (1 - i['al_ICMS'])
        i['ICMS_item'] = q2(base * i['al_ICMS'])
    cab = dict(local=str(d.get('local') or 'Santos'), fob_usd=fob_usd, fob_brl=fob_brl, frete=frete, cif=cif, desp=desp, icms=icms_total)
    info = d.get('info') if str(d.get('info') or '').strip() else _info(d, itens, cab)
    f4 = lambda v: f'{D(v):.4f}'
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
      <EXPORTADOR>{escape(i['exp'])}</EXPORTADOR>
      <FABRICANTE>{escape(i['fab'])}</FABRICANTE>
    </ITEM>
""")
    xml = f"""<?xml version='1.0' encoding='utf-8'?>
<DUIMP>
  <NUMERODUIMP>{num_duimp}</NUMERODUIMP>
  <DATADUIMP>{data_duimp}</DATADUIMP>
  <DATADESEMBARACO>{data_desemb}</DATADESEMBARACO>
  <UFDESEMBARACO>{uf}</UFDESEMBARACO>
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
        erros.append(f'CIF ({g(r, "TOTALMATERIAISCIF")}) ≠ FOB R$ + frete + seguro ({g(r, "TOTALMATERIAISREAIS") + g(r, "FRETE") + g(r, "SEGURO")}). O TOTVS confere essa soma: confira FOB, frete e valor aduaneiro no extrato.')
    for n, (i, ent) in enumerate(zip(itens, itens_in), 1):
        for t, ref in (('II', 'ref_ii'), ('PIS', 'ref_pis'), ('COFINS', 'ref_cofins')):
            if str(ent.get(ref) or '').strip() and q2(num(ent.get(ref))) != i[t]:
                ajustes.append(f'{t} item {n}: NF-e da JMR {q2(num(ent.get(ref)))} × usado {i[t]} (o total do cabeçalho segue o extrato).')
    soma_usd = q2(sum(num(i['qtd']) * num(i['unit']) for i in itens))
    esperado = fob_usd + (q2(num(d.get('frete_usd'))) if str(d.get('incoterm') or '').upper() in ('CFR', 'CIF', 'CPT', 'CIP') else D('0'))
    if abs(soma_usd - esperado) > D('0.05'):
        avisos.append(f'Quantidade × valor unitário dá US$ {soma_usd}, e o esperado ({d.get("incoterm") or "FOB"}: FOB' + (' + frete' if esperado != fob_usd else '') + f') é US$ {esperado}. Diferença de mais de 5 centavos: confira quantidade e valor unitário.')
    elif soma_usd != esperado:
        avisos.append(f'Quantidade × valor unitário dá US$ {soma_usd} contra US$ {esperado} do extrato ({soma_usd - esperado}): diferença de centavos pelas 5 casas do valor unitário.')
    dif_va = sum(i['vad'] for i in itens) - cif
    if abs(dif_va) > D('0.05'):
        erros.append(f'A soma dos valores aduaneiros dos itens ({sum(i["vad"] for i in itens)}) difere do valor aduaneiro total ({cif}) em {dif_va}. Confira os itens.')
    elif dif_va != 0:
        avisos.append(f'A soma dos valores aduaneiros dos itens difere {dif_va} do total ({cif}): ajuste de centavos; confira.')
    sem_desemb = not data_desemb
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
    return dict(ok=not erros, erros=erros, avisos=avisos, ajustes=ajustes, pontos=pontos, xml=(None if erros else xml), arquivo=f'DUIMP_-_{num_duimp}_TOTVS.xml', info=info)


# ---------------------------------------------------------------- rotas
def montar_formulario(nfe: dict, ext: dict) -> dict:
    """Junta NF-e (itens, valores aduaneiros e impostos por item) e extrato (fonte da verdade: cabecalho, fabricante, exportador, codigos, descricoes). Devolve {form, itens, vazios, lidos}."""
    tot = nfe['totais']
    n_ref = re.search(r'N/REF:\s*(\S+)', nfe.get('info_adic') or '')
    s_ref = re.search(r'S/REF:\s*(\S+)', nfe.get('info_adic') or '')
    di = nfe.get('nDI') or ''
    f = dict(numero=ext.get('numero') or di, data_duimp=ext.get('data_registro') or '', data_desembaraco=('' if ext.get('em_analise', True) else (ext.get('data_desembaraco') or '')),
             uf=nfe['uf_desemb'] or 'SP', local=ext.get('local') or re.sub(r'^PORTO\s+DE\s+', '', nfe['local_desemb'] or 'Santos', flags=re.I).title(),
             siscomex=ext.get('siscomex') or tot['vOutro'], despesas=ext.get('despesas') or str(sum(num(i['vAFRMM']) + num(i['vDespAdu']) for i in nfe['itens'])), frete=ext.get('frete') or tot['vFrete'],
             seguro=ext.get('seguro') or tot['vSeg'], fob_usd=ext.get('fob_usd', ''), fob_brl=ext.get('fob_brl', ''), cif=ext.get('cif') or tot['vProd'], icms_total=ext.get('icms_total') or tot['vICMS'],
             tot_ii=ext.get('tot_ii') or tot['vII'], tot_pis=ext.get('tot_pis') or tot['vPIS'], tot_cofins=ext.get('tot_cofins') or tot['vCOFINS'],
             al_ii=(ext.get('adicoes') or [{}])[0].get('al_ii') or '18', al_pis=(ext.get('adicoes') or [{}])[0].get('al_pis') or '2.10', al_cofins=(ext.get('adicoes') or [{}])[0].get('al_cofins') or '10.25',
             al_icms=(ext.get('adicoes') or [{}])[0].get('al_icms') or '18', exportador=ext.get('exportador', ''), fabricante=ext.get('fabricante', ''), taxa_dolar=ext.get('taxa_dolar', ''),
             data_taxa=(ext.get('data_registro') or '')[:10], navio=ext.get('navio', ''), armador=ext.get('armador', ''), bl=ext.get('bl', ''), manifesto=ext.get('manifesto', ''), recinto=ext.get('recinto', ''),
             incoterm=ext.get('incoterm', ''), volumes=ext.get('volumes', ''), fatura=ext.get('fatura') or (s_ref.group(1) if s_ref else ''), nossa_ref=(n_ref.group(1) if n_ref else ext.get('nossa_ref', '')),
             data_embarque=ext.get('data_embarque', ''), data_chegada=ext.get('data_chegada', ''), data_operacao=(ext.get('data_registro') or '')[:10], presenca_numero=ext.get('presenca_numero', ''),
             presenca_data=ext.get('presenca_data', ''), containers=ext.get('containers', ''), frete_usd=ext.get('frete_usd', ''), origem='China', info='')
    # complementos pelo texto da NF-e (modelo antigo da JMR, quando existir)
    cpl = ler_infcpl(nfe.get('info_adic') or '')
    for k, v in cpl.items():
        if k in f and not str(f.get(k) or '').strip():
            f[k] = v
    if cpl:
        f['info'] = nfe['info_adic']
    itens = []
    eit, adics = ext.get('itens') or [], ext.get('adicoes') or []
    base = eit or [None] * len(nfe['itens'])
    tot_usd = sum(num(i.get('total_usd')) for i in eit) or D('1')
    for k, e in enumerate(base):
        nit = nfe['itens'][k] if k < len(nfe['itens']) else {}
        ncm = (e or {}).get('ncm') or nit.get('ncm') or ''
        adic = next((a for a in adics if a.get('ncm') == ncm), adics[0] if adics else {})
        va = nit.get('vprod') or ''
        if not va and e and adic.get('va'):
            va = str(q2(num(adic['va']) * num(e.get('total_usd')) / tot_usd))
        itens.append(dict(ad=str(int(num(nit.get('ad') or adic.get('ad') or '1'))) if (nit.get('ad') or adic.get('ad')) else '1', cod=(e or {}).get('cod') or nit.get('cod') or '', desc=(e or {}).get('desc') or nit.get('desc') or '', ncm=ncm,
                          qtd=(e or {}).get('qtd') or nit.get('qtd') or '', unit=(e or {}).get('unit') or nit.get('unit') or '', vad=va, al_pis=adic.get('al_pis') or nit.get('pPIS') or '', al_cofins=adic.get('al_cofins') or nit.get('pCOFINS') or '',
                          exp=(e or {}).get('exportador') or '', fab=(e or {}).get('fabricante') or '', ref_ii=nit.get('vII', ''), ref_pis=nit.get('vPIS', ''), ref_cofins=nit.get('vCOFINS', '')))
    conferir = []
    if ext.get('data_chegada_texto') and ext.get('data_chegada') and ext['data_chegada_texto'] != ext['data_chegada']:
        conferir.append(f'Data de chegada: o texto do despachante diz {ext["data_chegada_texto"]} e o campo estruturado da carga diz {ext["data_chegada"]}. Usei o campo estruturado.')
    if ext.get('em_analise') and nfe.get('d_desemb'):
        conferir.append(f'A NF-e da JMR traz data de desembaraço ({nfe["d_desemb"]}), mas o extrato diz "{ext.get("situacao")}". Deixei DATADESEMBARACO vazio.')
    if any('desconhecido' in str(i.get('fab') or '').lower() for i in nfe['itens']):
        conferir.append('A NF-e da JMR traz "Fabricante desconhecido": usei o fabricante do extrato.')
    for k, i in enumerate(itens, 1):
        nd = (nfe['itens'][k - 1]['desc'] if k - 1 < len(nfe['itens']) else '')
        if nd and len(i['desc']) > len(nd) + 5:
            conferir.append(f'Item {k}: a descrição da NF-e da JMR está truncada ({len(nd)} caracteres); usei a descrição completa do extrato.')
        if i['exp'] and i['fab'] and i['exp'] != i['fab']:
            conferir.append(f'Item {k}: exportador ({i["exp"]}) diferente do fabricante ({i["fab"]}): confira os dois no cadastro do TOTVS, com endereço completo.')
        if i['cod'] and i['cod'].isdigit() and len(i['cod']) < 6:
            conferir.append(f'Item {k}: código do produto {i["cod"]} do extrato vira {i["cod"].zfill(6)}: confira no cadastro (SB1).')
    if ext.get('data_registro'):
        conferir.append('O extrato mostra a hora do registro só até os minutos: DATADUIMP segue com segundos 00. A data da taxa do dólar e a data de operação do texto complementar foram preenchidas com a data do registro; confira com o despachante.')
    if ext.get('numero') and ext.get('presenca_numero') and ext.get('presenca_numero') not in (ext.get('texto_ce') or ext.get('presenca_numero')):
        conferir.append('Presença de carga: o número do texto do despachante é diferente do campo estruturado da carga; usei o campo estruturado.')
    vazios = [k for k in ('numero', 'data_duimp', 'fob_usd', 'fob_brl', 'cif', 'exportador', 'fabricante', 'bl', 'navio', 'taxa_dolar') if not str(f.get(k) or '').strip()]
    lidos = [k for k, v in f.items() if str(v or '').strip() and ext.get(k)]
    return dict(form=f, itens=itens, vazios=vazios, lidos=lidos, conferir=conferir)


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
    ext, texto, aviso = {}, '', ''
    if f_pdf and f_pdf.filename:
        try:
            texto = texto_pdf(f_pdf.read())
            ext = ler_extrato(texto)
            if not ext.get('numero'):
                aviso = 'O PDF foi lido, mas não reconheci o layout do Extrato da DUIMP (Portal Único). Confira se é o extrato e preencha o que faltar.'
        except Exception as e:  # noqa: BLE001
            aviso = f'Não consegui ler o PDF do extrato ({str(e)[:160]}). Os dados da NF-e foram carregados; preencha o restante olhando o extrato.'
    else:
        aviso = 'Extrato não enviado: sem ele faltam exportador, fabricante, código do produto, datas e valores em dólar.'
    m = montar_formulario(nfe, ext)
    return jsonify(ok=True, form=m['form'], itens=m['itens'], nfe=dict(numero=nfe['nnf'], emissao=nfe['emissao'], emitente=nfe['emitente']), extrato_lido=bool(ext.get('numero')), texto_extrato=texto[:20000],
                   aviso=aviso, lidos_da_nfe=m['lidos'], vazios=m['vazios'], conferir=m['conferir'])


@duimp_bp.route('/gerar', methods=['POST'])
@login_required
def gerar_rota():
    _autorizar()
    d = request.get_json(silent=True) or {}
    try:
        return jsonify(gerar(d))
    except Exception as e:  # noqa: BLE001
        import traceback
        print(traceback.format_exc(), flush=True)
        return jsonify(ok=False, erros=[f'Não consegui gerar o XML: {type(e).__name__} {str(e)[:200]}. Confira se algum campo numérico ficou com texto.']), 400
