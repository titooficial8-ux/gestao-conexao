"""Financeiro: titulos a receber/pagar, vencidos, exportacao, prorrogacao de vencimento e verificacao dos recursos do TOTVS (so leitura)."""
import io
import os
import threading
from datetime import date

from flask import Blueprint, Response, abort, jsonify, request, send_file
from flask_login import current_user, login_required

from app.models import Tab

fin_bp = Blueprint('financeiro', __name__, url_prefix='/financeiro')


def _autorizar():
    tab = Tab.query.filter_by(slug='painel-financeiro').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


@fin_bp.route('/dados')
@login_required
def dados():
    _autorizar()
    from app.totvs import financeiro
    if not financeiro.ler().get('atualizado') and not financeiro.ESTADO['rodando']:         # primeira vez: le agora
        threading.Thread(target=financeiro.atualizar, daemon=True).start()
    return jsonify(financeiro.painel())


@fin_bp.route('/verificacao')
@login_required
def verificacao():
    """O que o TOTVS oferece (boleto, alteracao de vencimento): so lista endpoints do swagger, nao chama nenhum."""
    _autorizar()
    from app.totvs import api, financeiro
    try:
        cli = api.TotvsModaClient()
        cli.autenticar()
        return jsonify(ok=True, **financeiro.verificacao(cli))
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, erro=str(e)[:300])


@fin_bp.route('/boleto-teste', methods=['POST'])
@login_required
def boleto_teste():
    """So administrador: pede ao TOTVS o boleto de UMA parcela, para verificar se ele devolve o PDF."""
    if not current_user.is_admin:
        abort(403)
    from flask import Response
    from app.totvs import financeiro
    j = request.get_json(silent=True) or {}
    try:
        pdf, log = financeiro.boleto_totvs(str(j.get('doc', '')), str(j.get('parcela', '')), str(j.get('cod', '')), str(j.get('cnpj', '')))
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, log=[str(e)[:300]])
    if pdf:
        return Response(pdf, mimetype='application/pdf', headers={'Content-Disposition': f"attachment; filename=boleto_{j.get('doc')}_{j.get('parcela')}.pdf"})
    return jsonify(ok=False, log=log)


