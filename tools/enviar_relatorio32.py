"""Exemplo para o AGENTE que le o Relatorio 32 (DICFM001) no TOTVS: envia as pesagens direto para o Gestao, sem passar pelo PCP Hub.

No .env do Gestao:   RELATORIO32_TOKEN=<uma chave longa>
No agente:           GESTAO_URL=http://IP-DO-GESTAO:5000   GESTAO_TOKEN=<a mesma chave>

Chame `enviar(linhas)` a cada rodada (ex.: 1 min) com as pesagens do periodo lido. Cada linha e um dict:
  op, nr_item, nr_lote, cod_artigo, cod_maquina, local_code ('23'), qt_movimento (metros), qt_pesos (kg), metro_padrao, gramatura, largura, ciclo,
  dt_real (ISO; sem fuso = horario de Brasilia)
Se as linhas forem TODAS as do periodo lido, passe completo=True: o que nao vier mais e removido no Gestao (o Relatorio 32 corrigiu/removeu a peca)."""
import os

import requests

URL = os.getenv('GESTAO_URL', 'http://localhost:5000').rstrip('/') + '/api/relatorio32/receber'
TOKEN = os.getenv('GESTAO_TOKEN', '')


def enviar(linhas: list, completo: bool = False) -> dict:
    r = requests.post(URL, json={'linhas': linhas, 'completo': completo}, headers={'X-Token': TOKEN}, timeout=60)
    r.raise_for_status()
    return r.json()


if __name__ == '__main__':
    exemplo = [dict(op='3096', nr_item='1', nr_lote='1', cod_artigo='21972', cod_maquina='01', local_code='23', qt_movimento=52, qt_pesos=24.3, metro_padrao=52, dt_real='2026-10-30T10:15:00')]
    print(enviar(exemplo))
