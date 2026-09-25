import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Gauge, Ruler, Package, Weight, Target, Wrench, TrendingUp, Clock,
  RefreshCw, Zap, Search, AlertTriangle, Trophy, CalendarRange,
} from 'lucide-react';
import {
  Card, Button, Input, Dialog, DialogContent, DialogHeader, DialogTitle,
  Popover, PopoverTrigger, PopoverContent, useToast,
} from '../../components/ui.jsx';
import {
  SHIFT_META, PECAS_POR_TURNO_POR_MAQ, TURNOS,
  CAP_METROS_INSTALADA_TURNO_FIXA, CAP_PECAS_INSTALADA_TURNO_FIXA,
  productionDateOf, getShift, normalizeMachine, nf, nf1, nfKg, pct, toneFor, TONE_COLOR, isSample,
} from './domain.js';

const ROOT = document.getElementById('controle-producao-root');
const URLS = {
  movimentos: ROOT?.dataset.movimentosUrl,
  settings: ROOT?.dataset.settingsUrl,
  reprovas: ROOT?.dataset.reprovasUrl,
};
const IS_ADMIN = ROOT?.dataset.isAdmin === '1';

const todayISO = (() => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
})();
const todayMonth = todayISO.slice(0, 7);

function EditableSetting({ label, value, unit, onSave, disabled }) {
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState(value);
  useEffect(() => { setVal(value); }, [value]);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger>
        <button
          type="button"
          onClick={() => !disabled && setOpen((o) => !o)}
          className="w-full text-left rounded-lg p-3"
          style={{ background: 'var(--gc-muted)', border: '1px solid var(--gc-border)', cursor: disabled ? 'default' : 'pointer' }}
        >
          <p className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: 'var(--gc-muted-foreground)' }}>{label} {!disabled && '✎'}</p>
          <p className="text-lg font-heading font-bold" style={{ color: 'var(--gc-foreground)' }}>{nf(value)} <span className="text-xs font-normal">{unit}</span></p>
        </button>
      </PopoverTrigger>
      {open && !disabled && (
        <PopoverContent className="w-56">
          <p className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--gc-muted-foreground)' }}>{label}</p>
          <Input type="number" value={val} onChange={(e) => setVal(e.target.value)} className="w-full mb-2" />
          <div className="flex gap-2">
            <Button size="sm" onClick={() => { onSave(Number(val)); setOpen(false); }}>Salvar</Button>
            <Button size="sm" variant="outline" onClick={() => setOpen(false)}>Cancelar</Button>
          </div>
        </PopoverContent>
      )}
    </Popover>
  );
}

function StatBlock({ icon: Icon, label, value, sub }) {
  return (
    <div>
      <p className="flex items-center gap-1 text-[10px] uppercase font-semibold" style={{ color: 'var(--gc-muted-foreground)' }}>
        <Icon className="w-3 h-3" /> {label}
      </p>
      <p className="text-2xl font-heading font-bold" style={{ color: 'var(--gc-foreground)' }}>{value}</p>
      {sub && <p className="text-[10px]" style={{ color: 'var(--gc-muted-foreground)' }}>{sub}</p>}
    </div>
  );
}

function HeaderKpi({ icon: Icon, label, value, sub, accent }) {
  return (
    <Card style={{ background: 'var(--gc-card)', border: '1px solid var(--gc-border)' }} className="p-4 relative overflow-hidden">
      <div className="flex items-center justify-between">
        <p className="text-[10px] uppercase font-semibold tracking-wider" style={{ color: 'var(--gc-muted-foreground)' }}>{label}</p>
        <Icon className="w-4 h-4" style={{ color: accent || 'var(--gc-primary)' }} />
      </div>
      <p className="text-2xl font-heading font-bold mt-1" style={{ color: accent || 'var(--gc-foreground)' }}>{value}</p>
      {sub && <p className="text-[10px] mt-0.5" style={{ color: 'var(--gc-muted-foreground)' }}>{sub}</p>}
    </Card>
  );
}

