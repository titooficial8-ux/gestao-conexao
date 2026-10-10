"""Importa a fotografia mais recente do PCP Hub (arquivos app/seed_data/pcp_hub_*.json)
para o banco do Gestao Conexao: Planejamento (programacao por maquina, relatorio de
pedidos, fila de pre-programacao, historico, dias sem producao) e Producao (pesagens
do relatorio 32 da Malharia e ciclos diarios por artigo).

Uso (na pasta do projeto, com a venv ativa):
    python importar_pcp_hub.py

Os dados importados antes sao substituidos pelos da fotografia. Usuarios, chamados,
permissoes e o resto do banco nao sao mexidos.
"""
import json
import os
from datetime import datetime, date, timezone

from dotenv import load_dotenv

load_dotenv()

from app import create_app, db  # noqa: E402
from app.models import (  # noqa: E402
    Machine, Sequencia, PedidoRelatorio, FilaPreProgramacao, HistoricoProducao,
    Report32Movement, ArticleDailyCycle, NonWorkingDay, ArticleEstruturaMapping, BaseEstrutura,
)

SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app', 'seed_data')


def _ler(nome):
    caminho = os.path.join(SEED_DIR, nome)
    if not os.path.exists(caminho):
        print(f'  (arquivo {nome} nao encontrado, pulando)')
        return []
    with open(caminho, encoding='utf-8') as f:
        dados = json.load(f)
    return dados['rows'] if isinstance(dados, dict) else dados


def _data(valor):
    """Aceita 'AAAA-MM-DD' ou 'DD/MM/AAAA'."""
    if not valor:
        return None
    valor = str(valor)[:10]
    try:
        if '/' in valor:
            return datetime.strptime(valor, '%d/%m/%Y').date()
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _data_hora(valor):
    """Timestamp com fuso do PCP Hub -> datetime em UTC sem fuso (padrao do banco)."""
    if not valor:
        return None
    dt = datetime.fromisoformat(str(valor).replace('Z', '+00:00'))
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _num(valor, tipo=float):
    try:
        return tipo(valor) if valor is not None and valor != '' else 0
    except (TypeError, ValueError):
        return 0


def _substituir(modelo, objetos, rotulo):
    modelo.query.delete()
    db.session.bulk_save_objects(objetos)
    db.session.commit()
    print(f'  {rotulo}: {len(objetos)}')


