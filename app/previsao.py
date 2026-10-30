"""Previsao de termino de cada OP na maquina, pelas voltas e RPM (mesma conta da planilha producaoDIARIA).

  tempo da peca (min) = voltas / RPM
  minutos uteis por dia = 1440 x eficiencia                     (padrao 80 % -> 1152 min)
  pecas em 24 h        = minutos uteis / tempo da peca
  dias para terminar   = pecas que faltam / pecas em 24 h

As OPs de cada maquina andam na ordem da fila: a OP produzindo comeca agora (descontando o que ja foi produzido) e cada
proxima comeca quando a anterior termina, pulando os dias sem producao (feriados e paradas). O resultado vai para
Sequencia.termino_malharia (Termino Malharia) e Sequencia.pecas_dia. Voltas e RPM vem da base (ProducaoRef):
1) da propria OP, 2) do ultimo registro da maquina+artigo, 3) do ultimo registro do artigo. Sem base: usa Raport x RPM instalado (ver abaixo); sem isso, mantem o termino do PCP Hub.

Raport (cm de tecido por volta): metros por dia = raport/100 x RPM instalado x eficiencia (80 %) x 1440; voltas da peca = metros da peca x 100 / raport.
O raport vem da estrutura (CM, XN, CF, PL, XP, NV), com excecao por maquina (ex.: maquinas 4 e 8 em CM = 4,0 cm); o RPM instalado e da maquina."""
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import current_app

from app import db
from app.models import (ArtigoTamanho, FichaArtigo, Machine, MaquinaRpm, NonWorkingDay, ProducaoRef, RaportMaquinaEstrutura, RaportEstrutura, Sequencia)

EFICIENCIA = min(max(float(os.getenv('EFICIENCIA_MAQUINA', '0.80') or 0.80), 0.3), 1.0)      # a maquina rende 80 % do teorico (RPM instalado x eficiencia); EFICIENCIA_MAQUINA=0.85 no .env volta para 85 %
INEFICIENCIA = 1 - EFICIENCIA
MIN_DIA = 1440 * EFICIENCIA
TROCA_HORAS = float(os.getenv('TROCA_HORAS', '2'))      # tempo de troca de artigo somado antes de cada OP que ainda nao esta produzindo (padrao 2 h)
ATIVAS = ('PRODUZINDO', 'EM_SEQUENCIA', 'PCP')
_trava = threading.Lock()
_arquivo_lido = {'mtime': None}


def _num(v):
    try:
        return float(str(v).replace('.', '').replace(',', '.')) if isinstance(v, str) and ',' in v else float(v)
    except (TypeError, ValueError):
        return None


def caminho_base() -> Path:
    """Onde esta a planilha producaoDIARIA: PRODUCAO_DIARIA_XLSX (.env), senao a primeira que existir na pasta do projeto ou em instance/
    (producaoDIARIA*.xlsx / producao_diaria*.xlsx, maiuscula ou minuscula). Se nao houver nenhuma, devolve instance/producao_diaria.xlsx (onde o botao grava)."""
    env = os.getenv('PRODUCAO_DIARIA_XLSX')
    if env:
        return Path(env)
    pastas = [Path(current_app.root_path).parent, Path(current_app.instance_path)]
    for pasta in pastas:
        try:
            achados = [f for f in pasta.iterdir() if f.is_file() and re.match(r'(?i)^producao[_ ]?diaria.*\.xls[xm]$', f.name) and not f.name.startswith('~$')]
        except OSError:
            continue
        if achados:
            return max(achados, key=lambda f: f.stat().st_mtime)      # o mais recente
    return Path(current_app.instance_path) / 'producao_diaria.xlsx'


