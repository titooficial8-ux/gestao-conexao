import { useEffect, useMemo, useRef, useState } from 'react';
import { Search, Loader2, Package, Ruler, User, AlertTriangle, Info, Scale } from 'lucide-react';
import { Input, Dialog, DialogContent, DialogHeader, DialogTitle } from '../../components/ui.jsx';

const ROOT = document.getElementById('historico-artigo-root');
const URLS = {
  search: ROOT?.dataset.searchUrl,
  article: ROOT?.dataset.articleUrl,
  machines: ROOT?.dataset.machinesUrl,
  machineData: ROOT?.dataset.machineDataUrl,
  local30: ROOT?.dataset.local30Url,
};

/* Tokens de cor travados (mesmo padrão da aba original do PCP Hub) */
const C = {
  emerald: '#064e3b',
  emerald2: '#0d7a5f',
  gold: '#c9a84c',
  cream: '#f5f0e0',
  red: '#b91c1c',
};

const normArt = (v) => String(v ?? '').trim().split(/\s+/)[0].split('-')[0].toUpperCase();
const fmtDate = (v) =>
  v ? new Date(v).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
const fmtNum = (v) => (Number(v) || 0).toLocaleString('pt-BR', { maximumFractionDigits: 0 });

const MISSING_STATUSES = ['PRODUZINDO', 'FINALIZADO', 'CANCELADO', 'PARADA CORRETIVA'];
const showMissing = (status) => {
  const s = String(status ?? '').toUpperCase();
  return MISSING_STATUSES.some((x) => s.includes(x));
};

const STATUS_ORDER = ['PRODUZINDO', 'EM SEQUENCIA', 'INTERROMPIDO', 'CANCELADO', 'PCP', 'FINALIZADO'];
const statusRank = (status) => {
  const s = String(status ?? '')
    .toUpperCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
  const i = STATUS_ORDER.findIndex((x) => s.includes(x));
  return i === -1 ? STATUS_ORDER.length : i;
};

const card = { borderColor: 'rgba(6,78,59,0.12)' };

