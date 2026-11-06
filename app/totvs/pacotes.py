"""Recebe do TOTVS (modulo data-package) o pacote de saida com o relatorio de pedidos (038), que traz a OP de cada pedido (Nr. Cicloop / Nr. Op) —
a amarracao feita no PCPFP141, que os endpoints de pedido e de OP nao entregam. Automatico, sem ninguem subir arquivo.

Liga quando a Bhan/TOTVS configurar o pacote de saida (INTFC007) e o .env da VPS tiver:
    DATAPKG_TARGET=<Destino do pacote>          (obrigatorio)
    DATAPKG_MODEL=<codigo do modelo>            (opcional: filtra o modelo do pacote)
    DATAPKG_MIN=10                              (opcional: de quantos em quantos minutos olha pacotes novos)
So marca o pacote como 'Recebido' depois de importar com sucesso; se o conteudo vier diferente do esperado, nao marca e registra o erro."""
from __future__ import annotations

import base64
import os
import threading
import time
import traceback
from datetime import datetime, timedelta

from . import api

BASE = f'{api.BASE_URL}/api/totvsmoda/data-package/v2'
ESTADO = dict(ligado=False, ultima=None, erro=None, pacote=None, linhas=None, com_op=None)


def configurado() -> bool:
    return bool(os.getenv('DATAPKG_TARGET', '').strip())


def _cliente():
    cli = api.TotvsModaClient()
    cli.autenticar()
    return cli


def buscar_e_importar(app) -> bool:
    """Uma rodada: pega o pacote mais novo ainda nao recebido, importa e marca como recebido. Devolve True se importou algum."""
    from app.totvs.vinculos import importar_csv
    alvo = os.getenv('DATAPKG_TARGET', '').strip()
    modelo = os.getenv('DATAPKG_MODEL', '').strip()
    cli = _cliente()
    params = dict(Target=alvo, Page=1, PageSize=50, InitialInsertDate=(datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d'), Order='-insertDate')
    if modelo.isdigit():
        params['ModelCode'] = int(modelo)
    r = cli.get(f'{BASE}/output-packages', params)
    if r.status_code != 200:
        raise RuntimeError(f'Pacotes: HTTP {r.status_code} {r.text[:160]}')
    itens = r.json().get('items') or []
    ESTADO['ultima'] = datetime.now().isoformat(timespec='seconds')
    if not itens:
        return False
    itens.sort(key=lambda x: str(x.get('insertDate') or ''), reverse=True)
    importou = False
    for i, pk in enumerate(itens):                                            # o mais novo vale; os mais antigos so sao marcados como recebidos
        pid = pk.get('packageId')
        if not pid:
            continue
        if i == 0:
            c = cli.get(f'{BASE}/content-packages/{pid}', {})
            if c.status_code != 200:
                raise RuntimeError(f'Conteudo do pacote {pid}: HTTP {c.status_code} {c.text[:160]}')
            bruto = (c.json() or {}).get('content') or ''
            try:
                dados = base64.b64decode(bruto)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f'Pacote {pid}: conteudo nao e Base64 ({e})')
            with app.app_context():
                res = importar_csv(dados, f'pacote TOTVS {pk.get("packageNumber") or pid}')
            ESTADO.update(pacote=str(pk.get('packageNumber') or pid), linhas=res['linhas'], com_op=res['com_op'])
            importou = True
        m = cli.post(f'{BASE}/output-packages/receive', dict(packageId=pid))
        if m.status_code not in (200, 201, 204):
            raise RuntimeError(f'Marcar pacote {pid} como recebido: HTTP {m.status_code} {m.text[:160]}')
    return importou


def iniciar_agendador(app) -> None:
    if not configurado():
        return
    ESTADO['ligado'] = True
    minutos = max(int(os.getenv('DATAPKG_MIN', '10') or 10), 2)

    def loop():
        time.sleep(40)
        while True:
            try:
                if buscar_e_importar(app):
                    from app.totvs import pedidos
                    pedidos.atualizar(app)                                    # recalcula o Relatorio Pedidos com a ligacao nova
                ESTADO['erro'] = None
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                ESTADO['erro'] = str(e)[:300]
            time.sleep(minutos * 60)
    threading.Thread(target=loop, daemon=True, name='totvs-pacotes').start()
