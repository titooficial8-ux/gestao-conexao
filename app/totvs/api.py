"""Cliente da API TOTVS Moda (faturamento) — adaptado do script Faturamento_TOTVS.

As credenciais NAO ficam no codigo: vem do arquivo .env (veja .env.example).
Nada aqui e executado sozinho; o servico (servico.py) chama gerar_dados().
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd  # noqa: E402
import requests  # noqa: E402

# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------
BASE_URL = os.getenv("TOTVS_BASE_URL", "https://www30.bhan.com.br:9443").rstrip("/")
TOKEN_URL = f"{BASE_URL}/api/totvsmoda/authorization/v2/token"
INVOICES_URL = f"{BASE_URL}/api/totvsmoda/fiscal/v2/invoices/search"
BRANCHES_URL = f"{BASE_URL}/api/totvsmoda/person/v2/branchesList"

GRANT_TYPE = os.getenv("TOTVS_GRANT_TYPE", "password")  # password | client_credentials
CLIENT_ID = os.getenv("TOTVS_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("TOTVS_CLIENT_SECRET", "")
USERNAME = os.getenv("TOTVS_USERNAME", "")
PASSWORD = os.getenv("TOTVS_PASSWORD", "")
LOGIN_BRANCH = os.getenv("TOTVS_LOGIN_BRANCH", "")

_BR = os.getenv("TOTVS_BRANCHES", "auto").strip().lower()
BRANCHES = [] if _BR in ("", "auto") else [int(b) for b in _BR.replace(";", ",").split(",") if b.strip()]
DEFAULT_START = os.getenv("FATURAMENTO_INICIO", f"{date.today().year}-01-01")
# Somente saídas por padrão (faturamento). Use "All" para trazer entradas também.
OPERATION_TYPE = os.getenv("FATURAMENTO_TIPO_OPERACAO", "All")  # All: traz tambem as entradas (devolucoes de venda)
WINDOW_DAYS = int(os.getenv("FATURAMENTO_JANELA_DIAS", "31"))  # máx. API = 6 meses
VERIFY_SSL = os.getenv("TOTVS_VERIFY_SSL", "true").lower() != "false"

CANCELAR = threading.Event()  # o botao "Parar busca" liga isto; as chamadas a API checam e abortam


class Cancelado(Exception):
    pass


NOMES_REP: dict[int, str] = {}   # codigo do representante -> nome (alimentado pelos pedidos; o servico pre-carrega dos meses ja salvos)

PAGE_SIZE = 100  # máximo permitido pela API
EXPAND = "person,items,taxes,payments,salesOrder"
OUT_DIR = Path(os.getenv("FATURAMENTO_SAIDA", Path(__file__).resolve().parents[2] / "instance" / "totvs"))

STATUS_VALIDOS = {"Normal", "Issued"}  # Canceled / Denied / Deleted ficam fora dos totais


def claims_do_token(token: str) -> dict:
    """Decodifica o payload do JWT (sem validar assinatura) só para leitura."""
    import base64
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def empresas_do_token(claims: dict) -> list[int]:
    """Procura nos claims do token as empresas liberadas para o usuário."""
    achadas: set[int] = set()

    def coletar(v):
        if isinstance(v, (list, tuple)):
            for x in v:
                coletar(x)
        elif isinstance(v, int):
            achadas.add(v)
        elif isinstance(v, str):
            for parte in v.replace(";", ",").replace("|", ",").replace(" ", ",").split(","):
                if parte.strip().isdigit():
                    achadas.add(int(parte))
        elif isinstance(v, dict):
            for x in v.values():
                coletar(x)

    for k, v in claims.items():
        kl = k.lower()
        if "branch" in kl or "empresa" in kl or kl in ("cd_empresa", "companies", "company"):
            coletar(v)
    return sorted(b for b in achadas if 0 < b < 100000)


# --------------------------------------------------------------------------
# Cliente da API
# --------------------------------------------------------------------------
class TotvsModaClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.verify = VERIFY_SSL
        self.token: str | None = None
        self.refresh_token: str | None = None
        self.token_expira = 0.0

    def _dados_login(self, grant: str) -> dict:
        data = {"grant_type": grant, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
        if grant == "password":
            data.update({"username": USERNAME, "password": PASSWORD})
            if LOGIN_BRANCH:
                data["branch"] = LOGIN_BRANCH
        return data

    def autenticar(self, usar_refresh: bool = False) -> None:
        if usar_refresh and self.refresh_token:
            r = self.session.post(TOKEN_URL, data={"grant_type": "refresh_token",
                                                   "refresh_token": self.refresh_token}, timeout=60)
            if r.status_code != 200:
                return self.autenticar(usar_refresh=False)
        else:
            # Tenta o grant configurado; se falhar e houver usuário/senha, tenta o outro.
            grants = [GRANT_TYPE]
            if USERNAME and PASSWORD:
                grants.append("password" if GRANT_TYPE != "password" else "client_credentials")
            erros = []
            for g in grants:
                r = self.session.post(TOKEN_URL, data=self._dados_login(g), timeout=60)
                if r.status_code == 200:
                    print(f"  ✔ autenticado via {g}")
                    break
                erros.append(f"{g} → HTTP {r.status_code}: {r.text[:300]}")
            else:
                raise RuntimeError("Falha na autenticação:\n  " + "\n  ".join(erros))
        j = r.json()
        self.token = j["access_token"]
        self.refresh_token = j.get("refresh_token")
        self.token_expira = time.time() + int(j.get("expires_in", 3600)) - 120
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        print("  ✔ token obtido")

    def _garantir_token(self) -> None:
        if CANCELAR.is_set():
            raise Cancelado('Busca cancelada pelo usuario')
        if not self.token:
            self.autenticar()
        elif time.time() >= self.token_expira:
            self.autenticar(usar_refresh=True)

    def post(self, url: str, payload: dict, tentativas: int = 5) -> requests.Response:
        for n in range(1, tentativas + 1):
            self._garantir_token()
            try:
                r = self.session.post(url, json=payload, timeout=180)
            except requests.RequestException as e:
                espera = 2 ** n
                print(f"  ! erro de rede ({e}); nova tentativa em {espera}s")
                time.sleep(espera)
                continue
            if r.status_code == 401:
                self.autenticar(usar_refresh=True)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                espera = 2 ** n
                print(f"  ! HTTP {r.status_code}; nova tentativa em {espera}s")
                time.sleep(espera)
                continue
            return r
        raise RuntimeError(f"Desisti após {tentativas} tentativas em {url}")

    def get(self, url: str, params: dict) -> requests.Response:
        self._garantir_token()
        r = self.session.get(url, params=params, timeout=120)
        if r.status_code == 401:
            self.autenticar(usar_refresh=True)
            r = self.session.get(url, params=params, timeout=120)
        return r


def listar_empresas(cli: "TotvsModaClient") -> list[dict]:
    """Lista as empresas cadastradas (person/v2/branchesList).
    Alguns ambientes dão erro Oracle sem filtro, então tenta variações."""
    agora = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    tentativas = [
        {"StartChangeDate": "1990-01-01T00:00:00", "EndChangeDate": agora, "Order": "code"},
        {"BranchCodeList": list(range(1, 501))},
        {"Order": "code"},
    ]
    for extra in tentativas:
        empresas, page, ok = [], 1, True
        while True:
            r = cli.get(BRANCHES_URL, {**extra, "Page": page, "PageSize": 1000})
            if r.status_code != 200:
                print(f"  ! lista de empresas falhou (HTTP {r.status_code}): {r.text[:150]}")
                ok = False
                break
            j = r.json()
            empresas.extend(j.get("items") or [])
            if not j.get("hasNext"):
                break
            page += 1
        if ok and empresas:
            return empresas
    return []


def empresa_liberada(cli: "TotvsModaClient", codigo: int) -> bool:
    """Testa se o usuário pode consultar notas da empresa (consulta de 1 dia)."""
    hoje = date.today().isoformat()
    payload = {"filter": {"branchCodeList": [codigo], "startIssueDate": f"{hoje}T00:00:00",
                          "endIssueDate": f"{hoje}T23:59:59"}, "page": 1, "pageSize": 1}
    r = cli.post(INVOICES_URL, payload)
    return r.status_code in (200, 404)


def sondar_codigos(cli: "TotvsModaClient", ate: int = 200) -> list[int]:
    """Plano B: testa os códigos 1..ate direto na consulta de notas."""
    from concurrent.futures import ThreadPoolExecutor
    print(f"  Testando códigos de empresa 1 a {ate} direto na API fiscal (leva ~1 min)...")
    cli._garantir_token()
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(lambda c: (c, empresa_liberada(cli, c)), range(1, ate + 1)))
    liberadas = [c for c, ok in res if ok]
    for c in liberadas:
        print(f"    ✔ empresa {c} liberada")
    return liberadas


def descobrir_empresas(cli: "TotvsModaClient") -> list[int]:
    cadastradas = listar_empresas(cli)
    if not cadastradas:
        return sondar_codigos(cli)
    print(f"  {len(cadastradas)} empresa(s) cadastrada(s) no TOTVS. Testando acesso do usuário...")
    liberadas = []
    for e in sorted(cadastradas, key=lambda x: x.get("code") or 0):
        cod = e.get("code")
        if cod is None:
            continue
        ok = empresa_liberada(cli, int(cod))
        nome = e.get("fantasyName") or e.get("personName") or e.get("description") or ""
        print(f"    {'✔' if ok else '✖'} {cod:>4}  {e.get('cnpj') or '':<14}  {nome}")
        if ok:
            liberadas.append(int(cod))
    return liberadas


# --------------------------------------------------------------------------
# Extração
# --------------------------------------------------------------------------
def janelas(inicio: date, fim: date, dias: int):
    atual = inicio
    while atual <= fim:
        fim_janela = min(atual + timedelta(days=dias - 1), fim)
        yield atual, fim_janela
        atual = fim_janela + timedelta(days=1)


class EmpresaNaoPermitida(Exception):
    pass


def buscar_notas(cli: TotvsModaClient, inicio: date, fim: date, empresas: list[int]) -> list[dict]:
    todas: list[dict] = []
    enviar_tipo = OPERATION_TYPE and OPERATION_TYPE != "All"

    for ini_j, fim_j in janelas(inicio, fim, WINDOW_DAYS):
        page = 1
        while True:
            filtro = {
                "branchCodeList": empresas,
                "startIssueDate": f"{ini_j.isoformat()}T00:00:00",
                "endIssueDate": f"{fim_j.isoformat()}T23:59:59",
            }
            if enviar_tipo:
                filtro["operationType"] = OPERATION_TYPE
            payload = {"filter": filtro, "page": page, "pageSize": PAGE_SIZE,
                       "expand": EXPAND, "order": "branchCode,invoiceDate,invoiceSequence"}

            r = cli.post(INVOICES_URL, payload)
            if r.status_code == 400 and enviar_tipo and "operationType" in r.text:
                # Alguns ambientes não aceitam o enum como texto: filtra no cliente.
                print("  ! filtro operationType recusado; filtrando localmente")
                enviar_tipo = False
                continue
            if r.status_code == 404:
                break  # sem notas na janela
            if r.status_code == 400 and "branchCodeList" in r.text and "not allowed" in r.text:
                raise EmpresaNaoPermitida(r.text)
            if r.status_code != 200:
                raise RuntimeError(f"Erro {r.status_code} em {ini_j}..{fim_j} p{page}: {r.text}")

            j = r.json()
            itens = j.get("items") or []
            todas.extend(itens)
            print(f"  {ini_j:%d/%m/%Y}–{fim_j:%d/%m/%Y}  pág {page}/{j.get('totalPages', '?')}  "
                  f"+{len(itens)}  (total {len(todas)})")
            if not j.get("hasNext"):
                break
            page += 1

    if OPERATION_TYPE and OPERATION_TYPE != "All":
        todas = [n for n in todas if str(n.get("operationType", OPERATION_TYPE)) in (OPERATION_TYPE, "2", "S")]
    return todas


# --------------------------------------------------------------------------
# Transformação
# --------------------------------------------------------------------------
def _d(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "")).date()
    except ValueError:
        return v


def montar_notas(notas: list[dict]) -> pd.DataFrame:
    linhas = []
    for n in notas:
        p = n.get("person") or {}
        e = n.get("eletronic") or {}
        pedidos = ", ".join(str(s.get("orderCode")) for s in (n.get("salesOrder") or []) if s.get("orderCode"))
        status = n.get("invoiceStatus")
        linhas.append({
            "Empresa": n.get("branchCode"),
            "CNPJ Empresa": n.get("branchCnpj"),
            "Fatura (seq)": n.get("invoiceSequence"),
            "Data Fatura": _d(n.get("invoiceDate")),
            "Data Emissão": _d(n.get("issueDate")),
            "Nº NF": n.get("invoiceCode"),
            "Série": n.get("serialCode"),
            "Modelo": n.get("documentType"),
            "Situação": status,
            "Status NF-e": e.get("electronicInvoiceStatus"),
            "Chave de Acesso": e.get("accessKey"),
            "Tipo Operação": n.get("operationType"),
            "Cód. Operação": n.get("operationCode"),
            "Operação": n.get("operatioName"),
            "Cód. Cliente": n.get("personCode"),
            "Cliente": n.get("personName"),
            "CPF/CNPJ Cliente": p.get("personCpfCnpj"),
            "Cidade": p.get("city"),
            "UF": p.get("stateAbbreviation"),
            "Cond. Pagamento": n.get("paymentConditionName"),
            "Pedido(s)": pedidos,
            "Quantidade": n.get("quantity"),
            "Valor Produtos": n.get("productValue"),
            "Desconto %": n.get("discountPercentage"),
            "Frete": n.get("shippingValue"),
            "Seguro": n.get("insuranceValue"),
            "Desp. Acessórias": n.get("additionalValue"),
            "IPI": n.get("ipiValue"),
            "Base ICMS": n.get("baseIcmsValue"),
            "ICMS": n.get("icmsValue"),
            "ICMS ST": n.get("icmsSubStValue"),
            "Valor Total NF": n.get("totalValue"),
            "Válida p/ Faturamento": "Sim" if status in STATUS_VALIDOS else "Não",
        })
    return pd.DataFrame(linhas)


def montar_itens(notas: list[dict]) -> pd.DataFrame:
    linhas = []
    for n in notas:
        for it in n.get("items") or []:
            impostos: dict[str, float] = {}
            for t in it.get("taxes") or []:
                nome = (t.get("name") or f"Imposto {t.get('code')}").strip().upper()
                impostos[nome] = impostos.get(nome, 0) + (t.get("taxValue") or 0)
            linha = {
                "Empresa": n.get("branchCode"),
                "Fatura (seq)": n.get("invoiceSequence"),
                "Data Emissão": _d(n.get("issueDate")),
                "Nº NF": n.get("invoiceCode"),
                "Situação": n.get("invoiceStatus"),
                "Cliente": n.get("personName"),
                "Operação": n.get("operatioName"),
                "Item": it.get("sequence"),
                "Cód. Produto": it.get("code"),
                "Produto": it.get("name"),
                "NCM": it.get("ncm"),
                "CFOP": it.get("cfop"),
                "Unidade": it.get("measureUnit"),
                "Quantidade": it.get("quantity"),
                "Vl. Unit. Líquido": it.get("unitNetValue"),
                "Vl. Bruto": it.get("grossValue"),
                "Desconto": it.get("discountValue"),
                "Vl. Líquido": it.get("netValue"),
                "Frete": it.get("freightValue"),
            }
            for nome, valor in impostos.items():
                linha[f"Imp. {nome}"] = valor
            linhas.append(linha)
    return pd.DataFrame(linhas)


def resumos(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if df.empty:
        return {}
    v = df[df["Válida p/ Faturamento"] == "Sim"].copy()
    v["Mês"] = pd.to_datetime(v["Data Emissão"]).dt.to_period("M").astype(str)
    agg = {"Nº NF": "count", "Quantidade": "sum", "Valor Produtos": "sum",
           "IPI": "sum", "ICMS": "sum", "Valor Total NF": "sum"}
    ren = {"Nº NF": "Qtd. Notas"}
    out = {
        "Resumo Mensal": v.groupby("Mês").agg(agg).rename(columns=ren).reset_index(),
        "Resumo Clientes": v.groupby(["CPF/CNPJ Cliente", "Cliente"], dropna=False).agg(agg)
                            .rename(columns=ren).sort_values("Valor Total NF", ascending=False).reset_index(),
        "Resumo Empresas": v.groupby("Empresa").agg(agg).rename(columns=ren).reset_index(),
        "Resumo Operações": v.groupby(["Cód. Operação", "Operação"], dropna=False).agg(agg)
                             .rename(columns=ren).sort_values("Valor Total NF", ascending=False).reset_index(),
    }
    for nome, d in out.items():
        total = {c: d[c].sum() for c in d.columns if c in ("Qtd. Notas", "Quantidade", "Valor Produtos", "IPI", "ICMS", "Valor Total NF")}
        total[d.columns[0]] = "TOTAL"
        out[nome] = pd.concat([d, pd.DataFrame([total])], ignore_index=True)
    return out


# --------------------------------------------------------------------------
# Relatório 150 (FISCAL_0150) - mesmo layout da exportação do TOTVS
# --------------------------------------------------------------------------
OPERATIONS_URL = f"{BASE_URL}/api/totvsmoda/general/v2/operations"
LEGAL_URL = f"{BASE_URL}/api/totvsmoda/person/v2/legal-entities/search"
INDIVIDUAL_URL = f"{BASE_URL}/api/totvsmoda/person/v2/individuals/search"
REPRESENTATIVE_URL = f"{BASE_URL}/api/totvsmoda/person/v2/representatives/search"
ORDERS_URL = f"{BASE_URL}/api/totvsmoda/sales-order/v2/orders/search"
PRODUCTS_URL = f"{BASE_URL}/api/totvsmoda/product/v2/products/search"
SWAGGERS = {
    "person": f"{BASE_URL}/api/totvsmoda/person/v2/swagger/v1/swagger.json",
    "product": f"{BASE_URL}/api/totvsmoda/product/v2/swagger/v1/swagger.json",
    "sales-order": f"{BASE_URL}/api/totvsmoda/sales-order/v2/swagger/v1/swagger.json",
}

COLUNAS_150 = ["Emp", "COD.REP", "REPRE", "COD.CLI", "CLIENTE", "FANTASIA", "OPER", "DESC.", "DATA",
               "NFE", "CODIDO", "ARTIGO", "QUANT", "ESP.", "GRAMATURA", "SEGMENTO", "UNIT.LIQ.",
               "TOT.LIQUID", "EMISSAO", "UF", "Financeiro", "Vl. Freterat", "Vl. Segurorat",
               "Vl. Icms", "Bs calc pis cofins", "Pr. Aliqcofins", "Pr. Aliqpis",
               "Vl. Vlr Cofins", "Vl. Vlr Pis"]
TOTAIS_150 = ["QUANT", "TOT.LIQUID", "Vl. Icms"]

# Operações do 150. "financeiro" = só operações que movimentam financeiro
# (igual à coluna Financeiro=SIM). Ou informe códigos: "500,526,545,546".
OPERACOES_150 = os.getenv("RELATORIO150_OPERACOES", "financeiro").strip().lower()


class Diagnostico:
    """Guarda requisições/respostas das consultas extras para ajuste fino."""

    def __init__(self, pasta: Path):
        self.pasta = pasta
        self.problemas: list[str] = []

    def salvar(self, nome: str, conteudo) -> None:
        try:
            self.pasta.mkdir(parents=True, exist_ok=True)
            texto = conteudo if isinstance(conteudo, str) else json.dumps(conteudo, ensure_ascii=False, indent=1)
            (self.pasta / nome).write_text(texto, encoding="utf-8")
        except Exception:
            pass

    def problema(self, msg: str) -> None:
        self.problemas.append(msg)
        print(f"  ! {msg}")

    def baixar_swaggers(self, cli: "TotvsModaClient") -> None:
        for nome, url in SWAGGERS.items():
            try:
                r = cli.session.get(url, timeout=120)
                if r.status_code == 200:
                    self.salvar(f"swagger_{nome}.json", r.text)
            except Exception:
                pass


def _lotes(lista: list, n: int):
    for i in range(0, len(lista), n):
        yield lista[i:i + n]


def _post_paginado(cli: "TotvsModaClient", url: str, corpo: dict, diag: Diagnostico, nome: str,
                   max_paginas: int = 200) -> list[dict] | None:
    itens, page = [], 1
    while page <= max_paginas:
        body = {**corpo, "page": page}
        r = cli.post(url, body)
        if page == 1:
            diag.salvar(f"{nome}_requisicao.json", body)
            diag.salvar(f"{nome}_resposta.json", r.text[:200000])
        if r.status_code == 404:
            return itens
        if r.status_code != 200:
            return None if page == 1 else itens
        j = r.json()
        if isinstance(j, list):
            return j
        itens.extend(j.get("items") or [])
        if not j.get("hasNext"):
            break
        page += 1
    return itens


def _tentar(cli, url, corpos: list[dict], diag: Diagnostico, nome: str) -> list[dict] | None:
    """Tenta variações de corpo até uma ser aceita pela API."""
    for i, corpo in enumerate(corpos):
        res = _post_paginado(cli, url, corpo, diag, f"{nome}_v{i + 1}")
        if res is not None:
            return res
    return None


_ROTULOS = ("name", "typename", "description", "title", "typedescription", "fieldname", "label")


def _valor_atributo(obj, palavras: tuple[str, ...]):
    """Procura recursivamente um atributo (classificação, campo adicional ou
    chave) cujo nome contenha uma das palavras. Retorna o valor encontrado."""
    if isinstance(obj, dict):
        # chave direta: {"grammage": 160} / {"segmento": "X"}
        for k, v in obj.items():
            kl = k.lower()
            if any(p in kl for p in palavras) and not isinstance(v, (dict, list)) and v not in (None, ""):
                return v
        # par rótulo/valor: {"typeName": "SEGMENTO", "name": "COLCHAO MI"}
        for k, v in obj.items():
            if k.lower() in _ROTULOS and isinstance(v, str) and any(p in v.lower() for p in palavras):
                for alvo in ("value", "name", "description", "code"):
                    for k2, v2 in obj.items():
                        if k2.lower() == alvo and k2 != k and v2 not in (None, ""):
                            return v2
        for v in obj.values():
            achado = _valor_atributo(v, palavras)
            if achado not in (None, ""):
                return achado
    elif isinstance(obj, list):
        for v in obj:
            achado = _valor_atributo(v, palavras)
            if achado not in (None, ""):
                return achado
    return None


def _primeira_chave(obj: dict, *nomes):
    for n in nomes:
        for k, v in obj.items():
            if k.lower() == n.lower() and v not in (None, ""):
                return v
    return None


def buscar_operacoes(cli, codigos: list[int], diag: Diagnostico) -> dict[int, dict]:
    res: dict[int, dict] = {}
    for lote in _lotes(sorted(set(codigos)), 100):
        r = cli.get(OPERATIONS_URL, {"OperationCodeList": lote, "PageSize": 1000})
        diag.salvar("operacoes_resposta.json", r.text[:200000])
        if r.status_code != 200:
            diag.problema(f"operações: HTTP {r.status_code} {r.text[:150]}")
            continue
        for op in r.json().get("items") or []:
            res[int(op.get("operationCode"))] = op
    return res


def buscar_pessoas(cli, codigos: list[int], diag: Diagnostico) -> dict[int, dict]:
    res: dict[int, dict] = {}
    pendentes = sorted(set(c for c in codigos if c))
    for url, nome in ((LEGAL_URL, "pessoa_juridica"), (INDIVIDUAL_URL, "pessoa_fisica")):
        if not pendentes:
            break
        for lote in _lotes(pendentes, 100):
            itens = _tentar(cli, url, [
                {"filter": {"personCodeList": lote}, "pageSize": 1000, "expand": "representatives"},
                {"filter": {"personCodeList": lote}, "pageSize": 1000},
                {"filter": {"personCodeList": lote}, "pageSize": 100},
            ], diag, nome)
            if itens is None:
                diag.problema(f"{nome}: consulta recusada (veja diagnostico/{nome}_*)")
                break
            for p in itens:
                cod = _primeira_chave(p, "code", "personCode")
                if cod is not None:
                    res[int(cod)] = p
        pendentes = [c for c in pendentes if c not in res]
    return res


def buscar_representantes_pedidos(cli, pedidos: set[tuple[int, int]], diag: Diagnostico) -> dict:
    """(empresa, pedido) -> (cód. representante, nome)"""
    res: dict[tuple[int, int], tuple] = {}
    por_emp: dict[int, list[int]] = {}
    for emp, ped in pedidos:
        por_emp.setdefault(emp, []).append(ped)
    for emp, peds in por_emp.items():
        for lote in _lotes(sorted(set(peds)), 100):
            itens = _tentar(cli, ORDERS_URL, [
                {"filter": {"branchCodeList": [emp], "orderCodeList": lote}, "pageSize": 100},
                {"filter": {"branchCodeList": [emp], "orderCodeList": lote}, "pageSize": 100,
                 "expand": "representative"},
            ], diag, "pedidos")
            if itens is None:
                diag.problema("pedidos de venda: consulta recusada (veja diagnostico/pedidos_*)")
                return res
            for o in itens:
                cod_ped = _primeira_chave(o, "orderCode", "code")
                rep = _primeira_chave(o, "representativeCode", "representativeId")
                if rep is None:
                    rep = _valor_atributo(o, ("representativecode",))
                nome = _primeira_chave(o, "representativeName") or _valor_atributo(o, ("representativename",))
                if cod_ped is not None:
                    res[(emp, int(cod_ped))] = (rep, nome)
    return res


def buscar_nomes_representantes(cli, codigos: list[int], diag: Diagnostico) -> dict[int, str]:
    res: dict[int, str] = {}
    cods = sorted(set(int(c) for c in codigos if c not in (None, "")))
    for lote in _lotes(cods, 100):
        itens = _tentar(cli, REPRESENTATIVE_URL, [
            {"filter": {"personCodeList": lote}, "pageSize": 1000},
            {"filter": {"representativeCodeList": lote}, "pageSize": 1000},
        ], diag, "representantes")
        for p in itens or []:
            cod = _primeira_chave(p, "code", "personCode", "representativeCode")
            nome = _primeira_chave(p, "name", "personName", "representativeName")
            if cod is not None and nome:
                res[int(cod)] = nome
    faltam = [c for c in cods if c not in res]
    if faltam:  # representante também é pessoa (PJ/PF)
        for cod, p in buscar_pessoas(cli, faltam, diag).items():
            nome = _primeira_chave(p, "name", "personName")
            if nome:
                res[cod] = nome
    return res


def buscar_produtos(cli, codigos: list[int], empresa: int, diag: Diagnostico) -> dict[int, dict]:
    res: dict[int, dict] = {}
    for lote in _lotes(sorted(set(codigos)), 100):
        filtro = {"productCodeList": lote}
        itens = _tentar(cli, PRODUCTS_URL, [
            {"filter": filtro, "option": {"branchInfoCode": empresa}, "pageSize": 1000,
             "expand": "classifications,additionalFields"},
            {"filter": filtro, "option": {"branchInfoCode": empresa}, "pageSize": 1000},
            {"filter": filtro, "pageSize": 1000, "expand": "classifications,additionalFields"},
            {"filter": filtro, "pageSize": 1000},
        ], diag, "produtos")
        if itens is None:
            diag.problema("produtos: consulta recusada (veja diagnostico/produtos_*)")
            return res
        for p in itens:
            cod = _primeira_chave(p, "productCode", "code")
            if cod is not None:
                res[int(cod)] = p
    return res


def _clas_seg(prod: dict):
    for c in prod.get("classifications") or []:
        nome = f"{c.get('typeName') or ''} {c.get('typeNameAux') or ''}".upper()
        if c.get("typeCode") == 504 or "CLAS SEG" in nome or "SEGMENTO" in nome:
            return c.get("code")
    return None


def segmento_produto(prod: dict, conhecidos: list[str] | None = None):
    """Segmento como o Relatório 150 do TOTVS mostra (ex.: COLCHAO MI).
    O 150 usa o segmento da REFERÊNCIA (início do ReferenceCode, ex.:
    "COLCHAO MI PL 3030041"); se não der para identificar, usa a
    classificação "CLAS SEG" (tipo 504) do próprio produto."""
    ref = (prod.get("ReferenceCode") or prod.get("referenceCode") or "").strip()
    for seg in sorted(conhecidos or [], key=len, reverse=True):
        if seg and (ref == seg or ref.startswith(seg + " ")):
            return seg
    for c in prod.get("classifications") or []:
        nome = f"{c.get('typeName') or ''} {c.get('typeNameAux') or ''}".upper()
        if c.get("typeCode") == 504 or "CLAS SEG" in nome or "SEGMENTO" in nome:
            return c.get("code") or c.get("name")
    return _valor_atributo(prod, ("segment",))


def _imposto(item: dict, *nomes: str) -> dict:
    for t in item.get("taxes") or []:
        n = (t.get("name") or "").upper()
        if any(x in n for x in nomes):
            return t
    return {}


def _cod_produto(it: dict):
    prods = it.get("products") or []
    if prods and prods[0].get("productCode") is not None:
        return int(prods[0]["productCode"])
    try:
        return int(str(it.get("code")).strip())
    except (TypeError, ValueError):
        return None


def montar_relatorio150(cli, notas: list[dict], pasta_diag: Path) -> tuple[pd.DataFrame, Diagnostico]:
    diag = Diagnostico(pasta_diag)
    validas = [n for n in notas if n.get("invoiceStatus") in STATUS_VALIDOS]
    print(f"\nMontando Relatório 150 ({len(validas)} notas válidas)...")

    ops = buscar_operacoes(cli, [n.get("operationCode") for n in validas if n.get("operationCode")], diag)
    print(f"  operações: {len(ops)}")

    if OPERACOES_150 not in ("", "financeiro", "todas"):
        permitidas = {int(x) for x in OPERACOES_150.replace(";", ",").split(",") if x.strip()}
        validas = [n for n in validas if n.get("operationCode") in permitidas]
    elif OPERACOES_150 == "financeiro" and ops:
        validas = [n for n in validas if ops.get(n.get("operationCode"), {}).get("isFinancial")]
    if OPERACOES_150 in ("financeiro", "vendas"):
        # Relatório 150 = vendas (saídas) + devoluções de venda (entradas). Compras, serviços tomados, frete etc. ficam de fora.
        def _saida(n):
            return str(n.get("operationType", "")).lower() in ("output", "2", "s", "saida", "saída")
        validas = [n for n in validas if _saida(n) or "DEV" in str(n.get("operatioName") or "").upper()]
    print(f"  notas no relatório após filtro de operações ({OPERACOES_150}): {len(validas)}")

    pessoas = buscar_pessoas(cli, [n.get("personCode") for n in validas], diag)
    print(f"  clientes: {len(pessoas)}")

    pedidos = {(int(s.get("branchCode") or n.get("branchCode")), int(s["orderCode"]))
               for n in validas for s in (n.get("salesOrder") or []) if s.get("orderCode")}
    reps_ped = buscar_representantes_pedidos(cli, pedidos, diag) if pedidos else {}
    # Devoluções (e notas sem pedido de venda) não têm representante no pedido: vale o representante do cadastro do cliente.
    def _rep_cadastro(n):
        for r in (pessoas.get(n.get("personCode"), {}).get("representatives") or []):
            if r.get("representativeCode") is not None:
                return int(r["representativeCode"])
        return None
    nomes_rep = {}
    cods_sem_nome = [r for r, nm in reps_ped.values() if r is not None and not nm]
    for cod, nm in reps_ped.values():
        if cod is not None and nm:
            NOMES_REP[int(cod)] = nm
    cods_sem_nome += [c for c in (_rep_cadastro(n) for n in validas) if c is not None]
    cods_sem_nome = [c for c in cods_sem_nome if int(c) not in NOMES_REP]
    if cods_sem_nome:
        nomes_rep = buscar_nomes_representantes(cli, cods_sem_nome, diag)
    print(f"  pedidos com representante: {sum(1 for r, _ in reps_ped.values() if r is not None)}/{len(pedidos)}")

    produtos: dict[int, dict] = {}
    por_emp: dict[int, list[int]] = {}
    for n in validas:
        for it in n.get("items") or []:
            c = _cod_produto(it)
            if c is not None:
                por_emp.setdefault(n.get("branchCode"), []).append(c)
    for emp, cods in por_emp.items():
        produtos.update(buscar_produtos(cli, cods, emp, diag))
    print(f"  produtos: {len(produtos)}")
    segmentos = sorted({s for s in (_clas_seg(p) for p in produtos.values()) if s})

    linhas = []
    for n in sorted(validas, key=lambda x: (x.get("invoiceDate") or "", x.get("invoiceCode") or 0)):
        pessoa = pessoas.get(n.get("personCode"), {})
        endereco = n.get("person") or {}
        op = ops.get(n.get("operationCode"), {})
        rep_cod, rep_nome = None, None
        for s in n.get("salesOrder") or []:
            chave = (int(s.get("branchCode") or n.get("branchCode")), int(s.get("orderCode") or 0))
            if chave in reps_ped:
                rep_cod, rep_nome = reps_ped[chave]
                break
        if rep_cod is None:
            rep_cod = _rep_cadastro(n)
        if rep_cod is not None and not rep_nome:
            rep_nome = NOMES_REP.get(int(rep_cod)) or nomes_rep.get(int(rep_cod))
        for it in n.get("items") or []:
            cod = _cod_produto(it)
            prod = produtos.get(cod, {}) if cod is not None else {}
            icms = _imposto(it, "ICMS")
            cofins = _imposto(it, "COFINS")
            pis = _imposto(it, "PIS")
            gram = _valor_atributo(prod, ("gramat", "grammage"))
            try:
                gram = int(float(str(gram).replace(",", "."))) if gram not in (None, "") else None
            except ValueError:
                pass
            linhas.append({
                "Emp": n.get("branchCode"),
                "COD.REP": rep_cod,
                "REPRE": rep_nome,
                "COD.CLI": n.get("personCode"),
                "CLIENTE": n.get("personName") or endereco.get("personName"),
                "FANTASIA": _primeira_chave(pessoa, "fantasyName"),
                "OPER": n.get("operationCode"),
                "DESC.": n.get("operatioName"),
                "DATA": _d(n.get("invoiceDate")),
                "NFE": n.get("invoiceCode"),
                "CODIDO": cod,
                "ARTIGO": it.get("name"),
                "QUANT": it.get("quantity"),
                "ESP.": it.get("measureUnit"),
                "GRAMATURA": gram,
                "SEGMENTO": segmento_produto(prod, segmentos),
                "UNIT.LIQ.": it.get("unitNetValue"),
                "TOT.LIQUID": it.get("netValue"),
                "EMISSAO": _d(n.get("issueDate")),
                "UF": endereco.get("stateAbbreviation"),
                "Financeiro": ("SIM" if op.get("isFinancial") else "NAO") if op else None,
                "Vl. Freterat": it.get("freightValue") or 0,
                "Vl. Segurorat": it.get("insuranceValue") or 0,
                "Vl. Icms": icms.get("taxValue") or 0,
                "Bs calc pis cofins": (cofins.get("calculationBasisValue") or pis.get("calculationBasisValue")
                                       or round((it.get("netValue") or 0) - (icms.get("taxValue") or 0), 2)),
                "Pr. Aliqcofins": cofins.get("taxPercentage") or 0,
                "Pr. Aliqpis": pis.get("taxPercentage") or 0,
                "Vl. Vlr Cofins": cofins.get("taxValue") or 0,
                "Vl. Vlr Pis": pis.get("taxValue") or 0,
            })
    df = pd.DataFrame(linhas, columns=COLUNAS_150)

    if not df.empty:
        for col, rotulo in (("REPRE", "representante"), ("FANTASIA", "nome fantasia"),
                            ("GRAMATURA", "gramatura"), ("SEGMENTO", "segmento")):
            vazias = int(df[col].isna().sum())
            if vazias:
                diag.problema(f"{rotulo}: {vazias} de {len(df)} linhas sem valor")
    if diag.problemas:
        diag.baixar_swaggers(cli)
    return df, diag


def salvar_csv_150(df: pd.DataFrame, caminho: Path) -> None:
    """CSV no mesmo formato da exportação do TOTVS (; e vírgula decimal)."""
    def fmt(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        if isinstance(v, (date, datetime, pd.Timestamp)):
            return v.strftime("%d/%m/%Y")
        if isinstance(v, float):
            return (f"{v:.6f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))).replace(".", ",")
        return str(v)

    linhas = [";".join(COLUNAS_150)]
    for _, r in df.iterrows():
        linhas.append(";".join(fmt(r[c]) for c in COLUNAS_150))
    tot = [fmt(float(df[c].fillna(0).sum())) if c in TOTAIS_150 else "" for c in COLUNAS_150]
    linhas.append(";".join(tot))
    # newline="" evita a linha em branco extra entre os registros no Windows
    with open(caminho, "w", encoding="cp1252", errors="replace", newline="") as f:
        f.write("\r\n".join(linhas) + "\r\n")


# Larguras e formatos do Relatório 150 no Excel
LARGURAS_150 = {"Emp": 6, "COD.REP": 11, "REPRE": 32, "COD.CLI": 9, "CLIENTE": 40, "FANTASIA": 22,
                "OPER": 7, "DESC.": 30, "DATA": 11, "NFE": 9, "CODIDO": 9, "ARTIGO": 45, "QUANT": 11,
                "ESP.": 6, "GRAMATURA": 11, "SEGMENTO": 13, "UNIT.LIQ.": 11, "TOT.LIQUID": 13,
                "EMISSAO": 11, "UF": 5, "Financeiro": 10, "Vl. Freterat": 11, "Vl. Segurorat": 12,
                "Vl. Icms": 11, "Bs calc pis cofins": 15, "Pr. Aliqcofins": 12, "Pr. Aliqpis": 10,
                "Vl. Vlr Cofins": 13, "Vl. Vlr Pis": 11}
FORMATOS_150 = {"COD.REP": "0", "COD.CLI": "0", "NFE": "0", "CODIDO": "0", "OPER": "0", "Emp": "0",
                "GRAMATURA": "0", "DATA": "DD/MM/YYYY", "EMISSAO": "DD/MM/YYYY",
                "QUANT": "#,##0.###;-#,##0.###;0", "UNIT.LIQ.": "#,##0.00####",
                "TOT.LIQUID": "#,##0.00", "Vl. Freterat": "#,##0.00", "Vl. Segurorat": "#,##0.00",
                "Vl. Icms": "#,##0.00", "Bs calc pis cofins": "#,##0.00", "Pr. Aliqcofins": "0.00",
                "Pr. Aliqpis": "0.00", "Vl. Vlr Cofins": "#,##0.00##", "Vl. Vlr Pis": "#,##0.00##"}


def formatar_aba_150(ws, tem_total: bool = True) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    cab = [c.value for c in ws[1]]
    ultima = ws.max_row
    linhas_dados = ultima - 1 if tem_total else ultima
    for i, nome in enumerate(cab, start=1):
        letra = get_column_letter(i)
        ws.column_dimensions[letra].width = LARGURAS_150.get(nome, 12)
        fmt = FORMATOS_150.get(nome)
        for c in ws[letra][1:]:
            if fmt:
                c.number_format = fmt
            if nome in ("UF", "Financeiro", "ESP.", "Emp", "OPER"):
                c.alignment = Alignment(horizontal="center")
    azul = PatternFill("solid", fgColor="1F4E9A")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = azul
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 20
    ws.freeze_panes = "A2"
    ultima_col = get_column_letter(len(cab))
    ws.auto_filter.ref = f"A1:{ultima_col}{max(linhas_dados, 1)}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:1"
    if tem_total and ultima > 1:
        cinza = PatternFill("solid", fgColor="D9E1F2")
        linha_sup = Border(top=Side(style="thin", color="1F4E9A"))
        for c in ws[ultima]:
            c.font = Font(bold=True)
            c.fill = cinza
            c.border = linha_sup


def salvar_xlsx_150(df: pd.DataFrame, caminho: Path) -> None:
    """Relatório 150 sozinho, formatado (filtro, larguras, números e datas)."""
    with pd.ExcelWriter(caminho, engine="openpyxl") as xw:
        df_150_com_total(df).to_excel(xw, sheet_name="FISCAL_0150", index=False)
        formatar_aba_150(xw.sheets["FISCAL_0150"])


def df_150_com_total(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    total = {c: (df[c].fillna(0).sum() if c in TOTAIS_150 else None) for c in COLUNAS_150}
    total["Emp"] = "TOTAL"
    return pd.concat([df, pd.DataFrame([total])], ignore_index=True)


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------
def salvar_excel(caminho: Path, abas: dict[str, pd.DataFrame]) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    azul = PatternFill("solid", fgColor="1F4E9A")
    with pd.ExcelWriter(caminho, engine="openpyxl") as xw:
        for nome, df in abas.items():
            df.to_excel(xw, sheet_name=nome[:31], index=False)
            ws = xw.sheets[nome[:31]]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = azul
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            for i, col in enumerate(df.columns, start=1):
                letra = get_column_letter(i)
                largura = max([len(str(col))] + [len(str(x)) for x in df[col].head(500).tolist()])
                ws.column_dimensions[letra].width = min(max(10, largura + 2), 50)
                nome_col = str(col)
                fmt = None
                if nome_col.startswith(("Data",)) or nome_col in ("DATA", "EMISSAO"):
                    fmt = "DD/MM/YYYY"
                elif nome_col in ("Quantidade", "QUANT"):
                    fmt = "#,##0.000"
                elif nome_col in ("UNIT.LIQ.",):
                    fmt = "#,##0.000000"
                elif nome_col in ("TOT.LIQUID", "Bs calc pis cofins"):
                    fmt = "#,##0.00"
                elif nome_col.startswith("Pr. Aliq"):
                    fmt = "0.00"
                elif nome_col in ("Qtd. Notas",):
                    fmt = "#,##0"
                elif any(k in nome_col for k in ("Valor", "Vl.", "IPI", "ICMS", "Frete", "Seguro",
                                                   "Desp.", "Desconto", "Base", "Imp.")) and "%" not in nome_col:
                    fmt = "#,##0.00"
                if fmt:
                    for c in ws[letra][1:]:
                        c.number_format = fmt
            if nome.startswith("Resumo") and ws.max_row > 1:
                for c in ws[ws.max_row]:
                    c.font = Font(bold=True)
            if nome == "Relatório 150":
                formatar_aba_150(ws, tem_total=not df.empty)