function Local30Ticker() {
  const [rows, setRows] = useState([]);
  useEffect(() => {
    let alive = true;
    const load = () => fetch(URLS.local30).then((r) => r.json()).then((d) => alive && setRows(d || []));
    load();
    const t = setInterval(load, 60000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const items = rows.map((r) => {
    const art = (r.article || r.ds_produto || '').toString().trim();
    return `OP ${r.op ?? '—'}${art ? ` · ARTIGO ${art}` : ''} · Item ${r.nr_item ?? '—'} passou pela balança (LOCAL 30) — ${fmtNum(r.quantidade)} m · ${fmtNum(r.peso)} kg · ${fmtDate(r.captured_at)}`;
  });
  return (
    <div className="rounded-2xl border overflow-hidden" style={{ borderColor: 'rgba(201,168,76,0.35)', background: 'rgba(6,78,59,0.06)' }}>
      <div className="flex items-center gap-2 px-4 py-2">
        <Scale className="w-4 h-4 shrink-0" style={{ color: '#C9A84C' }} />
        <span className="text-[11px] font-bold uppercase tracking-wider shrink-0" style={{ color: '#064E3B' }}>Balança L30</span>
        <div className="relative flex-1 overflow-hidden">
          {items.length === 0 ? (
            <span className="text-xs" style={{ color: '#065F46' }}>Aguardando pesagens do Agente do LOCAL 30…</span>
          ) : (
            <div className="flex items-center gap-10 whitespace-nowrap w-max animate-ticker">
              {[...items, ...items].map((t, i) => (
                <span key={i} className="text-xs font-semibold" style={{ color: '#065F46' }}>{t}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function HistoricoArtigo() {
  const [term, setTerm] = useState('');
  const [options, setOptions] = useState([]);
  const [opOptions, setOpOptions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [opFilter, setOpFilter] = useState(null);
  const [rev30Op, setRev30Op] = useState(null);
  const [searching, setSearching] = useState(false);
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState([]);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [clientTerm, setClientTerm] = useState('');
  const [clientSel, setClientSel] = useState(null);
  const [clientOpen, setClientOpen] = useState(false);
  const [cycles, setCycles] = useState({});
  const [machines, setMachines] = useState([]);
  const [machineSel, setMachineSel] = useState(null);
  const [machineRows, setMachineRows] = useState([]);
  const [machineLoading, setMachineLoading] = useState(false);
  const [rev29, setRev29] = useState({});
  const seq = useRef(0);

  useEffect(() => {
    const id = 'fonts-sora-manrope';
    if (document.getElementById(id)) return;
    const link = document.createElement('link');
    link.id = id;
    link.rel = 'stylesheet';
    link.href = 'https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&family=Sora:wght@600;700&display=swap';
    document.head.appendChild(link);
  }, []);

  useEffect(() => {
    const t = term.trim();
    if (t.length < 2) {
      setOptions([]);
      setOpOptions([]);
      setSearching(false);
      return;
    }
    const my = ++seq.current;
    setSearching(true);
    const timer = setTimeout(async () => {
      const res = await fetch(`${URLS.search}?q=${encodeURIComponent(t)}`).then((r) => r.json());
      if (my !== seq.current) return;
      setOptions(res.articles || []);
      setOpOptions(res.ops || []);
      setSearching(false);
    }, 350);
    return () => clearTimeout(timer);
  }, [term]);

  const loadArticle = async (art, onlyOp) => {
    setSelected(art);
    setOpFilter(onlyOp ? String(onlyOp).trim() : null);
    setLoading(true);
    try {
      const data = await fetch(`${URLS.article}?art=${encodeURIComponent(art)}`).then((r) => r.json());
      const histA = data.historico || [];
      const activeA = data.ativo || [];
      const queueA = data.fila || [];
      const movs = data.movimentos || [];

      const perOp = new Map();
      for (const m of movs) {
        if (Number(String(m.local_code ?? '').replace(/\D/g, '')) !== 23) continue;
        if (normArt(m.cod_artigo) !== art) continue;
        const op = String(m.op ?? '').trim();
        if (!perOp.has(op)) perOp.set(op, { pieces: new Set(), nums: new Set(), meters: 0, last: 0 });
        const e = perOp.get(op);
        const item = String(m.nr_item ?? '').replace(/\D/g, '');
        if (item) {
          e.pieces.add(`${m.nr_lote}|${item}`);
          e.nums.add(Number(item));
        }
        e.meters += Number(m.qt_movimento) || 0;
        const ts = m.dt_real ? new Date(m.dt_real).getTime() : 0;
        if (ts > e.last) e.last = ts;
      }

      const gapCount = (nums) => {
        if (!nums || nums.size === 0) return 0;
        const arr = [...nums].filter((n) => Number.isFinite(n));
        if (arr.length === 0) return 0;
        const lo = Math.min(...arr);
        const hi = Math.max(...arr);
        let gaps = 0;
        for (let n = lo + 1; n < hi; n++) if (!nums.has(n)) gaps++;
        return gaps;
      };

      const out = [];
      const buildRow = (r, origin) => {
        const op = String(r.op ?? '').trim();
        const stat = perOp.get(op);
        const produced = stat?.pieces.size ?? 0;
        const total = Number(r.total_pcs) || 0;
        const q = queueA.find((x) => String(x.op ?? '').trim() === op && (!x.moved_to_machine || String(x.moved_to_machine) === String(r.machine)))
          ?? queueA.find((x) => String(x.op ?? '').trim() === op);
        return {
          id: `${origin}-${r.id}`,
          op: op || '—',
          machine: String(r.machine ?? '—'),
          article: r.artigo ?? art,
          client: r.cliente ?? null,
          status: r.status ?? '',
          totalPcs: total,
          producedPcs: produced,
          missingPcs: gapCount(stat?.nums),
          requestedMeters: Number(r.requested_quantity) || 0,
          realMeters: stat?.meters ?? 0,
          isAvulsa: Boolean(r.is_avulsa ?? q?.is_avulsa ?? false),
          qtAvulsa: Number(r.qt_avulsa) || 0,
          programmedBy: null,
          programmedAt: r.created_at ?? null,
          preProgrammedBy: null,
          preProgrammedAt: q?.created_at ?? null,
          movedBy: null,
          finishedAt: origin === 'HISTÓRICO' ? r.changed_at ?? null : null,
          origin,
        };
      };

      for (const r of activeA) out.push(buildRow(r, 'ATIVO'));
      for (const r of histA) out.push(buildRow(r, 'HISTÓRICO'));

      out.sort((a, b) => {
        const ra = statusRank(a.status);
        const rb = statusRank(b.status);
        if (ra !== rb) return ra - rb;
        const ta = new Date(a.finishedAt ?? a.programmedAt ?? 0).getTime();
        const tb = new Date(b.finishedAt ?? b.programmedAt ?? 0).getTime();
        return tb - ta;
      });
      setRows(out);

      const dmap = {};
      for (const d of data.daily_cycles || []) {
        const k = `${parseInt(String(d.machine), 10)}|${String(d.op ?? '').trim()}`;
        (dmap[k] ??= []).push({ cycle_date: String(d.cycle_date), pieces: Number(d.pieces) || 0, meters: Number(d.meters) || 0 });
      }
      setCycles(dmap);
      setRev29({});
    } finally {
      setLoading(false);
    }
  };

  const clientOptions = useMemo(() => {
    const set = new Set();
    for (const r of rows) if (r.client) set.add(String(r.client).trim());
    const t = clientTerm.trim().toUpperCase();
    return [...set].filter((c) => !t || c.toUpperCase().includes(t)).sort((a, b) => a.localeCompare(b)).slice(0, 50);
  }, [rows, clientTerm]);

  const filteredRows = useMemo(() => {
    const from = dateFrom ? new Date(`${dateFrom}T00:00:00`).getTime() : null;
    const to = dateTo ? new Date(`${dateTo}T23:59:59`).getTime() : null;
    const cli = (clientSel ?? clientTerm).trim().toUpperCase();
    return rows.filter((r) => {
      if (opFilter && String(r.op ?? '').trim() !== opFilter) return false;
      const ref = new Date(r.finishedAt ?? r.programmedAt ?? 0).getTime();
      if (from && (!ref || ref < from)) return false;
      if (to && (!ref || ref > to)) return false;
      if (cli && !String(r.client ?? '').toUpperCase().includes(cli)) return false;
      return true;
    });
  }, [rows, dateFrom, dateTo, clientSel, clientTerm, opFilter]);

  const totals = useMemo(() => filteredRows.reduce((acc, r) => ({
    ops: acc.ops + 1,
    produced: acc.produced + r.producedPcs,
    missing: acc.missing + (showMissing(r.status) ? r.missingPcs : 0),
    meters: acc.meters + r.realMeters,
  }), { ops: 0, produced: 0, missing: 0, meters: 0 }), [filteredRows]);

  useEffect(() => {
    fetch(URLS.machines).then((r) => r.json()).then((d) => setMachines(d || []));
  }, []);

  const loadMachine = async (m) => {
    setMachineSel(m);
    setMachineLoading(true);
    try {
      const list = await fetch(`${URLS.machineData}?machine=${encodeURIComponent(m)}`).then((r) => r.json());
      setMachineRows(list || []);
    } finally {
      setMachineLoading(false);
    }
  };

  const machineFiltered = useMemo(() => machineRows.filter((r) => {
    if (dateFrom && r.cycle_date < dateFrom) return false;
    if (dateTo && r.cycle_date > dateTo) return false;
    return true;
  }), [machineRows, dateFrom, dateTo]);

  return (
    <div className="min-h-screen w-full p-4 md:p-8 lg:p-10" style={{ background: C.cream, color: C.emerald, fontFamily: 'Manrope, system-ui, sans-serif' }}>
      <div className="max-w-[1400px] mx-auto space-y-8">
        <header className="rounded-3xl border overflow-hidden shadow-sm" style={{ borderColor: 'rgba(6,78,59,0.15)', background: `linear-gradient(135deg, ${C.emerald} 0%, ${C.emerald2} 100%)` }}>
          <div className="p-6 md:p-8 flex flex-col lg:flex-row lg:items-center justify-between gap-6">
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 md:w-16 md:h-16 rounded-2xl flex items-center justify-center shrink-0" style={{ background: 'rgba(245,240,224,0.12)', border: '1px solid rgba(201,168,76,0.35)' }}>
                <span className="text-2xl">🧵</span>
              </div>
              <div>
                <p className="text-[10px] md:text-[11px] font-bold uppercase tracking-[0.35em]" style={{ color: C.gold }}>Controle de Ordem de Produção</p>
                <h1 className="text-3xl md:text-4xl font-bold tracking-tight mt-1" style={{ fontFamily: 'Sora, system-ui, sans-serif', color: C.cream }}>
                  Histórico <span style={{ color: C.gold, opacity: 0.7 }}>por Artigo</span>
                </h1>
                <p className="mt-2 text-xs font-semibold uppercase tracking-[0.25em]" style={{ color: 'rgba(245,240,224,0.65)' }}>Rastreabilidade de Produção · Ambiente Malharia</p>
              </div>
            </div>

            <div className="w-full lg:w-96 space-y-2">
              <div className="relative flex items-center rounded-xl border px-3 h-12" style={{ background: 'rgba(245,240,224,0.97)', borderColor: 'rgba(201,168,76,0.5)' }}>
                <Search className="w-4 h-4 shrink-0" style={{ color: C.emerald2 }} />
                <Input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Buscar artigo ou OP..."
                  className="border-none shadow-none bg-transparent text-sm font-semibold" style={{ color: C.emerald, height: 'auto' }} />
                {searching && <Loader2 className="w-4 h-4 animate-spin" style={{ color: C.emerald2 }} />}
              </div>
              {selected && (
                <p className="text-[11px] font-semibold uppercase tracking-wider flex items-center gap-2" style={{ color: 'rgba(245,240,224,0.7)' }}>
                  Artigo selecionado: <span style={{ color: C.gold }}>{selected}</span>
                  {opFilter && (
                    <>
                      <span style={{ color: C.gold }}>· OP {opFilter}</span>
                      <button onClick={() => setOpFilter(null)} className="underline" style={{ color: 'rgba(245,240,224,0.85)' }}>ver todas</button>
                    </>
                  )}
                </p>
              )}
            </div>
          </div>
        </header>

        {term.trim().length >= 2 && (
          <div className="rounded-2xl border p-4 bg-white shadow-sm" style={card}>
            <p className="text-[10px] font-bold uppercase tracking-wider mb-2" style={{ color: C.emerald2 }}>Resultados da busca</p>
            {searching ? (
              <p className="text-xs flex items-center gap-2" style={{ color: C.emerald2 }}><Loader2 className="w-3 h-3 animate-spin" /> Procurando no banco de dados...</p>
            ) : options.length === 0 && opOptions.length === 0 ? (
              <p className="text-xs" style={{ color: C.emerald2 }}>Nenhum artigo ou OP encontrado.</p>
            ) : (
              <div className="space-y-3">
                {options.length > 0 && (
                  <div>
                    <p className="text-[9px] font-bold uppercase tracking-wider mb-1.5" style={{ color: C.emerald2, opacity: 0.7 }}>Artigos</p>
                    <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">
                      {options.map((o) => {
                        const active = selected === o.key;
                        return (
                          <button key={o.key} onClick={() => loadArticle(o.key)} className="text-xs font-bold px-3 py-1.5 rounded-lg border transition-colors"
                            style={active ? { background: C.emerald, color: C.cream, borderColor: C.emerald } : { background: 'transparent', color: C.emerald, borderColor: 'rgba(6,78,59,0.15)' }}>
                            {o.key}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                {opOptions.length > 0 && (
                  <div>
                    <p className="text-[9px] font-bold uppercase tracking-wider mb-1.5" style={{ color: C.emerald2, opacity: 0.7 }}>OPs</p>
                    <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">
                      {opOptions.map((o) => (
                        <button key={o.op} onClick={() => loadArticle(o.article, o.op)} className="text-xs font-bold px-3 py-1.5 rounded-lg border transition-colors"
                          style={opFilter === o.op ? { background: '#8a6d19', color: C.cream, borderColor: '#8a6d19' } : { background: 'rgba(201,168,76,0.12)', color: '#8a6d19', borderColor: 'rgba(201,168,76,0.4)' }}>
                          OP {o.op} · {o.article}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {loading && (
          <div className="flex items-center gap-2 text-sm" style={{ color: C.emerald2 }}><Loader2 className="w-4 h-4 animate-spin" /> Carregando histórico...</div>
        )}

        <div className="rounded-2xl border bg-white shadow-sm p-4 flex flex-col md:flex-row md:items-end gap-4" style={card}>
          <div className="flex gap-3">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-wider mb-1" style={{ color: C.emerald2 }}>Data inicial</p>
              <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="h-10 text-sm w-40 font-semibold border-2" style={{ borderColor: C.emerald2, backgroundColor: C.emerald2, color: '#ffffff' }} />
            </div>
            <div>
              <p className="text-[10px] font-bold uppercase tracking-wider mb-1" style={{ color: C.emerald2 }}>Data final</p>
              <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="h-10 text-sm w-40 font-semibold border-2" style={{ borderColor: C.emerald2, backgroundColor: C.emerald2, color: '#ffffff' }} />
            </div>
          </div>

          <div className="relative flex-1 min-w-[220px]">
            <p className="text-[10px] font-bold uppercase tracking-wider mb-1" style={{ color: C.emerald2 }}>Cliente</p>
            <div className="flex items-center rounded-md border px-3 h-10" style={{ borderColor: 'rgba(6,78,59,0.2)' }}>
              <Input value={clientSel ?? clientTerm} onChange={(e) => { setClientSel(null); setClientTerm(e.target.value); setClientOpen(true); }}
                onFocus={() => setClientOpen(true)} placeholder="Pesquisar cliente..." className="border-none shadow-none bg-transparent text-sm" style={{ color: C.emerald, height: 'auto' }} />
            </div>
            {clientOpen && clientOptions.length > 0 && (
              <div className="absolute z-20 mt-1 w-full max-h-52 overflow-y-auto rounded-md border bg-white shadow-lg" style={{ borderColor: 'rgba(6,78,59,0.2)' }}>
                {clientOptions.map((c) => (
                  <button key={c} onClick={() => { setClientSel(c); setClientTerm(c); setClientOpen(false); }} className="w-full text-left text-xs px-3 py-2 hover:bg-black/5" style={{ color: C.emerald }}>
                    {c}
                  </button>
                ))}
              </div>
            )}
          </div>

          <button onClick={() => { setDateFrom(''); setDateTo(''); setClientTerm(''); setClientSel(null); setClientOpen(false); }}
            className="h-10 px-4 rounded-md text-xs font-bold uppercase tracking-wider border" style={{ color: C.emerald, borderColor: 'rgba(6,78,59,0.2)' }}>
            Limpar filtros
          </button>
        </div>

        <Local30Ticker />

        <div className="rounded-2xl border bg-white shadow-sm p-5 space-y-4" style={card}>
          <div className="flex items-center gap-2">
            <Package className="w-4 h-4" style={{ color: C.emerald2 }} />
            <p className="text-[11px] font-bold uppercase tracking-wider" style={{ color: C.emerald2 }}>Arquivo diário por máquina (ciclo 06h → 06h)</p>
          </div>

          <div className="flex flex-wrap gap-1.5 max-h-32 overflow-y-auto">
            {machines.length === 0 && <p className="text-xs" style={{ color: C.emerald2 }}>Nenhum histórico diário arquivado ainda.</p>}
            {machines.map((m) => {
              const active = machineSel === m;
              return (
                <button key={m} onClick={() => (active ? (setMachineSel(null), setMachineRows([])) : loadMachine(m))}
                  className="text-xs font-bold px-3 py-1.5 rounded-lg border transition-colors"
                  style={active ? { background: C.emerald, color: C.cream, borderColor: C.emerald } : { background: 'transparent', color: C.emerald, borderColor: 'rgba(6,78,59,0.15)' }}>
                  MÁQ. {m}
                </button>
              );
            })}
          </div>

          {machineLoading && <p className="text-xs flex items-center gap-2" style={{ color: C.emerald2 }}><Loader2 className="w-3 h-3 animate-spin" /> Carregando arquivo da máquina...</p>}

          {!machineLoading && machineSel && (
            machineFiltered.length === 0 ? (
              <p className="text-xs" style={{ color: C.emerald2 }}>Nenhum registro para esta máquina no período.</p>
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                  {[
                    { label: 'Dias registrados', value: fmtNum(new Set(machineFiltered.map((r) => r.cycle_date)).size), color: C.emerald },
                    { label: 'Peças no período', value: fmtNum(machineFiltered.reduce((s, r) => s + r.pieces, 0)), color: C.emerald2 },
                    { label: 'Metros no período', value: `${fmtNum(machineFiltered.reduce((s, r) => s + r.meters, 0))} m`, color: C.gold },
                  ].map((k) => (
                    <div key={k.label} className="rounded-xl p-3 border" style={{ ...card, borderLeft: `4px solid ${k.color}` }}>
                      <p className="text-[10px] font-bold uppercase tracking-wider" style={{ color: C.emerald2 }}>{k.label}</p>
                      <p className="text-xl font-bold tabular-nums" style={{ fontFamily: 'Sora', color: k.color }}>{k.value}</p>
                    </div>
                  ))}
                </div>

                <div className="overflow-x-auto rounded-xl border" style={card}>
                  <table className="w-full text-xs">
                    <thead>
                      <tr style={{ background: 'rgba(6,78,59,0.06)' }}>
                        {['Data', 'Artigo', 'OP', 'Peças', 'Metros'].map((h) => (
                          <th key={h} className="text-left font-bold uppercase text-[10px] px-3 py-2" style={{ color: C.emerald2 }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {machineFiltered.map((r, i) => (
                        <tr key={`${r.cycle_date}-${r.op}-${r.article}-${i}`} className="border-t" style={{ borderColor: 'rgba(6,78,59,0.08)' }}>
                          <td className="px-3 py-2 font-bold" style={{ color: C.emerald }}>{r.cycle_date.split('-').reverse().join('/')}</td>
                          <td className="px-3 py-2" style={{ color: C.emerald }}>{r.article}</td>
                          <td className="px-3 py-2" style={{ color: C.emerald2 }}>{r.op || '—'}</td>
                          <td className="px-3 py-2 font-bold tabular-nums" style={{ color: C.emerald2 }}>{fmtNum(r.pieces)}</td>
                          <td className="px-3 py-2 tabular-nums" style={{ color: C.gold }}>{fmtNum(r.meters)} m</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          )}
        </div>

        {!loading && selected && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                { label: 'OPs registradas', value: String(totals.ops), color: C.emerald },
                { label: 'Peças pesadas (L23)', value: fmtNum(totals.produced), color: C.emerald2 },
                { label: 'Peças não pesadas', value: fmtNum(totals.missing), color: C.red },
                { label: 'Metragem real', value: `${fmtNum(totals.meters)} m`, color: C.gold },
              ].map((k) => (
                <div key={k.label} className="rounded-2xl p-5 border bg-white shadow-sm relative overflow-hidden" style={{ ...card, borderLeft: `4px solid ${k.color}` }}>
                  <p className="text-[10px] font-bold uppercase tracking-wider" style={{ color: C.emerald2 }}>{k.label}</p>
                  <p className="text-2xl md:text-3xl font-bold tabular-nums mt-1" style={{ fontFamily: 'Sora', color: k.color }}>{k.value}</p>
                </div>
              ))}
            </div>

            <div className="space-y-3">
              {filteredRows.length === 0 && (
                <div className="rounded-2xl p-6 text-sm border bg-white" style={card}>Nenhum registro para este artigo com os filtros aplicados.</div>
              )}
              {filteredRows.map((r) => (
                <div key={r.id} className="rounded-2xl p-5 border bg-white shadow-sm space-y-3" style={card}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded-md" style={r.origin === 'ATIVO' ? { background: C.emerald, color: C.cream } : { background: 'rgba(6,78,59,0.08)', color: C.emerald }}>{r.origin}</span>
                    <span className="text-sm font-bold" style={{ fontFamily: 'Sora', color: C.emerald }}>MÁQ. {r.machine}</span>
                    <span className="text-sm" style={{ color: C.emerald2 }}>OP {r.op}</span>
                    <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded-md border" style={{ borderColor: 'rgba(6,78,59,0.2)', color: C.emerald2 }}>{r.status || '—'}</span>
                    {r.isAvulsa && <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded-md" style={{ background: 'rgba(201,168,76,0.15)', color: '#8a6d19' }}>AVULSA</span>}
                    <span className="text-xs ml-auto font-semibold" style={{ color: C.emerald2 }}>{r.client ?? '—'}</span>
                  </div>

                  <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
                    <div>
                      <p className="flex items-center gap-1 font-bold uppercase text-[10px]" style={{ color: C.emerald2 }}><Package className="w-3 h-3" /> TOTAL PÇS</p>
                      <p className="font-bold tabular-nums" style={{ color: C.emerald }}>{fmtNum(r.totalPcs)}</p>
                    </div>
                    <div>
                      <p className="font-bold uppercase text-[10px]" style={{ color: C.emerald2 }}>Peças pesadas (L23)</p>
                      <p className="font-bold tabular-nums" style={{ color: C.emerald2 }}>{fmtNum(r.producedPcs)}</p>
                    </div>
                    {showMissing(r.status) && (
                      <div>
                        <p className="flex items-center gap-1 font-bold uppercase text-[10px]" style={{ color: C.emerald2 }}><AlertTriangle className="w-3 h-3" /> Faltou pesar</p>
                        <p className="font-bold tabular-nums" style={{ color: r.missingPcs > 0 ? C.red : C.emerald2 }}>{fmtNum(r.missingPcs)}</p>
                      </div>
                    )}
                    {String(r.client ?? '').toUpperCase().includes('ORTOBOM') && (
                      <div>
                        <p className="flex items-center gap-1 font-bold uppercase text-[10px]" style={{ color: C.emerald2 }}><Ruler className="w-3 h-3" /> QT.AVULSA</p>
                        <p className="font-bold tabular-nums" style={{ color: C.gold }}>{fmtNum(r.qtAvulsa)}</p>
                      </div>
                    )}
                    <div>
                      <p className="flex items-center gap-1 font-bold uppercase text-[10px]" style={{ color: C.emerald2 }}><Ruler className="w-3 h-3" /> Metragem solicitada</p>
                      <p className="font-bold tabular-nums" style={{ color: C.emerald }}>{fmtNum(r.requestedMeters)} m</p>
                    </div>
                    <div>
                      <p className="font-bold uppercase text-[10px]" style={{ color: C.emerald2 }}>Metragem geral (malharia)</p>
                      <p className="font-bold tabular-nums" style={{ color: C.gold }}>{fmtNum(r.realMeters)} m</p>
                    </div>
                    {(() => {
                      const l30 = rev29[String(r.op ?? '').trim()] ?? [];
                      const mt30 = l30.reduce((s, x) => s + x.quantidade, 0);
                      return (
                        <div>
                          <p className="flex items-center gap-1 font-bold uppercase text-[10px]" style={{ color: C.emerald2 }}>
                            Metragem acabamento (L30)
                            <button type="button" onClick={() => setRev30Op(String(r.op ?? '').trim())} title="Ver pesagens do LOCAL 30">
                              <Info className="w-3.5 h-3.5" style={{ color: C.gold }} />
                            </button>
                          </p>
                          <p className="font-bold tabular-nums" style={{ color: C.gold }}>{fmtNum(mt30)} m</p>
                        </div>
                      );
                    })()}
                  </div>

                  {(() => {
                    const list = cycles[`${parseInt(String(r.machine), 10)}|${String(r.op ?? '').trim()}`] ?? [];
                    if (list.length === 0) return null;
                    const avg = Math.round(list.reduce((s, d) => s + d.pieces, 0) / list.length);
                    return (
                      <div className="border-t pt-3" style={{ borderColor: 'rgba(6,78,59,0.1)' }}>
                        <p className="flex items-center gap-1 font-bold uppercase text-[10px] mb-2" style={{ color: C.emerald2 }}>
                          <Package className="w-3 h-3" /> Peças por dia (ciclo 06h → 06h) · média {fmtNum(avg)} pçs/24h
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {list.map((d) => (
                            <div key={d.cycle_date} className="rounded-lg px-2.5 py-1.5 border text-[11px]" style={{ borderColor: 'rgba(6,78,59,0.15)', background: 'rgba(6,78,59,0.04)' }}>
                              <span className="font-bold" style={{ color: C.emerald }}>{d.cycle_date.split('-').reverse().slice(0, 2).join('/')}</span>
                              <span className="mx-1" style={{ color: 'rgba(6,78,59,0.3)' }}>|</span>
                              <span className="font-bold tabular-nums" style={{ color: d.pieces >= avg ? C.emerald2 : C.red }}>{fmtNum(d.pieces)} pçs</span>
                              <span className="ml-1 tabular-nums" style={{ color: C.gold }}>{fmtNum(d.meters)} m</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })()}

                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-[11px] border-t pt-3" style={{ borderColor: 'rgba(6,78,59,0.1)' }}>
                    <div>
                      <p className="flex items-center gap-1 font-bold uppercase text-[10px]" style={{ color: C.emerald2 }}><User className="w-3 h-3" /> Programou na máquina</p>
                      <p style={{ color: C.emerald }}>{r.programmedBy ?? '—'}</p>
                      <p style={{ color: C.emerald2 }}>{fmtDate(r.programmedAt)}</p>
                    </div>
                    <div>
                      <p className="flex items-center gap-1 font-bold uppercase text-[10px]" style={{ color: C.emerald2 }}><User className="w-3 h-3" /> Pré-programação (cadastrou)</p>
                      <p style={{ color: C.emerald }}>{r.preProgrammedBy ?? '—'}</p>
                      <p style={{ color: C.emerald2 }}>{r.preProgrammedAt ? fmtDate(r.preProgrammedAt) : '—'}</p>
                    </div>
                    <div>
                      <p className="flex items-center gap-1 font-bold uppercase text-[10px]" style={{ color: C.emerald2 }}><User className="w-3 h-3" /> Destinou para máquina</p>
                      <p style={{ color: C.emerald }}>{r.movedBy ?? '—'}</p>
                      <p style={{ color: C.emerald2 }}>{r.finishedAt ? `Finalizado: ${fmtDate(r.finishedAt)}` : ''}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <Dialog open={!!rev30Op} onOpenChange={(o) => !o && setRev30Op(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle style={{ color: C.emerald }}>Pesagens LOCAL 30 · OP {rev30Op} {selected ? `· Artigo ${selected}` : ''}</DialogTitle>
          </DialogHeader>
          <p className="text-sm" style={{ color: C.emerald2 }}>Nenhuma pesagem registrada no LOCAL 30 para esta OP.</p>
        </DialogContent>
      </Dialog>
    </div>
  );
}