@fin_bp.route('/previa-boleto')
@login_required
def previa_boleto():
    """PRE-VISUALIZACAO no estilo da tela FCRFP098 do TOTVS (Impressao de Boleto Bancario). NAO e boleto e nada e gravado no TOTVS: mostra os boletos ainda nao gerados
    com o VENCIMENTO SUGERIDO pela regra de pagamento do cliente. ?doc=69030&parcela=1 destaca uma parcela; ?cliente=MARANHAO filtra o cliente."""
    _autorizar()
    from html import escape as h
    from app.totvs import financeiro as F
    doc, parcela, cli = (request.args.get('doc') or '').strip(), (request.args.get('parcela') or '').strip(), (request.args.get('cliente') or '').strip().upper()
    p = F.painel()
    todos = p['abertos_regra']
    if doc:                                                    # com ?doc= mostra o cliente dessa fatura e destaca a parcela
        ref = next((x for x in todos if str(x['doc']) == doc and (not parcela or str(x['parcela']) == parcela)), None)
        cli = str(ref['cliente']).upper() if ref else cli
    itens = [x for x in todos if (not cli or cli in str(x['cliente']).upper())] if (not doc or cli) else []
    if not itens:
        return Response('<meta charset="utf-8"><p style="font:16px Arial;padding:30px">Nenhum boleto não gerado encontrado para esse filtro.</p>', status=404, mimetype='text/html')
    dd = lambda s: date.fromisoformat(s).strftime('%d/%m/%Y') if s else ''
    brl = lambda v: f"{v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    novo_de = lambda x: x['novo'] or x['venc']
    emis = sorted(x['emissao'] for x in itens if x.get('emissao'))
    codigos = {}
    for t in F.ler().get('receber', []):
        codigos[(str(t.get('doc')), str(t.get('parcela')))] = t.get('cod')
    linhas = ''
    for x in itens:
        dest = bool(doc and str(x['doc']) == doc and (not parcela or str(x['parcela']) == parcela))
        mudou = bool(x['novo'])
        linhas += (f"<tr class=\"{'sel' if dest else ''}\"><td>001</td><td class=\"n\">{h(str(codigos.get((str(x['doc']), str(x['parcela']))) or ''))}</td><td>{h(str(x['cliente']))}</td>"
                   f"<td class=\"n\">{h(str(x['doc']))}</td><td class=\"n\">{h(str(x['parcela']))}</td><td>{dd(x['emissao'])}</td><td class=\"{'mud' if mudou else ''}\">{dd(novo_de(x))}</td><td class=\"n\">1.020</td><td>Fatura</td>"
                   f"<td class=\"n\">{brl(x['valor'])}</td><td></td><td class=\"o\"></td><td class=\"o\"></td><td class=\"o\">{dd(x['venc']) if mudou else ''}</td><td><span class=\"ck\"></span></td></tr>")
    total = sum(x['valor'] for x in itens)
    ex = ''
    if doc:
        ref = next((x for x in itens if str(x['doc']) == doc and (not parcela or str(x['parcela']) == parcela)), itens[0])
        rg = F.regra_do_cliente(ref['cliente'])
        if rg and ref['novo']:
            passos = F.explicar_prorrogacao(rg, date.fromisoformat(ref['venc']))
            ex = '<div class="ex"><b>Como a regra chegou na data</b> (' + h(rg['nome'] + ' — ' + rg['texto']) + ')<br>' + ' &rarr; '.join(
                f"{d.strftime('%d/%m')} {F.SEMANA[d.weekday()][:3]}: " + ('<span class="okk">válido</span>' if not m else '<span class="nok">' + h(m.split(' (')[0]) + '</span>') for d, m in passos) + '</div>'
    corpo = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>FCRFP098 – prévia</title><style>