def importar(path) -> int:
    """Le a planilha producaoDIARIA (colunas numOp, Maquina, Artigo, Estrutura, metrosPeca, RPM, Voltas, tamVolta, Criado) e troca o conteudo de producao_ref."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    linhas = ws.iter_rows(values_only=True)
    cab = [str(c or '').strip() for c in next(linhas)]
    ix = {n: i for i, n in enumerate(cab)}
    obrig = ('Maquina', 'Artigo', 'RPM', 'Voltas')
    faltam = [c for c in obrig if c not in ix]
    if faltam:
        raise ValueError(f'Planilha sem as colunas: {", ".join(faltam)}')
    novas = []
    for r in linhas:
        ref = _ref_de_valores(r[ix['numOp']] if 'numOp' in ix else None, r[ix['Maquina']], r[ix['Artigo']], r[ix['Estrutura']] if 'Estrutura' in ix else None,
                              r[ix['RPM']], r[ix['Voltas']], r[ix['tamVolta']] if 'tamVolta' in ix else None, r[ix['metrosPeca']] if 'metrosPeca' in ix else None,
                              r[ix['Criado']] if 'Criado' in ix else None)
        if ref:
            novas.append(ref)
    return _substituir_base(novas)


def _ref_de_valores(op, maquina, artigo, estrutura, rpm, voltas, tam_volta, metros_peca, criado):
    maq, rpm, voltas = _num(maquina), _num(rpm), _num(voltas)
    artigo = str(artigo or '').strip()
    if maq is None or not artigo or not rpm or not voltas or rpm <= 0 or voltas <= 0:
        return None
    return ProducaoRef(op=str(op or '').strip() or None, maquina=int(maq), artigo=artigo, estrutura=str(estrutura or '').strip() or None, rpm=rpm, voltas=voltas,
                       tam_volta=_num(tam_volta), metros_peca=_num(metros_peca), referencia=criado if isinstance(criado, datetime) else None)


def _substituir_base(novas: list) -> int:
    if not novas:
        raise ValueError('A base nao tem linhas validas (maquina, artigo, RPM e voltas).')
    try:
        ProducaoRef.query.delete()
        db.session.bulk_save_objects(novas)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return len(novas)


# ---- leitura automatica da lista producaoDIARIA no SharePoint (Graph, so leitura) ------------------------------------------
SP_MIN = float(os.getenv('PRODUCAODIARIA_MIN', '10'))
ESTADO_BASE = dict(fonte=None, sharepoint_ultima=None, sharepoint_erro=None, sharepoint_linhas=0)
_ult_sp = [0.0]


def sincronizar_sharepoint() -> int:
    """Le a lista producaoDIARIA (site Colchaonacional) pelo Graph e troca a base. Colunas: numOp, Maquina, Artigo, Estrutura, metrosPeca, RPM, Voltas, tamVolta."""
    from app.indicadores import sharepoint as sp
    host = os.getenv('PRODUCAODIARIA_HOST', 'conexaomalhasedes.sharepoint.com')
    site_path = os.getenv('PRODUCAODIARIA_SITE', 'Colchaonacional')
    lista_nome = os.getenv('PRODUCAODIARIA_LISTA', 'producaoDIARIA')
    site = os.getenv('PRODUCAODIARIA_SITE_ID') or sp._get(f'{sp.GRAPH}/sites/{host}:/sites/{site_path}')['id']
    lista = os.getenv('PRODUCAODIARIA_LISTA_ID')
    if not lista:
        for l in sp._get(f'{sp.GRAPH}/sites/{site}/lists', {'$select': 'id,displayName,name', '$top': 200}).get('value') or []:
            if lista_nome.lower() in (str(l.get('displayName') or '').lower(), str(l.get('name') or '').lower()):
                lista = l['id']
                break
    if not lista:
        raise RuntimeError(f'Lista "{lista_nome}" nao encontrada no site {site_path}.')
    nome_de = {}                                               # titulo da coluna -> nome interno
    for c in sp._get(f'{sp.GRAPH}/sites/{site}/lists/{lista}/columns').get('value') or []:
        nome_de[str(c.get('displayName') or '').lower()] = c.get('name')
        nome_de[str(c.get('name') or '').lower()] = c.get('name')

    def campo(f, titulo):
        return f.get(nome_de.get(titulo.lower(), titulo))

    novas, url, params = [], f'{sp.GRAPH}/sites/{site}/lists/{lista}/items', {'$expand': 'fields', '$top': 999}
    while url and len(novas) < 60000:
        j = sp._get(url, params)
        for it in j.get('value') or []:
            f = it.get('fields') or {}
            criado = None
            try:
                criado = datetime.fromisoformat(str(it.get('createdDateTime') or '').replace('Z', '+00:00')).replace(tzinfo=None)
            except ValueError:
                pass
            ref = _ref_de_valores(campo(f, 'numOp'), campo(f, 'Maquina'), campo(f, 'Artigo'), campo(f, 'Estrutura'), campo(f, 'RPM'), campo(f, 'Voltas'),
                                  campo(f, 'tamVolta'), campo(f, 'metrosPeca'), criado)
            if ref:
                novas.append(ref)
        url, params = j.get('@odata.nextLink'), None
    return _substituir_base(novas)


# ---- ficha tecnica do artigo (lista basedeficha, site Conexopreo): RPM e voltas por peca do registro mais recente de cada artigo -------------------------------
FONTE = os.getenv('VOLTAS_RPM_FONTE', 'raport')              # 'raport' (padrao): so RPM instalado x eficiencia + Raport da planilha; 'ficha': ficha do SharePoint, producaoDIARIA e, por ultimo, o Raport
FICHA_MIN = float(os.getenv('FICHA_MIN', '30'))
FICHA_SITE_ID = os.getenv('FICHA_SITE_ID', 'conexaomalhasedes.sharepoint.com,755eaebb-3bfb-4682-8e31-d394a0dbbf23,fb853ab7-5f02-463c-a857-841348a3b2a9')
FICHA_LISTA_ID = os.getenv('FICHA_LISTA_ID', 'be6ae3ff-f4ce-42da-b316-9efd0459a14a')
ESTADO_FICHA = dict(ultima=None, erro=None, artigos=0, registros=0)
_ult_ficha = [0.0]


def _flt(v):
    try:
        x = float(str(v).replace(',', '.').strip())
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def sincronizar_ficha() -> int:
    """GET /sites/{site}/lists/{basedeficha}/items?$expand=fields($select=Artigo,Totvs,Cliente,RPM,Voltasporpe_x00e7_a,STATUS,DATA) (so leitura).
    Para cada artigo fica o registro mais recente (por DATA) com RPM e voltas validos; registros 'Desatualizado' so entram se nao houver outro."""
    from app.indicadores import sharepoint as sp
    url, params, linhas = f'{sp.GRAPH}/sites/{FICHA_SITE_ID}/lists/{FICHA_LISTA_ID}/items', {'$expand': 'fields($select=Artigo,Totvs,Cliente,RPM,Voltasporpe_x00e7_a,STATUS,DATA)', '$top': 999}, []
    while url:
        j = sp._get(url, params)
        for it in j.get('value') or []:
            f = it.get('fields') or {}
            artigo = str(f.get('Artigo') or '').strip()
            rpm, voltas = _flt(f.get('RPM')), _flt(f.get('Voltasporpe_x00e7_a'))
            if not artigo or not rpm or not voltas:
                continue
            try:
                data = datetime.fromisoformat(str(f.get('DATA') or '').replace('Z', '+00:00')).date()
            except ValueError:
                data = None
            linhas.append(dict(artigo=artigo, totvs=str(f.get('Totvs') or '') or None, cliente=f.get('Cliente'), rpm=rpm, voltas=voltas, status=str(f.get('STATUS') or ''),
                               data=data, criado=str(it.get('createdDateTime') or '')))
        url, params = j.get('@odata.nextLink'), None
    melhor = {}
    for l in linhas:
        k = l['artigo'].upper()
        chave = (l['status'] != 'Desatualizado', str(l['data'] or ''), l['criado'])          # o mais recente; 'Desatualizado' so se nao houver outro
        if k not in melhor or chave > melhor[k][0]:
            melhor[k] = (chave, l)
    if not melhor:
        raise ValueError('A lista basedeficha nao trouxe nenhum artigo com RPM e voltas.')
    FichaArtigo.query.delete()
    agora = datetime.utcnow()
    for k, (_, l) in melhor.items():
        db.session.add(FichaArtigo(artigo=k, base=k.split('-')[0], totvs=l['totvs'], cliente=l['cliente'], rpm=l['rpm'], voltas=l['voltas'], status=l['status'], data=l['data'], atualizado_em=agora))
    db.session.commit()
    ESTADO_FICHA.update(artigos=len(melhor), registros=len(linhas))
    return len(melhor)


def verificar_ficha() -> None:
    from app.indicadores import sharepoint as sp
    if FONTE != 'ficha' or not sp.configurado() or time.time() - _ult_ficha[0] < FICHA_MIN * 60:
        return
    _ult_ficha[0] = time.time()
    try:
        sincronizar_ficha()
        ESTADO_FICHA.update(ultima=datetime.now().isoformat(timespec='seconds'), erro=None)
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        ESTADO_FICHA.update(erro=str(e)[:300])


def verificar_fontes() -> None:
    """A base de voltas/RPM vem do SharePoint (automatico, a cada PRODUCAODIARIA_MIN min). Se o SharePoint nao responder, usa a planilha da pasta do projeto."""
    from app.indicadores import sharepoint as sp
    if FONTE == 'raport':                                      # so Raport e RPM da planilha: nada do SharePoint/PowerApps entra na previsao
        return
    verificar_ficha()
    if sp.configurado() and time.time() - _ult_sp[0] >= SP_MIN * 60:
        _ult_sp[0] = time.time()
        try:
            n = sincronizar_sharepoint()
            ESTADO_BASE.update(fonte='sharepoint', sharepoint_ultima=datetime.now().isoformat(timespec='seconds'), sharepoint_erro=None, sharepoint_linhas=n)
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            ESTADO_BASE.update(sharepoint_erro=str(e)[:300])
    if ESTADO_BASE['fonte'] != 'sharepoint':                   # SharePoint nunca respondeu: usa a planilha da pasta do projeto, se houver
        verificar_arquivo()
        if ProducaoRef.query.count():
            ESTADO_BASE['fonte'] = 'planilha'


def verificar_arquivo() -> None:
    """Importa a planilha quando ela e nova ou foi trocada (olha a data do arquivo)."""
    p = caminho_base()
    try:
        mt = p.stat().st_mtime
    except OSError:
        return
    if _arquivo_lido['mtime'] == mt:
        return
    try:
        importar(p)
        _arquivo_lido['mtime'] = mt
    except Exception as e:  # noqa: BLE001
        _arquivo_lido['mtime'] = mt          # nao tenta de novo a cada ciclo
        print(f'[previsao] base de voltas/RPM nao importada: {e}', flush=True)


def atualizar_status_maquinas() -> None:
    """Maquina com OP na fila = ativa; sem nenhuma OP = sem programacao (automatico). Manutencao e inoperante continuam como foram marcadas."""
    com_fila = {m for (m,) in db.session.query(Sequencia.machine_id).filter(Sequencia.status.in_(ATIVAS)).distinct()}
    mudou = False
    for m in Machine.query.all():
        if m.status in (Machine.STATUS_MANUTENCAO, Machine.STATUS_INOPERANTE):
            continue
        novo = Machine.STATUS_ATIVA if m.id in com_fila else Machine.STATUS_SEM_PROGRAMACAO
        if m.status != novo:
            m.status, mudou = novo, True
    if mudou:
        db.session.commit()


def _indices():
    por_op, por_maq_art, por_art = {}, {}, {}
    for r in ProducaoRef.query.order_by(ProducaoRef.referencia.asc().nullsfirst(), ProducaoRef.id).all():     # o mais recente sobrescreve
        if r.op:
            por_op[(r.op, r.maquina)] = r
        por_maq_art[(r.maquina, r.artigo)] = r
        por_art[r.artigo] = r
    ficha = {}
    for f in FichaArtigo.query.all():
        ficha[f.artigo] = f
        ficha.setdefault(f.base, f)
    return por_op, por_maq_art, por_art, ficha


def _codigo(artigo) -> str:
    m = re.match(r'\s*(\S+)', str(artigo or ''))
    return m.group(1) if m else ''


RAPORT_ESTRUTURA = {'CM': 3.5, 'XN': 3.0, 'CF': 2.0, 'PL': 2.0, 'XP': 2.5, 'NV': 2.0}
RAPORT_MAQUINA = {(4, 'CM'): 4.0, (8, 'CM'): 4.0}
RPM_INSTALADO = {1: 19.1, 2: 18.5, 3: 18.5, 4: 16.8, 5: 18.9, 6: 19.0, 8: 16.7, 9: 20.9, 10: 17.0, 12: 20.8, 17: 21.0, 22: 20.7, 23: 21.0, 25: 20.7,
                 40: 13.9, 41: 16.8, 43: 17.0, 44: 16.8, 45: 18.0, 46: 17.0, 47: 16.8, 48: 18.7, 49: 17.0, 50: 17.0, 51: 16.8, 52: 16.8, 53: 16.8, 54: 17.0, 55: 17.0}
ARTIGOS_TAMANHO = {2230016: ('CM', 52), 21992: ('PL', 52), 410036: ('CM', 42), 3540003: ('CM', 52), 21975: ('CM', 42), 190044: ('NV', 32), 1130024: ('CM', 52),
                   2410025: ('PL', 52), 190025: ('CM', 42), 3530001: ('CM', 52), 2830025: ('PL', 52), 20849: ('CM', 42), 3030073: ('PL', 52), 2070053: ('CM', 52),
                   3710001: ('CM', 52), 21875: ('CM', 52), 1910028: ('XP', 52), 2830013: ('PL', 52), 1130031: ('CM', 52), 21974: ('CM', 52), 2830026: ('NV', 52),
                   3540018: ('CM', 52), 2070065: ('XP', 22), 1130030: ('XP', 52), 61573: ('XP', 32), 3530002: ('CM', 52)}
_NOMES_ESTRUTURA = {'COMUM': 'CM', 'PLANO': 'PL', 'NOVA': 'NV', 'XPREMIUM': 'XP', 'X PREMIUM': 'XP', 'XNEW': 'XN', 'X NEW': 'XN', 'COMFORT': 'CF', 'CONFORT': 'CF'}


def semear_raport() -> None:
    """Grava os valores iniciais (planilha Acompanhamento Producao) so onde a tabela ainda esta vazia; depois a tela /raport edita."""
    if not RaportEstrutura.query.first():
        db.session.add_all(RaportEstrutura(estrutura=e, raport=r) for e, r in RAPORT_ESTRUTURA.items())
        db.session.add_all(RaportMaquinaEstrutura(maquina=m, estrutura=e, raport=r) for (m, e), r in RAPORT_MAQUINA.items())
    if not MaquinaRpm.query.first():
        db.session.add_all(MaquinaRpm(maquina=m, rpm_instalado=r) for m, r in RPM_INSTALADO.items())
    if not ArtigoTamanho.query.first():
        db.session.add_all(ArtigoTamanho(artigo=str(a), estrutura=e, metros_peca=t) for a, (e, t) in ARTIGOS_TAMANHO.items())
    db.session.commit()


def codigo_estrutura(texto):
    """'ESTRUTURA COMUM' / 'COMUM' / 'CM' -> 'CM'."""
    t = re.sub(r'\s+', ' ', re.sub(r'^ESTRUTURA\s*', '', _sem_acento(texto).upper().strip())).strip()
    if not t:
        return None
    return _NOMES_ESTRUTURA.get(t, t if len(t) == 2 else None)


def _sem_acento(t):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', str(t or '')) if unicodedata.category(c) != 'Mn')


def _cfg_raport():
    return ({r.estrutura: r.raport for r in RaportEstrutura.query.all()},
            {(r.maquina, r.estrutura): r.raport for r in RaportMaquinaEstrutura.query.all()},
            {r.maquina: r.rpm_instalado for r in MaquinaRpm.query.all()},
            {r.artigo: r for r in ArtigoTamanho.query.all()})


def _ds_metros(s):
    """DS.METROS (metragem padrao) da OP, em metros."""
    try:
        from app.main.routes import resolve_metros_from_article
        v = resolve_metros_from_article(s.artigo)
        return float(str(v).replace(',', '.')) if v else None
    except Exception:  # noqa: BLE001
        return None


def previsao_raport(cfg, maquina: int, s, idx_art):
    """(voltas, rpm85, raport) da OP pelo raport da estrutura e RPM instalado da maquina, ou None se faltar algum dado."""
    por_estr, por_maq, rpms, artigos = cfg
    art = _codigo(s.artigo)
    tam = artigos.get(art)
    estr = codigo_estrutura(s.estrutura) or (tam.estrutura if tam else None)
    rpm = rpms.get(maquina)
    raport = por_maq.get((maquina, estr)) or por_estr.get(estr)
    metros = _ds_metros(s) or (tam.metros_peca if tam else None)       # DS.METROS da OP (METRO IDEAL LOTE do TOTVS); a medida da planilha so entra se o TOTVS nao informar
    if not (estr and rpm and raport and metros):
        return None
    return metros * 100 / raport, rpm * EFICIENCIA, raport              # voltas = metragem padrao (m) x 100 / raport (cm); RPM = RPM instalado da maquina x eficiencia (80 %)


class _RefFicha:
    """Voltas e RPM da ficha tecnica (basedeficha), no mesmo formato de ProducaoRef."""
    def __init__(self, f):
        self.voltas, self.rpm, self.metros_peca, self.op, self.data, self.status = f.voltas, f.rpm, None, None, f.data, f.status


def referencia(idx, maquina: int, op, artigo):
    por_op, por_maq_art, por_art, ficha = idx
    art = _codigo(artigo)
    if FONTE == 'raport':
        return None, None                                              # a conta sai do Raport (previsao_raport)
    if FONTE == 'ficha':              # padrao: o registro mais recente do artigo na ficha (basedeficha) manda
        f = ficha.get(art.upper()) or ficha.get(art.upper().split('-')[0])
        if f:
            return _RefFicha(f), 'ficha'
    if op and (str(op), maquina) in por_op:
        return por_op[(str(op), maquina)], 'OP'
    if (maquina, art) in por_maq_art:
        return por_maq_art[(maquina, art)], 'maquina+artigo'
    if art in por_art:
        return por_art[art], 'artigo'
    f = ficha.get(art.upper()) or ficha.get(art.upper().split('-')[0])
    if f:
        return _RefFicha(f), 'ficha'
    return None, None


def _cfg_leve(maquina: int, s):
    """Mesma coisa que _cfg_raport(), mas so com o artigo e a maquina desta OP (o modal do INFO abre sem ler o cadastro inteiro de artigos)."""
    cod = _codigo(s.artigo)
    return ({r.estrutura: r.raport for r in RaportEstrutura.query.all()},
            {(r.maquina, r.estrutura): r.raport for r in RaportMaquinaEstrutura.query.filter_by(maquina=maquina).all()},
            {r.maquina: r.rpm_instalado for r in MaquinaRpm.query.filter_by(maquina=maquina).all()},
            {r.artigo: r for r in ArtigoTamanho.query.filter_by(artigo=cod).all()})


def raport_da_op(maquina: int, s):
    """Raport (cm) do artigo da OP nesta maquina: excecao da maquina + estrutura, senao o da estrutura (cadastro de Raport). None se a estrutura nao esta no cadastro."""
    por_estr, por_maq, _rpms, artigos = _cfg_leve(maquina, s)
    tam = artigos.get(_codigo(s.artigo))
    estr = codigo_estrutura(s.estrutura) or (tam.estrutura if tam else None)
    return (por_maq.get((maquina, estr)) or por_estr.get(estr)) if estr else None


def estimativa_artigo(maquina: int, s):
    """Quantas pecas o artigo da OP `s` entrega nesta maquina (mesma conta do Termino Malharia): DS.METROS, voltas, RPM, raport, pecas por hora/turno/24 h. None se faltar base."""
    idx = _indices() if FONTE != 'raport' else ({}, {}, {}, {})                  # fonte raport: nao precisa da base de producao (varrer a tabela inteira deixava o modal lento)
    ref, fonte = referencia(idx, maquina, s.op, s.artigo)
    rap = None if ref else previsao_raport(_cfg_leve(maquina, s), maquina, s, idx[2])
    if ref:
        voltas, rpm, min_dia, raport = ref.voltas, ref.rpm, MIN_DIA, None
    elif rap:
        (voltas, rpm, raport), min_dia, fonte = rap, 1440, 'raport'
    else:
        return None
    if not voltas or not rpm:
        return None
    min_peca = voltas / rpm
    por_dia = min_dia / min_peca
    metros = _ds_metros(s) or (round(voltas * raport / 100, 1) if raport else None)      # sem DS.METROS no TOTVS: a metragem da planilha (voltas x raport)
    return dict(fonte=fonte, ds_metros=metros, voltas=round(voltas, 1), rpm=round(rpm, 2), rpm_instalado=round(rpm / EFICIENCIA, 1) if fonte == 'raport' else None, raport=raport, eficiencia=round(EFICIENCIA * 100),
                min_por_peca=round(min_peca, 1), pecas_hora=round(por_dia / 24, 2), pecas_turno=round(por_dia / 3, 1), pecas_dia=round(por_dia, 1), metros_dia=round(por_dia * metros) if metros else None)


def _pular_dias(inicio: datetime, dias: float, parados: set) -> datetime:
    """Termina `dias` de producao depois de `inicio`, pulando os dias sem producao. O dia de producao vai das 06:00 as 06:00 (a fabrica para no domingo 06:00 e volta na
    segunda 06:00), por isso as datas sao contadas com 6 h de deslocamento: um domingo sem producao cobre de domingo 06:00 a segunda 06:00."""
    desloc = timedelta(hours=6)
    fim = inicio + timedelta(days=dias)
    contados = set()
    while True:
        novos = {d for d in parados if (inicio - desloc).date() <= d <= (fim - desloc).date()} - contados
        if not novos:
            return fim
        contados |= novos
        fim += timedelta(days=len(novos))


def calcular(agora=None, maquina: int = None, esperar: bool = False) -> int:
    """Recalcula pecas_dia e termino_malharia de todas as OPs ativas (ou so da maquina `maquina`). Devolve quantas OPs foram calculadas.
    esperar=True: se outro calculo estiver rodando, espera ate 25 s em vez de desistir (usado quando alguem reordena a fila)."""
    if not _trava.acquire(blocking=esperar, timeout=25 if esperar else -1):
        return 0
    try:
        verificar_fontes()
        atualizar_status_maquinas()
        semear_raport()
        idx = _indices()
        cfg = _cfg_raport()
        if not any(idx) and not cfg[2]:
            return 0
        agora = agora or datetime.now()
        from app.dias_producao import domingos
        parados_global = {d.date for d in NonWorkingDay.query.filter(NonWorkingDay.date >= agora.date()).all() if d.machine_id is None} | domingos(agora.date(), agora.date() + timedelta(days=500))     # domingo nunca produz
        n = 0
        for m in (Machine.query.filter_by(number=maquina).all() if maquina else Machine.query.all()):
            parados = parados_global | {d.date for d in NonWorkingDay.query.filter(NonWorkingDay.machine_id == m.id, NonWorkingDay.date >= agora.date()).all()}
            seqs = [s for s in Sequencia.query.filter_by(machine_id=m.id).all() if (s.status or '') in ATIVAS]
            seqs.sort(key=lambda s: (0 if s.status == 'PRODUZINDO' else 1, s.sequence_order if s.sequence_order is not None else 10 ** 6, s.id))
            cursor = agora
            for s in seqs:
                ref, fonte = referencia(idx, m.number, s.op, s.artigo)
                rap = None if ref else previsao_raport(cfg, m.number, s, idx[2])
                if not ref and not rap:
                    s.termino_calculado = False
                    s.pecas_dia = s.prev_fonte = s.prev_rpm = s.prev_voltas = s.prev_raport = s.prev_termino_em = None
                    if s.termino_malharia:                              # sem base: a fila segue pelo termino que o PCP Hub informou
                        cursor = max(cursor, datetime.combine(s.termino_malharia, datetime.min.time()))
                    continue
                if s.status != 'PRODUZINDO' and TROCA_HORAS:
                    cursor = cursor + timedelta(hours=TROCA_HORAS)
                if ref:
                    voltas, rpm, minutos_dia, s.prev_raport = ref.voltas, ref.rpm, MIN_DIA, None
                else:                                                   # raport x RPM instalado (ja com a eficiencia aplicada no RPM, sem desconto extra)
                    (voltas, rpm, s.prev_raport), minutos_dia, fonte = rap, 1440, 'raport'
                tempo_peca = voltas / rpm                               # minutos por peca
                pecas_dia = minutos_dia / tempo_peca
                restam = max((s.total_pcs or 0) - (s.pecas_produzidas or 0), 0)
                fim = _pular_dias(cursor, restam / pecas_dia, parados)
                s.pecas_dia, s.prev_fonte, s.prev_rpm, s.prev_voltas = round(pecas_dia, 2), fonte, round(rpm, 3), round(voltas, 1)
                s.termino_malharia, s.termino_calculado, s.prev_termino_em = fim.date(), True, fim
                cursor = fim
                n += 1
        db.session.commit()
        return n
    except Exception:
        db.session.rollback()
        raise
    finally:
        _trava.release()