def importar():
    agora = datetime.utcnow()
    print('Importando dados do PCP Hub...')

    # --- cadastros usados para resolver estrutura/metragem ---
    _substituir(ArticleEstruturaMapping, [
        ArticleEstruturaMapping(article=r['article'], estrutura=r['estrutura'])
        for r in _ler('pcp_hub_estrutura_mapping.json') if r.get('article')
    ], 'Mapeamento artigo x estrutura')

    _substituir(BaseEstrutura, [
        BaseEstrutura(ds_produto=r['ds_produto'], ds_metros=r['ds_metros'])
        for r in _ler('pcp_hub_base_estrutura.json')
    ], 'Base estrutura')

    # --- Planejamento ---
    _substituir(PedidoRelatorio, [
        PedidoRelatorio(
            cod_pedido=r.get('cod_pedido'), nome_cliente=r.get('nome_cliente'),
            dt_pedido=_data(r.get('dt_pedido')), dt_prevfaturamento=_data(r.get('dt_prevfaturamento')),
            dt_chegada30=_data(r.get('dt_chegada30')), tp_situacao=r.get('tp_situacao'),
            cod_produto=r.get('cod_produto'), ds_produto=r.get('ds_produto'),
            cod_segmento=r.get('cod_segmento'), estrutura=r.get('estrutura'),
            qt_solicitada=_num(r.get('qt_solicitada')), qt_faturada=_num(r.get('qt_faturada')),
            qt_pendente=_num(r.get('qt_pendente')), nr_cicloop=r.get('nr_cicloop'), nr_op=r.get('nr_op'),
            qt_finalizada=_num(r.get('qt_finalizada')), qt_pendenteop=_num(r.get('qt_pendenteop')),
            qt_afaturar=_num(r.get('qt_afaturar')), qt_saldo=_num(r.get('qt_saldo')),
            region=r.get('region') or 'BR', synced_at=agora,
        ) for r in _ler('pcp_hub_pedidos.json')
    ], 'Relatorio de pedidos')

    # a estrutura da sequencia vem do mapeamento/pedidos que acabaram de entrar
    from app.main.routes import resolve_estrutura_from_article

    maquinas = {m.number: m for m in Machine.query.all()}
    sequencias = []
    for r in _ler('pcp_hub_snapshot.json'):
        try:
            numero = int(str(r.get('machine')).strip())
        except (TypeError, ValueError):
            continue
        maquina = maquinas.get(numero)
        if not maquina:
            maquina = Machine(number=numero)
            db.session.add(maquina)
            db.session.flush()
            maquinas[numero] = maquina
        sequencias.append(Sequencia(
            machine_id=maquina.id, sequence_order=r.get('sequence_order'),
            artigo=r.get('article'), cliente=r.get('client'), op=r.get('op'), status=r.get('status'),
            total_pcs=_num(r.get('total_pcs'), int), pecas_produzidas=_num(r.get('quantity_pieces'), int),
            qt_solicitada=_num(r.get('requested_quantity'), int), qt_avulsa=_num(r.get('qt_avulsa'), int),
            cod_segmento=r.get('cod_segmento'), dt_cliente=_data(r.get('client_date')),
            production_month=r.get('production_month'), termino_malharia=_data(r.get('estimated_end_date')),
            estrutura=resolve_estrutura_from_article(r.get('article')),
            region=r.get('region') or 'BR', synced_at=agora,
        ))
    _substituir(Sequencia, sequencias, 'Programacao por maquina (sequencias)')

    _substituir(FilaPreProgramacao, [
        FilaPreProgramacao(
            op=r.get('op'), artigo=r.get('article'), cliente=r.get('client'),
            requested_quantity=_num(r.get('requested_quantity')), total_pcs=_num(r.get('total_pcs'), int),
            status=r.get('status'), moved_to_machine=r.get('moved_to_machine'),
            moved_at=_data_hora(r.get('moved_at')), is_avulsa=bool(r.get('is_avulsa')),
            cod_segmento=r.get('cod_segmento'), production_month=r.get('production_month'),
            dt_prevfaturamento=r.get('dt_prevfaturamento'),
            created_at=_data_hora(r.get('created_at')) or agora, synced_at=agora,
        ) for r in _ler('pcp_hub_priority_queue.json')
    ], 'Fila de pre-programacao')

    _substituir(HistoricoProducao, [
        HistoricoProducao(
            machine=r.get('machine'), date=_data(r.get('date')), artigo=r.get('article'),
            cliente=r.get('client'), op=r.get('op'), quantity_pieces=_num(r.get('quantity_pieces'), int),
            requested_quantity=_num(r.get('requested_quantity'), int),
            pieces_per_day=_num(r.get('pieces_per_day'), int), old_status=r.get('old_status'),
            new_status=r.get('new_status'), changed_at=_data_hora(r.get('changed_at')),
            reason=(r.get('reason') or None) and r['reason'][:255],
            estimated_end_date=_data(r.get('estimated_end_date')), client_date=_data(r.get('client_date')),
            total_pcs=r.get('total_pcs'), synced_at=agora,
        ) for r in _ler('pcp_hub_historico.json')
    ], 'Historico de producao')

    ja_cadastrados = {d.date for d in NonWorkingDay.query.filter_by(machine_id=None).all()}
    novos_dias = []
    for r in _ler('pcp_hub_non_working_days.json'):
        dia = _data(r.get('date'))
        if dia and dia not in ja_cadastrados:
            ja_cadastrados.add(dia)
            novos_dias.append(NonWorkingDay(date=dia, comment=r.get('note') or 'Importado do PCP Hub'))
    db.session.bulk_save_objects(novos_dias)
    db.session.commit()
    print(f'  Dias sem producao (novos): {len(novos_dias)}')

    # --- Producao ---
    _substituir(Report32Movement, [
        Report32Movement(
            ciclo=r.get('ciclo'), op=r.get('op'), nr_lote=r.get('nr_lote'), nr_item=r.get('nr_item'),
            cod_artigo=r.get('cod_artigo'), metro_padrao=r.get('metro_padrao'), gramatura=r.get('gramatura'),
            largura=r.get('largura'), local_code=r.get('local_code'), qt_movimento=r.get('qt_movimento'),
            qt_pesos=r.get('qt_pesos'), cod_maquina=r.get('cod_maquina'),
            dt_real=_data_hora(r.get('dt_real')), synced_at=agora,
        ) for r in _ler('pcp_hub_report32_movement.json')
    ], 'Pesagens da Malharia (relatorio 32)')

    _substituir(ArticleDailyCycle, [
        ArticleDailyCycle(
            article=r.get('article'), machine=r.get('machine'), op=r.get('op'),
            cycle_date=_data(r.get('cycle_date')), pieces=_num(r.get('pieces'), int),
            meters=_num(r.get('meters')), synced_at=agora,
        ) for r in _ler('pcp_hub_article_daily_cycles.json')
    ], 'Ciclos diarios por artigo')

    print('Pronto! Abra o site e confira Planejamento e Producao.')


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        importar()
