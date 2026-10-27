"""Previsao de termino de cada OP na maquina, pelas voltas e RPM (mesma conta da planilha producaoDIARIA).

  tempo da peca (min) = voltas / RPM
  minutos uteis por dia = 1440 x (1 - ineficiencia)            (padrao 15 % -> 1224 min)
  pecas em 24 h        = minutos uteis / tempo da peca
  dias para terminar   = pecas que faltam / pecas em 24 h

As OPs de cada maquina andam na ordem da fila: a OP produzindo comeca agora (descontando o que ja foi produzido) e cada
proxima comeca quando a anterior termina, pulando os dias sem producao (feriados e paradas). O resultado vai para
Sequencia.termino_malharia (Termino Malharia) e Sequencia.pecas_dia. Voltas e RPM vem da base (ProducaoRef):
1) da propria OP, 2) do ultimo registro da maquina+artigo, 3) do ultimo registro do artigo. Sem base: mantem o termino do PCP Hub."""
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import current_app

from app import db
from app.models import Machine, NonWorkingDay, ProducaoRef, Sequencia

INEFICIENCIA = float(os.getenv('INEFICIENCIA_MAQUINA', '0.15'))
MIN_DIA = 1440 * (1 - INEFICIENCIA)
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


def verificar_fontes() -> None:
    """A base de voltas/RPM vem do SharePoint (automatico, a cada PRODUCAODIARIA_MIN min). Se o SharePoint nao responder, usa a planilha da pasta do projeto."""
    from app.indicadores import sharepoint as sp
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
    return por_op, por_maq_art, por_art


def _codigo(artigo) -> str:
    m = re.match(r'\s*(\S+)', str(artigo or ''))
    return m.group(1) if m else ''


def referencia(idx, maquina: int, op, artigo):
    por_op, por_maq_art, por_art = idx
    art = _codigo(artigo)
    if op and (str(op), maquina) in por_op:
        return por_op[(str(op), maquina)], 'OP'
    if (maquina, art) in por_maq_art:
        return por_maq_art[(maquina, art)], 'maquina+artigo'
    if art in por_art:
        return por_art[art], 'artigo'
    return None, None


def _pular_dias(inicio: datetime, dias: float, parados: set) -> datetime:
    fim = inicio + timedelta(days=dias)
    contados = set()
    while True:
        novos = {d for d in parados if inicio.date() <= d <= fim.date()} - contados
        if not novos:
            return fim
        contados |= novos
        fim += timedelta(days=len(novos))


def calcular(agora=None) -> int:
    """Recalcula pecas_dia e termino_malharia de todas as OPs ativas. Devolve quantas OPs foram calculadas."""
    if not _trava.acquire(blocking=False):
        return 0
    try:
        verificar_fontes()
        atualizar_status_maquinas()
        idx = _indices()
        if not any(idx):
            return 0
        agora = agora or datetime.now()
        parados_global = {d.date for d in NonWorkingDay.query.filter(NonWorkingDay.date >= agora.date()).all() if d.machine_id is None}
        n = 0
        for m in Machine.query.all():
            parados = parados_global | {d.date for d in NonWorkingDay.query.filter(NonWorkingDay.machine_id == m.id, NonWorkingDay.date >= agora.date()).all()}
            seqs = [s for s in Sequencia.query.filter_by(machine_id=m.id).all() if (s.status or '') in ATIVAS]
            seqs.sort(key=lambda s: (0 if s.status == 'PRODUZINDO' else 1, s.sequence_order if s.sequence_order is not None else 10 ** 6, s.id))
            cursor = agora
            for s in seqs:
                ref, fonte = referencia(idx, m.number, s.op, s.artigo)
                if not ref:
                    s.termino_calculado = False
                    s.pecas_dia = s.prev_fonte = s.prev_rpm = s.prev_voltas = s.prev_termino_em = None
                    if s.termino_malharia:                              # sem base: a fila segue pelo termino que o PCP Hub informou
                        cursor = max(cursor, datetime.combine(s.termino_malharia, datetime.min.time()))
                    continue
                if s.status != 'PRODUZINDO' and TROCA_HORAS:
                    cursor = cursor + timedelta(hours=TROCA_HORAS)
                tempo_peca = ref.voltas / ref.rpm                       # minutos por peca
                pecas_dia = MIN_DIA / tempo_peca
                restam = max((s.total_pcs or 0) - (s.pecas_produzidas or 0), 0)
                fim = _pular_dias(cursor, restam / pecas_dia, parados)
                s.pecas_dia, s.prev_fonte, s.prev_rpm, s.prev_voltas = round(pecas_dia, 2), fonte, ref.rpm, ref.voltas
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