body{{margin:0;background:#c9d2e3;font:12px Arial,Helvetica,sans-serif;color:#000}}.jan{{max-width:1180px;margin:14px auto;background:#f0f0f0;border:1px solid #6b7a99;box-shadow:0 4px 18px rgba(0,0,0,.3)}}
.tit{{background:linear-gradient(#b5c3de,#98aad0);padding:5px 8px;font-weight:700;display:flex;justify-content:space-between;border-bottom:1px solid #6b7a99}}.tit i{{font-style:normal;border:1px solid #333;background:#fff;padding:0 6px}}
.tool{{padding:6px 12px;color:#444;font-size:15px;letter-spacing:10px}}.marca{{background:#dc2626;color:#fff;text-align:center;font-weight:900;padding:6px;margin:0 12px 8px;animation:pisca 1.2s infinite}}@keyframes pisca{{50%{{opacity:.55}}}}
fieldset{{border:1px solid #9aa4b8;margin:6px 12px;padding:6px 10px;background:#f0f0f0}}legend{{font-weight:700;padding:0 4px}}
.f{{display:grid;grid-template-columns:130px 1fr;gap:3px 6px;align-items:center}}.f label{{text-align:left}}.in{{background:#fff;border:1px solid #8a94a8;min-height:16px;padding:1px 4px}}.in.g{{background:#e4e4e4;color:#444}}.l2{{display:flex;gap:6px}}
.sit{{display:flex;gap:14px;align-items:center;margin-top:4px;flex-wrap:wrap}}table{{border-collapse:collapse;width:100%;background:#fff}}th{{color:#1c2fa6;font-weight:400;border:1px solid #9aa4b8;border-bottom:2px solid #1c2fa6;padding:3px 5px;white-space:nowrap}}
td{{border:1px solid #d0d4de;padding:2px 5px;white-space:nowrap}}td.n{{text-align:right}}tr.sel td{{background:#cdeefb}}td.mud{{background:#fff3b0;font-weight:700;color:#7a2e00}}th.o,td.o{{background:#fde8e8;color:#8a1c1c}}th.o{{color:#8a1c1c;border-bottom-color:#8a1c1c}}
.ck{{display:inline-block;width:12px;height:12px;border:1px solid #666;background:#fff}}.rod{{display:flex;justify-content:flex-end;gap:10px;margin:6px 12px 0;align-items:center}}.rod span{{font-weight:700}}.rod .v{{background:#ffffd0;border:1px solid #9aa4b8;min-width:110px;text-align:right;padding:2px 6px}}
.ex{{margin:8px 12px;padding:8px 10px;background:#fff;border:1px solid #9aa4b8;line-height:1.7}}.okk{{color:#166534;font-weight:700}}.nok{{color:#b91c1c}}.bt{{margin:10px 12px 12px;display:flex;gap:10px;align-items:center;color:#444}}.bt button{{border:1px solid #666;background:#e8e8e8;padding:4px 12px;cursor:pointer}}@media print{{.bt button{{display:none}}body{{background:#fff}}}}
</style></head><body><div class="jan">
<div class="tit"><span>FCRFP098 - Impressão de Boleto Bancário &nbsp;·&nbsp; PRÉVIA</span><i>×</i></div><div class="tool">&#8634; &#9906; &nbsp; &#9113; &#9638; &#9432; &#8677;</div>
<div class="marca">PRÉ-VISUALIZAÇÃO PARA CONFERÊNCIA · NÃO É O TOTVS E NÃO É BOLETO · NADA FOI GRAVADO · vencimento = sugerido pela regra de pagamento do cliente</div>
<fieldset><legend>Filtro</legend><div class="f"><label>Conta *</label><div class="l2"><div class="in" style="width:90px;text-align:right">13007732</div><div class="in g" style="flex:1">130077322</div></div>
<label>Modalidade *</label><div class="in g">Cobrança simples direta</div><label>Empresa *</label><div class="l2"><div class="in g" style="width:50px">001</div><div class="in g" style="width:50px">001</div></div>
<label>Cliente</label><div class="in">{h(cli)}</div><label>Portador</label><div class="in">1.020 — TITULO EM CARTEIRA</div><label>Nota fiscal</label><div class="in"></div><label>Transação</label><div class="in"></div>
<label>Data emissão</label><div class="l2"><div class="in" style="width:90px">{dd(emis[0]) if emis else ''}</div><div class="in" style="width:90px">{dd(emis[-1]) if emis else ''}</div></div><label>Data vencimento</label><div class="in"></div></div>
<div class="sit"><span>Situação impressão: <span class="in">Não impressa</span></span><span>Tipo de documento: <span class="in">Fatura e Nota de Débito</span></span><span>Tipo de impressão: <span class="in">Boleto</span></span></div></fieldset>
<fieldset><legend>Documento</legend><div style="overflow-x:auto"><table><tr><th>Emp.</th><th>Cód.</th><th>Cliente</th><th>Fatura/Parc.</th><th>&raquo;</th><th>Emissão</th><th>Vencimento</th><th>Port.</th><th>Documento</th><th>Valor</th><th>Nosso número</th><th class="o">Operador</th><th class="o">Dt. impressão</th><th class="o">Venc. atual (TOTVS)</th><th>&#9632;</th></tr>
{linhas}</table></div></fieldset>
<div class="rod"><span>Valor total</span><div class="v">{brl(total)}</div><span>Quantidade</span><div class="v">{len(itens)}</div></div>{ex}
<div class="bt"><button onclick="window.print()">Imprimir / salvar em PDF</button> Colunas em vermelho claro e a data amarela só existem nesta prévia: o <b>Vencimento</b> destacado é o sugerido pela regra; o atual no TOTVS está na última coluna.</div></div></body></html>"""
    return Response(corpo, mimetype='text/html')


_ENDERECOS = {}
BENEF = dict(nome=os.getenv('BOLETO_BENEF_NOME', 'CONEXAO MALHAS'), cnpj=os.getenv('BOLETO_BENEF_CNPJ', '74.283.375/0001-42'), rua=os.getenv('BOLETO_BENEF_RUA', 'AV. BENEDITO QUINA DA SILVA'),
             cep=os.getenv('BOLETO_BENEF_CEP', '13212-141'), agencia=os.getenv('BOLETO_BENEF_AGENCIA', '3853- / 0544684'), carteira=os.getenv('BOLETO_CARTEIRA', 'RCR'))


def _endereco_cliente(cod, cnpj):
    """Razao social e endereco do pagador, como o TOTVS imprime no boleto (consulta de leitura ao cadastro de pessoas)."""
    if cod in _ENDERECOS:
        return _ENDERECOS[cod]
    out = dict(nome='', cnpj=cnpj or '', end='', cep_cidade='')
    try:
        from app.totvs import api
        cli = api.TotvsModaClient()
        cli.autenticar()
        r = cli.post(api.LEGAL_URL, dict(filter=dict(personCodeList=[int(cod)]), expand='addresses', page=1, pageSize=5))
        for x in (r.json().get('items') or []) if r.status_code == 200 else []:
            ad = next((a for a in (x.get('addresses') or []) if a.get('addressType') == 'Commercial'), (x.get('addresses') or [None])[0]) or {}
            cep = str(ad.get('cep') or '')
            out = dict(nome=x.get('name') or '', cnpj=x.get('cnpj') or cnpj or '',
                       end=f"{ad.get('publicPlace') or ''} {ad.get('address') or ''}, {ad.get('addressNumber') if ad.get('addressNumber') is not None else ''} - {ad.get('neighborhood') or ''}".strip(),
                       cep_cidade=f"{cep[:5]}-{cep[5:]} - {ad.get('cityName') or ''} - {ad.get('stateAbbreviation') or ''}")
    except Exception:  # noqa: BLE001
        pass
    _ENDERECOS[cod] = out
    return out


@fin_bp.route('/previa-boleto-santander')
@login_required
def previa_boleto_santander():
    """PREVIA do boleto no layout do boleto Santander que o TOTVS emite (Recibo do Pagador + Ficha de compensacao), com o VENCIMENTO sugerido pela regra.
    SIMULACAO: nao e boleto valido (sem nosso numero/codigo de barras) e nada e gravado no TOTVS. ?doc=69030&parcela=1"""
    _autorizar()
    from html import escape as h
    from app.totvs import financeiro as F
    doc, parcela = (request.args.get('doc') or '').strip(), (request.args.get('parcela') or '').strip()
    t = next((x for x in F.ler().get('receber', []) if str(x.get('doc')) == doc and str(x.get('parcela')) == parcela), None)
    if not t:
        return Response('<meta charset="utf-8"><p style="font:16px Arial;padding:30px">Título não encontrado na base lida do TOTVS.</p>', status=404, mimetype='text/html')
    dd = lambda d: d.strftime('%d/%m/%Y')
    brl = lambda v: f"{v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    venc = date.fromisoformat(t['venc'])
    rg = F.regra_do_cliente(t.get('nome'))
    novo = F.proximo_valido(rg, venc) if rg else venc
    valor = t['saldo'] or t['valor']
    fator = 1614 + (novo - date(2026, 11, 5)).days                         # fator de vencimento (calibrado por um boleto Santander real: 05/11/2026 = 1614)
    centavos = f"{int(round(valor * 100)):010d}"
    linha = f"03399.05440 6840X.XXXXXX XXXXX.XXXXXX X {fator}{centavos}"
    pag = _endereco_cliente(t.get('cod'), t.get('cnpj'))
    cnpj = pag['cnpj'] or ''
    cnpj_fmt = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}" if len(cnpj) == 14 else cnpj
    juros = round(valor * 0.002, 2)
    ndoc = f"{int(doc):06d}/{int(parcela):03d}" if doc.isdigit() and parcela.isdigit() else f"{doc}/{parcela}"
    emis = dd(date.fromisoformat(t['emissao'])) if t.get('emissao') else ''
    hoje = dd(date.today())

    def cel(span, rotulo, valor='', cinza=False, forte=False, dir_=False):
        cls = 'c' + (' g' if cinza else '')
        v = f'<span class="{"vf" if forte else "vv"}{" d" if dir_ else ""}">{valor}</span>' if valor != '' else ''
        return f'<div class="{cls}" style="grid-column:span {span}"><small>{rotulo}</small>{v}</div>'

    def benef():
        return f'<div class="bf"><span><b>{h(BENEF["nome"])}</b></span><span>{h(BENEF["cnpj"])}</span></div><div class="bf"><span>{h(BENEF["rua"])}</span><span>{h(BENEF["cep"])}</span></div>'

    def bloco(titulo, ficha):
        linhas = []
        if ficha:
            linhas += [cel(8, 'Local de Pagamento', 'PAGAR PREFERENCIALMENTE NO GRUPO SANTANDER BANESPA - GC', forte=True), cel(2, 'Vencimento', dd(novo), True, True, True)]
            linhas += [f'<div class="c" style="grid-column:span 8"><small>Beneficiário</small>{benef()}</div>', cel(2, 'Agência / Código do Beneficiário', h(BENEF['agencia']), dir_=True)]
            linhas += [cel(2, 'Data do Documento', emis, True, True), cel(2, 'N° do Documento', ndoc, True, True), cel(1, 'Espécie documento', 'DM'), cel(1, 'Aceite', 'N'), cel(2, 'Data do Processamento', hoje, True, True), cel(2, 'Nosso - Número', 'gerado pelo TOTVS', dir_=True)]
            linhas += [cel(2, 'Uso do Banco'), cel(1, 'Carteira', h(BENEF['carteira'])), cel(1, 'Espécie Moeda', 'R$'), cel(2, 'Quantidade moeda'), cel(2, 'Valor'), cel(2, '(=) Valor do Documento', brl(valor), True, True, True)]
        else:
            linhas += [f'<div class="c" style="grid-column:span 8"><small>Beneficiário</small>{benef()}</div>', cel(2, 'Vencimento', dd(novo), True, True, True)]
            linhas += [cel(1, 'Carteira', h(BENEF['carteira'])), cel(1, 'Espécie Moeda', 'R$'), cel(3, 'Nosso - Número', 'gerado pelo TOTVS', dir_=True), cel(2, 'Quantidade moeda'), cel(1, 'Valor'), cel(2, 'Agência / Cód. Benef.', h(BENEF['agencia']), dir_=True)]
            linhas += [cel(2, 'Data do Documento', emis, True, True), cel(2, 'N° do Documento', ndoc, True, True), cel(1, 'Espécie Documento', 'DM'), cel(1, 'Aceite', 'N'), cel(2, 'Data do Processamento', hoje, True, True), cel(2, 'Valor do Documento', brl(valor), True, True, True)]
        linhas += [cel(2, '(-) Desconto / Abatimento'), cel(2, '(-) Outras Deduções / Abatimentos'), cel(2, '(+) Juros / Multa / Mora'), cel(2, '(+) Outros Acréscimos'), cel(2, '(=) Valor cobrado', '', True)]
        info = ''
        if ficha:
            nf = f'Nota Fiscal: {int(doc):06d} .' if doc.isdigit() else ''
            info = f'<div class="inf"><small>Informações de responsabilidade do beneficiário:</small>- APÓS {dd(venc)} COBRAR JUROS DE R${brl(juros)}, POR DIA DE ATRASO.<br><br>{nf}</div>'
        return f"""<div class="bl"><div class="tt">{titulo}</div>
<div class="cab"><span class="lg">&#9670; Santander</span><span class="bc">033 - 7</span><span class="ld">{linha}</span></div>
<div class="gr">{''.join(linhas)}</div>{info}<div class="pg"><div><small>Pagador:</small>{h(pag['nome'] or t.get('nome') or '')}<br><span class="sub">{h(pag['end'])}</span><br><span class="sub">{h(pag['cep_cidade'])}</span></div><div class="cn"><small>CNPJ/CPF:</small>{h(cnpj_fmt)}</div></div>
<div class="fin"><small>Beneficiário final:</small></div>{'<div class="barra">código de barras gerado pelo TOTVS na emissão do boleto</div>' if ficha else ''}</div>"""

    corpo = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Prévia do boleto {h(doc)}/{h(parcela)}</title><style>
body{{margin:0;background:#e5e7eb;font:12px Arial,Helvetica,sans-serif;color:#111}}.fl{{max-width:820px;margin:12px auto;background:#fff;padding:14px 22px 22px;position:relative;overflow:hidden}}
.marca{{background:#dc2626;color:#fff;text-align:center;font-weight:900;padding:8px;border-radius:6px;margin-bottom:12px;animation:pi 1.2s infinite}}@keyframes pi{{50%{{opacity:.55}}}}
.agua{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none;font-size:92px;font-weight:900;color:rgba(220,38,38,.13);transform:rotate(-28deg);white-space:nowrap}}
.bl{{margin-bottom:26px;position:relative}}.tt{{text-align:right;font-weight:700;border-top:1px solid #000;padding:3px 0}}.cab{{display:flex;align-items:center;border-bottom:2px solid #000;padding:4px 0;gap:14px}}.lg{{color:#ec0000;font-weight:900;font-size:17px;min-width:128px}}.bc{{font-size:18px;font-weight:700;border-left:2px solid #000;border-right:2px solid #000;padding:0 12px}}.ld{{font-size:15px;font-weight:700;letter-spacing:.3px}}
.gr{{display:grid;grid-template-columns:repeat(10,1fr);border-left:1px solid #000}}.bf{{display:flex;justify-content:space-between;gap:10px;font-size:12.5px}}.vv{{display:block;font-size:12px}}.vf{{display:block;font-weight:700;font-size:12.5px}}.d{{text-align:right}}.c{{border-right:1px solid #000;border-bottom:1px solid #000;padding:1px 4px 3px;min-height:26px;grid-column:span 1;position:relative}}.c.w2{{grid-column:span 2}}.c.w3{{grid-column:span 3}}.c.g{{background:#d9d9d9}}
.c small,.pg small,.inf small,.fin small{{display:block;font-size:8.5px;color:#444}}.dir{{position:absolute;right:6px;top:11px}}.sub{{color:#222}}.venc{{display:block;text-align:right;font-size:15px}}.c b{{display:block;text-align:right}}
.inf{{border:1px solid #000;border-top:0;padding:4px 6px;min-height:70px}}.pg{{display:flex;justify-content:space-between;padding:6px 4px;border-bottom:1px solid #000}}.cn{{min-width:150px}}.fin{{padding:4px;border-bottom:1px solid #000}}
.barra{{margin-top:10px;height:52px;border:1px dashed #dc2626;color:#b91c1c;display:flex;align-items:center;justify-content:center;font-weight:700;background:repeating-linear-gradient(90deg,#fff 0 3px,#f3f4f6 3px 6px)}}
.bt{{text-align:center;margin:10px}}.bt button{{padding:8px 16px;border:0;border-radius:8px;background:#0a4aa8;color:#fff;font-weight:800;cursor:pointer}}@media print{{.bt{{display:none}}body{{background:#fff}}}}
</style></head><body><div class="bt"><button onclick="window.print()">Imprimir / salvar em PDF</button></div><div class="fl"><div class="agua">PRÉVIA · NÃO PAGAR</div>
<div class="marca">PRÉ-VISUALIZAÇÃO · NÃO É BOLETO VÁLIDO · NADA FOI GRAVADO NO TOTVS · vencimento sugerido pela regra{(' (' + h(rg['nome']) + ')') if rg else ''}: {dd(venc)} → {dd(novo)}</div>
{bloco('Recibo do Pagador', False)}{bloco('Ficha de compensação', True)}</div></body></html>"""
    return Response(corpo, mimetype='text/html')


@fin_bp.route('/diagnostico-titulo')
@login_required
def diagnostico_titulo():
    """So administrador: ?doc=68830 mostra o titulo como o Gestao guardou (emissao/vencimento) e TODOS os campos de data que o TOTVS devolve para ele, para comparar com a tela FCRFP098."""
    if not current_user.is_admin:
        abort(403)
    from datetime import timedelta
    from app.totvs import api, financeiro
    doc = (request.args.get('doc') or '').strip()
    guardados = [t for t in financeiro.ler().get('receber', []) if str(t.get('doc')) == doc]
    out = dict(doc=doc, guardado_no_gestao=[dict(parcela=t['parcela'], cliente=t['nome'], emissao=t['emissao'], vencimento=t['venc'], valor=t['valor'], tipo_doc=t.get('tipo_doc')) for t in guardados])
    try:
        cli = api.TotvsModaClient()
        cli.autenticar()
        vencs = sorted({t['venc'] for t in guardados if t.get('venc')})
        bruto = []
        if vencs:
            ini, fim = vencs[0], vencs[-1]
            hoje = date.today()
            chg = dict(startDate=f'{hoje - timedelta(days=30)}T00:00:00', endDate=f'{hoje}T23:59:59', inCheck=False)
            itens = financeiro._paginar(cli, financeiro.RECEBER_URL, dict(filter=dict(branchCodeList=[financeiro.EMPRESA], change=chg, startExpiredDate=f'{ini}T00:00:00', endExpiredDate=f'{fim}T23:59:59')), tam=100)
            bruto = [x for x in itens if str(x.get('receivableCode')) == doc]
        out['totvs_campos_de_data'] = [{k: v for k, v in x.items() if not isinstance(v, (list, dict)) and ('date' in k.lower() or k in ('installmentCode', 'receivableCode', 'customerCode', 'documentType'))} for x in bruto[:12]]
    except Exception as e:  # noqa: BLE001
        out['erro_totvs'] = str(e)[:300]
    return jsonify(out)


@fin_bp.route('/diagnostico')
@login_required
def diagnostico():
    if not current_user.is_admin:
        abort(403)
    from app.totvs import financeiro
    d = financeiro.ler()
    return jsonify(url_receber=d.get('url_R'), url_pagar=d.get('url_P'), amostra_receber=d.get('amostra_R'), amostra_pagar=d.get('amostra_P'), n_receber=len(d.get('receber', [])), n_pagar=len(d.get('pagar', [])), nomes_erro=d.get('nomes_erro'), nomes_resolvidos=len(d.get('nomes') or {}),
                   status_vistos=sorted({t['status'] for t in d.get('receber', [])})[:30], estado=financeiro.ESTADO)


@fin_bp.route('/exportar')
@login_required
def exportar():
    _autorizar()
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from app.totvs import financeiro
    p = financeiro.painel()
    tipo = request.args.get('tipo', 'receber')
    mes = request.args.get('mes', '')                      # AAAA-MM; vazio = tudo
    dia = lambda v: date.fromisoformat(v).strftime('%d/%m/%Y') if v else ''
    if tipo == 'prorrogacao':
        nome, cab = 'Prorrogação de vencimento', ['Cliente', 'Regra de pagamento', 'Fatura / Duplicata', 'Parcela', 'Dt. emissão', 'Vencimento atual', 'Novo vencimento', 'Dias a prorrogar', 'Valor (R$)', 'Já vencido?', 'Banco', 'Nosso número']
        linhas = [[x['cliente'], x['regra'], x['doc'], x['parcela'], dia(x.get('emissao')), dia(x['venc']), dia(x['novo']), x['dias'], x['valor'], 'Sim' if x['vencido'] else 'Não', x['banco'], x['nosso']] for x in sorted(p['prorrogacao'], key=lambda x: (str(x['cliente']).lower(), x['venc'], str(x['doc']), str(x['parcela']))) if not mes or x['venc'][:7] == mes]
    elif tipo == 'abertos':
        nome, cab = 'Boletos não gerados', ['Cliente', 'Regra de pagamento', 'Fatura / Duplicata', 'Parcela', 'Dt. emissão', 'Vencimento', 'Situação do vencimento', 'Novo vencimento', 'Valor em aberto (R$)', 'Já vencido?', 'Banco', 'Nosso número']
        linhas = [[x['cliente'], x['regra'], x['doc'], x['parcela'], dia(x.get('emissao')), dia(x['venc']), 'Boleto não gerado · cai em dia que não paga (prorrogar)' if x['novo'] else 'Boleto não gerado', dia(x['novo']), x['valor'], 'Sim' if x['vencido'] else 'Não', x['banco'], x['nosso']] for x in p['abertos_regra'] if not mes or x['venc'][:7] == mes]
    elif tipo == 'alteracoes':
        nome, cab = 'Alterações nos títulos', ['Quando', 'Tipo', 'Cliente', 'Fatura / Duplicata', 'Parcela', 'Dt. emissão', 'Vencimento', 'De', 'Para (conforme regra)', 'Valor (R$)']
        linhas = [[x['em'].replace('T', ' '), x['tipo'], x['nome'], x['doc'], x['parcela'], dia(x.get('emissao')), dia(x.get('venc')), x['de'], (dia(x['para_regra']) if x.get('para_regra') and x['tipo'] not in ('Baixado / pago', 'Saldo alterado') else x['para']), x['valor']] for x in sorted(p['alteracoes'], key=lambda x: (str(x['nome']).lower(), x['em'])) if not mes or x['em'][:7] == mes]
    elif tipo == 'exportacao':
        nome, cab = 'Exportação', ['Cliente', 'Fatura / Duplicata', 'Parcela', 'Emissão', 'Vencimento', 'Situação', 'Valor original', 'Valor pago', 'Saldo em aberto', 'Pago em']
        lin = []
        for x in p['exportacao'] + p['exp_pagos']:
            if mes and (x['venc'] or '')[:7] != mes:
                continue
            sit = 'Pago' if x.get('pago_titulo') else (f"Vencido há {x['atraso']} dias" if x['vencido'] else 'A vencer')
            lin.append([x['nome'], x['doc'], x['parcela'], dia(x['emissao']), dia(x['venc']), sit, x['valor'], x['pago'] or (x['valor'] if x.get('pago_titulo') else 0), 0 if x.get('pago_titulo') else x['saldo'], dia(x['pag_em'])])
        linhas = lin
    else:
        lista = {'receber': p['vencidos_receber'], 'pagar': p['vencidos_pagar'], 'exportacao': p['exportacao']}.get(tipo, p['vencidos_receber'])
        if mes and tipo == 'exportacao':
            lista = [x for x in lista if (x['venc'] or '')[:7] == mes]
        nome = {'receber': 'Vencidos a receber', 'pagar': 'Vencidos a pagar', 'exportacao': 'A receber exportação'}.get(tipo, 'Vencidos')
        cab = ['Cliente / Fornecedor', 'Fatura / Duplicata', 'Parcela', 'Emissão', 'Vencimento', 'Dias de atraso', 'Valor original', 'Saldo em aberto', 'Moeda', 'Banco', 'Situação']
        linhas = [[x['nome'], x['doc'], x['parcela'], dia(x['emissao']), dia(x['venc']), x['atraso'], x['valor'], x['saldo'], x['moeda'], x['banco'], x['status']] for x in lista]
    wb = Workbook()
    ws = wb.active
    ws.title = nome[:31]
    ws.append(cab)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='0F2A4A')
    for l in linhas:
        ws.append(l)
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(50, max(12, max(len(str(c.value or '')) for c in col) + 2))
    ws.freeze_panes = 'A2'
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f'financeiro_{tipo}_{date.today():%Y-%m-%d}.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