function Progress({ value, colorVar }) {
  return (
    <div className="rounded-full h-2 overflow-hidden" style={{ background: 'var(--gc-muted)' }}>
      <div className="h-full rounded-full" style={{ width: `${Math.min(Math.max(value, 0), 100)}%`, background: colorVar || 'var(--gc-primary)' }} />
    </div>
  );
}

function ShiftCard({ shiftKey, rows, settings, date, onOpenMachines }) {
  const meta = SHIFT_META[shiftKey];
  const shiftRows = useMemo(() => rows.filter((r) => {
    if (!r.dt_real) return false;
    const d = new Date(r.dt_real);
    return productionDateOf(d) === date && getShift(d) === shiftKey;
  }), [rows, date, shiftKey]);

  const metros = shiftRows.reduce((s, r) => s + (Number(r.metro_padrao) || 0), 0);
  const pecas = shiftRows.length;
  const kg = shiftRows.reduce((s, r) => s + (Number(r.qt_pesos) || 0), 0);
  const machines = [...new Set(shiftRows.map((r) => normalizeMachine(r.cod_maquina)))].filter(Boolean);
  const capPecas = settings.cap_pecas_turno ?? CAP_PECAS_INSTALADA_TURNO_FIXA;
  const capMetros = settings.cap_metros_turno ?? CAP_METROS_INSTALADA_TURNO_FIXA;
  const efic = pct(pecas, Math.max(machines.length, 1) * PECAS_POR_TURNO_POR_MAQ);
  const tone = toneFor(efic);
  const metaTurno = settings.meta_turno || 0;
  const atingido = pct(metros, metaTurno);

  const now = new Date();
  const hasStarted = productionDateOf(now) > date || (productionDateOf(now) === date && (() => {
    const mm = now.toLocaleTimeString('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit', hour12: false });
    const [h, m] = mm.split(':').map(Number);
    return h * 60 + m >= meta.from || shiftKey !== 'NOITE';
  })());
  if (!hasStarted && pecas === 0) return null;

  return (
    <Card style={{ background: 'var(--gc-card)', border: '1px solid var(--gc-border)' }} className="overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '1px solid var(--gc-border)' }}>
        <div className="flex items-center gap-2">
          <span className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold" style={{ background: 'var(--gc-primary)', color: '#fff' }}>{shiftKey === 'MANHA' ? '1º' : shiftKey === 'TARDE' ? '2º' : '3º'}</span>
          <div>
            <p className="text-sm font-heading font-bold" style={{ color: 'var(--gc-foreground)' }}>{meta.label}</p>
            <p className="text-[10px]" style={{ color: 'var(--gc-muted-foreground)' }}>{meta.horario}</p>
          </div>
        </div>
        <span className="text-[11px] font-bold px-2 py-0.5 rounded-full" style={{ background: `${TONE_COLOR[tone]}22`, color: TONE_COLOR[tone] }}>{Math.round(efic)}%</span>
      </div>
      <div className="p-4 space-y-3">
        <div className="grid grid-cols-3 gap-2 text-center">
          <div><p className="text-[9px] uppercase" style={{ color: 'var(--gc-muted-foreground)' }}>Metros</p><p className="font-heading font-bold" style={{ color: 'var(--gc-foreground)' }}>{nf(metros)}</p></div>
          <div><p className="text-[9px] uppercase" style={{ color: 'var(--gc-muted-foreground)' }}>Peças</p><p className="font-heading font-bold" style={{ color: 'var(--gc-foreground)' }}>{nf(pecas)}</p></div>
          <div><p className="text-[9px] uppercase" style={{ color: 'var(--gc-muted-foreground)' }}>Quilos</p><p className="font-heading font-bold" style={{ color: 'var(--gc-foreground)' }}>{nfKg(kg)}</p></div>
        </div>
        <div>
          <div className="flex justify-between text-[10px] mb-1" style={{ color: 'var(--gc-muted-foreground)' }}><span>Capacidade — Metros</span><span>{nf(metros)} / {nf(capMetros)}</span></div>
          <Progress value={pct(metros, capMetros)} />
        </div>
        <div>
          <div className="flex justify-between text-[10px] mb-1" style={{ color: 'var(--gc-muted-foreground)' }}><span>Capacidade — Peças</span><span>{pecas} / {capPecas}</span></div>
          <Progress value={pct(pecas, capPecas)} colorVar="var(--gc-success)" />
        </div>
        <div className="flex items-center justify-between pt-1">
          <div>
            <p className="text-[9px] uppercase" style={{ color: 'var(--gc-muted-foreground)' }}>Meta do turno</p>
            <p className="text-xs font-semibold" style={{ color: 'var(--gc-foreground)' }}>{nf(metaTurno)} mt</p>
          </div>
          <p className="text-sm font-heading font-bold" style={{ color: TONE_COLOR[toneFor(atingido)] }}>{nf1(atingido)}%</p>
        </div>
        <button type="button" onClick={() => onOpenMachines(shiftKey, shiftRows, machines)} className="w-full text-left text-[11px] pt-2" style={{ borderTop: '1px solid var(--gc-border)', color: 'var(--gc-primary)' }}>
          Máquinas ativas {machines.length} / {settings.cap_maquinas ?? 28}
        </button>
      </div>
    </Card>
  );
}

