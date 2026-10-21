"""Leitura (so leitura) da lista de NCs (ncform) do SharePoint pela API Graph da Microsoft: origem do Reprova Cru (local 24).

No .env:
  SHAREPOINT_TENANT_ID=...        (ID do diretorio / tenant do Azure)
  SHAREPOINT_CLIENT_ID=...        (ID do aplicativo registrado)
  SHAREPOINT_CLIENT_SECRET=...    (segredo do aplicativo)
  SHAREPOINT_SITE_ID=b846d7a8-2441-4ac1-8505-8e1e5b687265,1005b64e-51eb-439c-a9e3-69b714f601ec   (padrao: conexaomalhasedes)
  SHAREPOINT_LISTA_NC=42bb9846-9eb1-4ac3-b986-948ed40d4b59   (ID da lista ncform; ja e o padrao)
"""
from __future__ import annotations

import os
import time

import requests

SITE_PADRAO = 'conexaomalhasedes.sharepoint.com,b846d7a8-2441-4ac1-8505-8e1e5b687265,1005b64e-51eb-439c-a9e3-69b714f601ec'
GRAPH = 'https://graph.microsoft.com/v1.0'
LISTA_PADRAO = '42bb9846-9eb1-4ac3-b986-948ed40d4b59'          # lista "ncform" (nome interno: ncform-b9730590-...)
_token = {'v': None, 'ate': 0.0}


def configurado() -> bool:
    return all(os.getenv(k) for k in ('SHAREPOINT_TENANT_ID', 'SHAREPOINT_CLIENT_ID', 'SHAREPOINT_CLIENT_SECRET'))


def faltando() -> list:
    return [k for k in ('SHAREPOINT_TENANT_ID', 'SHAREPOINT_CLIENT_ID', 'SHAREPOINT_CLIENT_SECRET') if not os.getenv(k)]


def _site() -> str:
    return os.getenv('SHAREPOINT_SITE_ID') or SITE_PADRAO


def _lista() -> str:
    return os.getenv('SHAREPOINT_LISTA_NC') or LISTA_PADRAO


def _auth() -> dict:
    if not _token['v'] or time.time() > _token['ate'] - 60:
        r = requests.post(f"https://login.microsoftonline.com/{os.environ['SHAREPOINT_TENANT_ID']}/oauth2/v2.0/token", timeout=30,
                          data=dict(grant_type='client_credentials', client_id=os.environ['SHAREPOINT_CLIENT_ID'], client_secret=os.environ['SHAREPOINT_CLIENT_SECRET'],
                                    scope='https://graph.microsoft.com/.default'))
        if r.status_code != 200:
            raise RuntimeError(f'Microsoft recusou o login: HTTP {r.status_code} {r.text[:200]}')
        j = r.json()
        _token.update(v=j['access_token'], ate=time.time() + int(j.get('expires_in', 3600)))
    return {'Authorization': f"Bearer {_token['v']}"}


def _get(url: str, params=None) -> dict:
    r = requests.get(url, headers=_auth(), params=params, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f'Graph HTTP {r.status_code}: {r.text[:240]}')
    return r.json()


def colunas() -> list:
    j = _get(f'{GRAPH}/sites/{_site()}/lists/{_lista()}/columns')
    return [dict(nome=c.get('name'), titulo=c.get('displayName'), tipo=next((k for k in ('text', 'number', 'dateTime', 'choice', 'boolean', 'lookup', 'currency', 'personOrGroup') if k in c), '?')) for c in j.get('value') or []]


def itens(limite: int = 5000) -> list:
    """Linhas da lista (campo 'fields' de cada item), da mais nova para a mais antiga."""
    saida, url, params = [], f'{GRAPH}/sites/{_site()}/lists/{_lista()}/items', {'$expand': 'fields', '$top': 999}
    while url and len(saida) < limite:
        j = _get(url, params)
        for it in j.get('value') or []:
            saida.append(dict(it.get('fields') or {}, _id=it.get('id'), _criado=it.get('createdDateTime'), _alterado=it.get('lastModifiedDateTime')))
        url, params = j.get('@odata.nextLink'), None
    return saida


def itens_recentes(desde_iso: str, limite: int = 3000) -> list:
    """So os itens alterados depois de `desde_iso` (ex.: 2026-10-21T00:00:00Z): le da mais recente para a mais antiga e para no corte."""
    saida, url, params = [], f'{GRAPH}/sites/{_site()}/lists/{_lista()}/items', {'$expand': 'fields', '$top': 100, '$orderby': 'lastModifiedDateTime desc'}
    while url and len(saida) < limite:
        j = _get(url, params)
        for it in j.get('value') or []:
            if str(it.get('lastModifiedDateTime') or '') < desde_iso:
                return saida
            saida.append(dict(it.get('fields') or {}, _id=it.get('id'), _criado=it.get('createdDateTime'), _alterado=it.get('lastModifiedDateTime')))
        url, params = j.get('@odata.nextLink'), None
    return saida
