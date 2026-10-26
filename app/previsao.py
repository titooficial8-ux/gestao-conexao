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
        maq = _num(r[ix['Maquina']])
        rpm, voltas = _num(r[ix['RPM']]), _num(r[ix['Voltas']])
        artigo = str(r[ix['Artigo']] or '').strip()
        if maq is None or not artigo or not rpm or not voltas or rpm <= 0 or voltas <= 0:
            continue
        ref = r[ix['Criado']] if 'Criado' in ix and isinstance(r[ix['Criado']], datetime) else None
        novas.append(ProducaoRef(
            op=str(r[ix['numOp']] or '').strip() if 'numOp' in ix else None, maquina=int(maq), artigo=artigo,
            estrutura=str(r[ix['Estrutura']] or '').strip() if 'Estrutura' in ix else None, rpm=rpm, voltas=voltas,
            tam_volta=_num(r[ix['tamVolta']]) if 'tamVolta' in ix else None, metros_peca=_num(r[ix['metrosPeca']]) if 'metrosPeca' in ix else None, referencia=ref))
    if not novas:
        raise ValueError('A planilha nao tem linhas validas (maquina, artigo, RPM e voltas).')
    try:
        ProducaoRef.query.delete()
        db.session.bulk_save_objects(novas)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return len(novas)


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
        verificar_arquivo()
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
                    s.pecas_dia = s.prev_fonte = s.prev_rpm = s.prev_voltas = None
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
                s.termino_malharia, s.termino_calculado = fim.date(), True
                cursor = fim
                n += 1
        db.session.commit()
        return n
    except Exception:
        db.session.rollback()
        raise
    finally:
        _trava.release()