export default function ControleProducao() {
  const { toast } = useToast();
  const qc = useQueryClient();
  const [date, setDate] = useState(todayISO);
  const [heOn, setHeOn] = useState(false);
  const [heDialogOpen, setHeDialogOpen] = useState(false);
  const [heDate, setHeDate] = useState(todayISO);
  const [month, setMonth] = useState(todayMonth);
  const [machinesDialog, setMachinesDialog] = useState(null);
  const [reprovaSearch, setReprovaSearch] = useState('');

  const { data: rows = [], isFetching, refetch } = useQuery({
    queryKey: ['controle_producao_movimentos'],
    queryFn: () => fetch(URLS.movimentos).then((r) => r.json()),
    staleTime: 60_000,
    refetchInterval: heOn ? 20_000 : 120_000,
  });

  const { data: settings } = useQuery({
    queryKey: ['controle_producao_settings'],
    queryFn: () => fetch(URLS.settings).then((r) => r.json()),
  });

  const { data: reprovas = [] } = useQuery({
    queryKey: ['controle_producao_reprovas'],
    queryFn: () => fetch(URLS.reprovas).then((r) => r.json()),
    refetchInterval: 60_000,
  });

  const saveSetting = async (campo, valor) => {
    const res = await fetch(URLS.settings, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [campo]: valor }),
    }).then((r) => r.json());
    qc.setQueryData(['controle_producao_settings'], res);
    toast({ title: 'Configuração salva' });
  };

  const hardRefresh = () => {
    refetch();
    toast({ title: 'Atualizando...', description: 'Buscando dados mais recentes do Relatório 32.' });
  };

  const dayRows = useMemo(() => rows.filter((r) => r.dt_real && !isSample(r.cod_artigo) && productionDateOf(new Date(r.dt_real)) === date), [rows, date]);
  const totalMetros = dayRows.reduce((s, r) => s + (Number(r.metro_padrao) || 0), 0);
  const totalPecas = dayRows.length;
  const totalKg = dayRows.reduce((s, r) => s + (Number(r.qt_pesos) || 0), 0);

  const s = settings || { meta_dia: 13000, meta_turno: 4334, media_padrao: 48, cap_maquinas: 28, cap_pecas_turno: 36, cap_metros_turno: null };
  const capMetrosDia = (s.cap_metros_turno ?? CAP_METROS_INSTALADA_TURNO_FIXA) * TURNOS;
  const capPecasDia = (s.cap_pecas_turno ?? CAP_PECAS_INSTALADA_TURNO_FIXA) * TURNOS;
  const eficDia = pct(totalMetros, capMetrosDia);
  const eficPecas = pct(totalPecas, capPecasDia);
  const progressoMeta = pct(totalMetros, s.meta_dia);

  const byMachine = useMemo(() => {
    const map = {};
    dayRows.forEach((r) => {
      const m = normalizeMachine(r.cod_maquina);
      if (!m) return;
      map[m] = (map[m] || 0) + (Number(r.metro_padrao) || 0);
    });
    return Object.entries(map).sort((a, b) => b[1] - a[1]);
  }, [dayRows]);

  // H.E. — 06:00 -> 06:00 seguinte, Turno A (06-18h) / Turno B (18:01-06h)
  const heData = useMemo(() => {
    if (!heOn) return null;
    const start = new Date(`${heDate}T09:00:00.000Z`);
    const end = new Date(start.getTime() + 24 * 60 * 60 * 1000);
    const inWindow = rows.filter((r) => r.dt_real && !isSample(r.cod_artigo) && new Date(r.dt_real) >= start && new Date(r.dt_real) < end);
    const hourBuckets = Array.from({ length: 24 }, () => ({ metros: 0, pecas: 0 }));
    let metros = 0, pecas = 0, kg = 0;
    const turnoA = { metros: 0, pecas: 0, kg: 0, machines: new Set() };
    const turnoB = { metros: 0, pecas: 0, kg: 0, machines: new Set() };
    let lastTs = null;
    inWindow.forEach((r) => {
      const d = new Date(r.dt_real);
      const localHourStr = d.toLocaleTimeString('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', hour12: false });
      const hourIdx = (parseInt(localHourStr, 10) - 6 + 24) % 24;
      const m = Number(r.metro_padrao) || 0;
      hourBuckets[hourIdx].metros += m;
      hourBuckets[hourIdx].pecas += 1;
      metros += m; pecas += 1; kg += Number(r.qt_pesos) || 0;
      const isA = hourIdx < 12;
      const bucket = isA ? turnoA : turnoB;
      bucket.metros += m; bucket.kg += Number(r.qt_pesos) || 0; bucket.pecas += 1;
      bucket.machines.add(normalizeMachine(r.cod_maquina));
      if (!lastTs || d > lastTs) lastTs = d;
    });
    return { start, end, metros, pecas, kg, hourBuckets, turnoA, turnoB, lastTs, machineCount: new Set(inWindow.map((r) => normalizeMachine(r.cod_maquina))).size };
  }, [rows, heOn, heDate]);

  // Producao total do mes
  const monthStats = useMemo(() => {
    const rowsMonth = rows.filter((r) => r.dt_real && !isSample(r.cod_artigo) && productionDateOf(new Date(r.dt_real)).startsWith(month));
    const byDay = {};
    rowsMonth.forEach((r) => {
      const day = productionDateOf(new Date(r.dt_real));
      (byDay[day] ??= { metros: 0, pecas: 0, byShift: { MANHA: 0, TARDE: 0, NOITE: 0 } });
      const m = Number(r.metro_padrao) || 0;
      byDay[day].metros += m;
      byDay[day].pecas += 1;
      byDay[day].byShift[getShift(new Date(r.dt_real))] += m;
    });
    const days = Object.entries(byDay);
    const totalMetros = days.reduce((s2, [, v]) => s2 + v.metros, 0);
    const totalPecasMes = days.reduce((s2, [, v]) => s2 + v.pecas, 0);
    const diasComProducao = days.length;
    const diasAcimaMeta = days.filter(([, v]) => v.metros >= (s.meta_dia || 0)).sort((a, b) => b[1].metros - a[1].metros);
    const topPecas = [...days].sort((a, b) => b[1].pecas - a[1].pecas).slice(0, 5);
    const shiftTotals = { MANHA: 0, TARDE: 0, NOITE: 0 };
    days.forEach(([, v]) => { shiftTotals.MANHA += v.byShift.MANHA; shiftTotals.TARDE += v.byShift.TARDE; shiftTotals.NOITE += v.byShift.NOITE; });
    const denom = Math.max(diasComProducao, 1);
    const shiftAvg = Object.entries(shiftTotals).map(([k, v]) => ({ key: k, avg: v / denom })).sort((a, b) => b.avg - a.avg);
    return { totalMetros, totalPecasMes, diasComProducao, diasAcimaMeta, topPecas, shiftAvg };
  }, [rows, month, s.meta_dia]);

  return (
    <div className="gc-scope p-4 md:p-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="text-3xl">🧶</span>
          <div>
            <p className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: 'var(--gc-muted-foreground)' }}>PCP Conexão / Malharia / Painel Executivo</p>
            <h1 className="font-heading text-xl font-bold" style={{ color: 'var(--gc-foreground)' }}>Dados Malharia</h1>
            <p className="text-xs" style={{ color: 'var(--gc-muted-foreground)' }}>Controle de produção por turno · Atualização diária 07:25</p>
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="text-[10px] block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Data base</label>
            <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="w-40" />
          </div>
          <Button size="sm" onClick={hardRefresh} disabled={isFetching}>
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin' : ''}`} /> Atualizar
          </Button>
          <Button
            size="sm"
            onClick={() => (heOn ? setHeOn(false) : setHeDialogOpen(true))}
            style={heOn ? { background: 'linear-gradient(90deg,#a21caf,#4338ca)', color: '#fff' } : undefined}
            variant={heOn ? 'default' : 'outline'}
          >
            <Zap className="w-3.5 h-3.5" /> H.E. {heOn ? 'ON' : 'OFF'}
          </Button>
        </div>
      </div>

      <Dialog open={heDialogOpen} onOpenChange={setHeDialogOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>Ativar H.E.</DialogTitle></DialogHeader>
          <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Data (ciclo 06h → 06h seguinte)</label>
          <Input type="date" value={heDate} onChange={(e) => setHeDate(e.target.value)} className="w-full mb-3" />
          <Button onClick={() => { setHeOn(true); setHeDialogOpen(false); }}>Ativar H.E.</Button>
        </DialogContent>
      </Dialog>

      {heOn && heData && (
        <Card style={{ background: 'linear-gradient(135deg,#1e1b4b,#3b0764)', border: '1px solid rgba(168,85,247,0.4)' }} className="p-4 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2"><span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: '#e879f9' }} /><span className="relative inline-flex rounded-full h-2 w-2" style={{ background: '#e879f9' }} /></span>
              <p className="text-sm font-heading font-bold text-white">H.E. · Apuração em tempo real</p>
            </div>
            <div className="flex items-center gap-2">
              <Input type="date" value={heDate} onChange={(e) => setHeDate(e.target.value)} className="w-36" />
              <Button size="sm" variant="outline" onClick={() => refetch()}>Agora</Button>
            </div>
          </div>
          <p className="text-[11px]" style={{ color: 'rgba(255,255,255,0.7)' }}>
            {heData.start.toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })} → {heData.end.toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
            {' · '}{heData.machineCount} máquinas · última pesagem {heData.lastTs ? heData.lastTs.toLocaleTimeString('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit' }) : '—'}
          </p>
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-lg p-3" style={{ background: 'rgba(255,255,255,0.08)' }}><p className="text-[10px] uppercase text-white/70">Metros</p><p className="text-xl font-heading font-bold text-white">{nf(heData.metros)}</p></div>
            <div className="rounded-lg p-3" style={{ background: 'rgba(255,255,255,0.08)' }}><p className="text-[10px] uppercase text-white/70">Peças</p><p className="text-xl font-heading font-bold text-white">{nf(heData.pecas)}</p></div>
            <div className="rounded-lg p-3" style={{ background: 'rgba(255,255,255,0.08)' }}><p className="text-[10px] uppercase text-white/70">Quilos</p><p className="text-xl font-heading font-bold text-white">{nfKg(heData.kg)}</p></div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {[['Turno A (06:00–18:00)', heData.turnoA], ['Turno B (18:01–06:00)', heData.turnoB]].map(([label, t]) => (
              <div key={label} className="rounded-lg p-3" style={{ background: 'rgba(255,255,255,0.06)' }}>
                <p className="text-xs font-semibold text-white mb-1">{label}</p>
                <p className="text-[11px] text-white/70">Metros: {nf(t.metros)} · Peças: {t.pecas} · Kg: {nfKg(t.kg)} · Máqs: {t.machines.size}</p>
                <p className="text-[11px] text-white/70">{nf1(pct(t.metros, heData.metros || 1))}% do total</p>
              </div>
            ))}
          </div>
          <div className="flex items-end gap-1 h-16">
            {heData.hourBuckets.map((b, i) => {
              const max = Math.max(...heData.hourBuckets.map((x) => x.metros), 1);
              const hourLabel = (6 + i) % 24;
              return (
                <div key={i} className="flex-1 h-full flex items-end" title={`${hourLabel}h — ${nf(b.metros)}m · ${b.pecas}pç`}>
                  <div style={{ height: `${Math.max((b.metros / max) * 100, 2)}%`, width: '100%', background: '#e879f9', borderRadius: 2 }} />
                </div>
              );
            })}
          </div>
        </Card>
      )}

      <Card style={{ background: 'var(--gc-card)', border: '1px solid var(--gc-border)' }} className="p-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <div className="flex items-center justify-between mb-2">
              <div>
                <p className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--gc-muted-foreground)' }}>Produção do dia</p>
                <p className="text-xs font-semibold" style={{ color: 'var(--gc-foreground)' }}>{date.split('-').reverse().join('/')}</p>
              </div>
              <span className="text-[11px] font-bold px-2 py-0.5 rounded-full" style={{ background: `${TONE_COLOR[toneFor(eficDia)]}22`, color: TONE_COLOR[toneFor(eficDia)] }}>Eficiência {nf1(eficDia)}%</span>
            </div>
            <div className="grid grid-cols-3 gap-3 mb-3">
              <StatBlock icon={Ruler} label="Metros" value={nf(totalMetros)} sub={`/ ${nf(capMetrosDia)} instalados`} />
              <StatBlock icon={Package} label="Peças" value={nf(totalPecas)} sub={`/ ${nf(capPecasDia)} cap.`} />
              <StatBlock icon={Weight} label="Quilos" value={nfKg(totalKg)} sub="produzidos no dia" />
            </div>
            <div>
              <div className="flex justify-between text-[10px] mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>
                <span>Progresso da meta diária</span><span>{nf(totalMetros)} / {nf(s.meta_dia)} mt</span>
              </div>
              <Progress value={progressoMeta} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <EditableSetting label="Meta diária" value={s.meta_dia} unit="metros" disabled={!IS_ADMIN} onSave={(v) => saveSetting('meta_dia', v)} />
            <EditableSetting label="Meta por turno" value={s.meta_turno} unit="metros" disabled={!IS_ADMIN} onSave={(v) => saveSetting('meta_turno', v)} />
            <EditableSetting label="Média padrão" value={s.media_padrao} unit="mt/pç" disabled={!IS_ADMIN} onSave={(v) => saveSetting('media_padrao', v)} />
            <EditableSetting label="Cap. instalada" value={s.cap_maquinas} unit="máqs" disabled={!IS_ADMIN} onSave={(v) => saveSetting('cap_maquinas', v)} />
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <HeaderKpi icon={Ruler} label="Total Metros" value={nf(totalMetros)} sub="produzidos no dia" />
        <HeaderKpi icon={Package} label="Total Peças" value={nf(totalPecas)} sub={`cap. ${nf(capPecasDia)}`} />
        <HeaderKpi icon={Weight} label="Total Quilos" value={nfKg(totalKg)} sub="kg do dia" />
        <HeaderKpi icon={Gauge} label="Eficiência Peças" value={`${nf1(eficPecas)}%`} sub="vs. capacidade" accent={TONE_COLOR[toneFor(eficPecas)]} />
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <div>
            <p className="font-heading font-bold text-sm" style={{ color: 'var(--gc-foreground)' }}>Desempenho por Turno</p>
            <p className="text-[11px]" style={{ color: 'var(--gc-muted-foreground)' }}>Produção, capacidade e eficiência detalhadas</p>
          </div>
          <div className="flex items-center gap-3 text-[10px]" style={{ color: 'var(--gc-muted-foreground)' }}>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: TONE_COLOR.emerald }} /> ≥90%</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: TONE_COLOR.amber }} /> ≥85%</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: TONE_COLOR.rose }} /> &lt;85%</span>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {Object.keys(SHIFT_META).map((k) => (
            <ShiftCard key={k} shiftKey={k} rows={rows} settings={s} date={date} onOpenMachines={(shiftKey, shiftRows, machines) => setMachinesDialog({ shiftKey, shiftRows, machines })} />
          ))}
        </div>
      </div>

      <Dialog open={!!machinesDialog} onOpenChange={(v) => !v && setMachinesDialog(null)}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader><DialogTitle>Máquinas ativas — {machinesDialog ? SHIFT_META[machinesDialog.shiftKey].label : ''}</DialogTitle></DialogHeader>
          {machinesDialog && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr style={{ borderBottom: '1px solid var(--gc-border)' }}>{['OP', 'Máquina', 'Artigo', 'Qt. Movimento', 'Dt. Real'].map((h) => <th key={h} className="text-left py-2 text-xs font-heading" style={{ color: 'var(--gc-muted-foreground)' }}>{h}</th>)}</tr></thead>
                <tbody>
                  {machinesDialog.shiftRows.map((r, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--gc-border)' }}>
                      <td className="py-1.5">{r.op}</td>
                      <td className="py-1.5">{r.cod_maquina}</td>
                      <td className="py-1.5 text-xs truncate max-w-[220px]">{r.cod_artigo}</td>
                      <td className="py-1.5">{Number(r.qt_movimento).toLocaleString('pt-BR', { maximumFractionDigits: 3 })}</td>
                      <td className="py-1.5 text-xs whitespace-nowrap">{r.dt_real ? new Date(r.dt_real).toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Card>
        <div className="p-4 flex items-center justify-between flex-wrap gap-3" style={{ borderBottom: '1px solid var(--gc-border)' }}>
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" style={{ color: 'var(--gc-destructive)' }} />
            <div>
              <p className="font-heading font-bold text-sm">Reprovas do Cru (Diária)</p>
              <p className="text-[11px]" style={{ color: 'var(--gc-muted-foreground)' }}>Relatório 24 · atualização automática 24h via agente</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center rounded-md border px-2 h-9" style={{ borderColor: 'var(--gc-border)' }}>
              <Search className="w-3.5 h-3.5 mr-1" style={{ color: 'var(--gc-muted-foreground)' }} />
              <Input value={reprovaSearch} onChange={(e) => setReprovaSearch(e.target.value)} placeholder="Pesquisar..." className="border-none bg-transparent text-xs" style={{ height: 'auto' }} />
            </div>
          </div>
        </div>
        <div className="p-4">
          <p className="text-xs font-semibold mb-2" style={{ color: 'var(--gc-muted-foreground)' }}>Reprovas — {date.split('-').reverse().join('/')} · Itens: {reprovas.length} · Qt. movimento: 0</p>
          {reprovas.length === 0 ? (
            <p className="text-sm text-center py-6" style={{ color: 'var(--gc-muted-foreground)' }}>Nenhuma reprova registrada — aguardando dados do agente do Relatório 24.</p>
          ) : (
            <table className="w-full text-sm">
              <tbody>
                {reprovas.map((r, i) => <tr key={i}><td>{r.op}</td></tr>)}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      <Card className="p-4 flex items-center gap-3" style={{ opacity: 0.7 }}>
        <Wrench className="w-5 h-5" style={{ color: 'var(--gc-muted-foreground)' }} />
        <div className="flex-1">
          <p className="font-heading font-bold text-sm">Setup / Trocas do dia</p>
          <p className="text-[11px]" style={{ color: 'var(--gc-muted-foreground)' }}>Em breve — integrado à aba Malharia 2.0 (trocas de máquina)</p>
        </div>
        <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded-full" style={{ background: 'var(--gc-muted)', color: 'var(--gc-muted-foreground)' }}>Em breve</span>
      </Card>

      <Card>
        <div className="p-4 flex items-center justify-between flex-wrap gap-3" style={{ borderBottom: '1px solid var(--gc-border)' }}>
          <div className="flex items-center gap-2">
            <CalendarRange className="w-4 h-4" style={{ color: 'var(--gc-primary)' }} />
            <p className="font-heading font-bold text-sm">Produção total do mês</p>
          </div>
          <div className="flex items-center gap-2">
            <Input type="month" value={month} max={todayMonth} onChange={(e) => setMonth(e.target.value)} className="w-40" />
            {month !== todayMonth && <Button size="sm" variant="ghost" onClick={() => setMonth(todayMonth)}>Mês atual</Button>}
          </div>
        </div>
        <div className="p-4 space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatBlock icon={Ruler} label="Total produzido" value={`${nf(monthStats.totalMetros)} m`} />
            <StatBlock icon={Package} label="Peças totais" value={nf(monthStats.totalPecasMes)} />
            <StatBlock icon={Target} label="Dias com produção" value={monthStats.diasComProducao} sub={`média ${nf(monthStats.totalMetros / Math.max(monthStats.diasComProducao, 1))} mt/dia`} />
            <StatBlock icon={Trophy} label={`Dias ≥ ${nf(s.meta_dia)} mt`} value={monthStats.diasAcimaMeta.length} />
          </div>
          <div>
            <div className="flex justify-between text-[10px] mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>
              <span>Meta do mês · {nf(s.meta_dia)} mt/dia × {monthStats.diasComProducao} dias</span>
              <span>{nf1(pct(monthStats.totalMetros, s.meta_dia * Math.max(monthStats.diasComProducao, 1)))}%</span>
            </div>
            <Progress value={pct(monthStats.totalMetros, s.meta_dia * Math.max(monthStats.diasComProducao, 1))} />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <p className="text-[11px] font-semibold uppercase mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Dias acima da meta</p>
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {monthStats.diasAcimaMeta.slice(0, 10).map(([d, v]) => (
                  <div key={d} className="flex justify-between text-xs"><span>{d.split('-').reverse().join('/')}</span><span className="font-semibold">{nf(v.metros)} m</span></div>
                ))}
                {monthStats.diasAcimaMeta.length === 0 && <p className="text-xs" style={{ color: 'var(--gc-muted-foreground)' }}>Nenhum dia ainda.</p>}
              </div>
            </div>
            <div>
              <p className="text-[11px] font-semibold uppercase mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Dias com mais peças</p>
              <div className="space-y-1">
                {monthStats.topPecas.map(([d, v]) => (
                  <div key={d} className="flex justify-between text-xs"><span>{d.split('-').reverse().join('/')}</span><span className="font-semibold">{v.pecas} pçs</span></div>
                ))}
              </div>
            </div>
            <div>
              <p className="text-[11px] font-semibold uppercase mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Turnos · melhor eficiência</p>
              <div className="space-y-1">
                {monthStats.shiftAvg.map((t, i) => (
                  <div key={t.key} className="flex justify-between text-xs items-center">
                    <span>{SHIFT_META[t.key].label} {i === 0 && <span className="ml-1 text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: `${TONE_COLOR.emerald}22`, color: TONE_COLOR.emerald }}>Líder</span>}</span>
                    <span className="font-semibold">{nf(t.avg)} mt/dia</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </Card>

      <Card>
        <div className="p-4" style={{ borderBottom: '1px solid var(--gc-border)' }}>
          <p className="font-heading font-bold text-sm">Metragem por Máquina</p>
        </div>
        <div className="p-4 space-y-2 max-h-72 overflow-y-auto">
          {byMachine.length === 0 ? (
            <p className="text-sm text-center py-6" style={{ color: 'var(--gc-muted-foreground)' }}>Nenhum dado para a data selecionada.</p>
          ) : byMachine.map(([m, total]) => {
            const max = byMachine[0][1] || 1;
            return (
              <div key={m} className="flex items-center gap-3">
                <span className="text-sm font-mono w-16" style={{ color: 'var(--gc-muted-foreground)' }}>Máq. {m}</span>
                <div className="flex-1 rounded-full h-6 overflow-hidden" style={{ background: 'var(--gc-muted)' }}>
                  <div className="h-full rounded-full flex items-center justify-end pr-2" style={{ width: `${Math.max((total / max) * 100, 5)}%`, background: 'var(--gc-primary)' }}>
                    <span className="text-xs text-white font-semibold">{nf(total)}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}
