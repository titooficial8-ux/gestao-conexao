import { useEffect, useMemo, useRef, useState, useCallback, memo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ChevronLeft, ChevronRight, Pause, Play, Maximize, Minimize, Clock, Trophy, Target, RefreshCw,
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, ResponsiveContainer, ReferenceLine, Tooltip, CartesianGrid,
} from 'recharts';
import {
  brParts, prodDayKeyFast, shiftOfFast, SHIFT_LABELS, SHIFT_RANGES, SHIFT_ORDINAL, SHIFT_SEQUENCE,
} from '../../lib/malhariaShifts';
import { useIsMobile } from '../../hooks/use-mobile';

const conexaoLogo = '/static/img/logo_conexao_wordmark.png';

// ---------- METAS (ajustaveis) ----------
const META_DIARIA_METROS = 13000;
const META_TURNO_METROS = Math.round(META_DIARIA_METROS / 3);

const SLIDE_MS = 20_000;
const AUTO_VOLTAR_HOJE_MS = 10 * 60_000;
const ATUALIZACAO_MS = 75 * 60_000;
const ATUALIZACAO_TXT = `${Math.floor(ATUALIZACAO_MS / 3_600_000)}h${String(Math.round((ATUALIZACAO_MS % 3_600_000) / 60_000)).padStart(2, '0')}min`;

// ---------- Formatacao ----------
const nf0 = (n) => Math.round(n || 0).toLocaleString('pt-BR');
const nf1 = (n) => (Number.isFinite(n) ? n : 0).toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const dayLabel = (key) => { const [, m, d] = key.split('-'); return `${d}/${m}`; };
const dayLabelFull = (key) => { const [y, m, d] = key.split('-'); return `${d}/${m}/${y}`; };

const emptyTotals = () => ({ metros: 0, pecas: 0, quilos: 0 });

// ---------- UI atoms ----------
const Label = memo(({ children, className = '' }) => (
  <div className={`font-body text-[0.7rem] md:text-xs uppercase tracking-[0.22em] text-[#6B7280] ${className}`}>{children}</div>
));

const Card = memo(({ children, className = '', primary = false, idx = 0 }) => (
  <div
    className={`dash-laminate ${primary ? 'dash-laminate-primary' : ''} rounded-3xl border border-[#E5E7EB] bg-[#FFFFFF] ${className}`}
    style={{ animationDelay: `${(idx % 6) * 1.1}s` }}
  >
    <span className="dash-laminate-sheen" style={{ animationDelay: `${(idx % 6) * 1.1}s` }} aria-hidden />
    {children}
  </div>
));

const Progress = memo(({ pct, className = '' }) => (
  <div className={`h-3 w-full overflow-hidden rounded-full bg-[#F3F4F6] ${className}`}>
    <motion.div
      className="h-full rounded-full bg-[#0C6FE0]"
      initial={{ width: 0 }}
      animate={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
      transition={{ duration: 0.9, ease: 'easeOut' }}
    />
  </div>
));

const Ring = ({ pct, size = 190 }) => {
  const stroke = 16;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const filled = Math.min(100, Math.max(0, pct)) / 100;
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} className="fill-none stroke-[#F3F4F6]" />
        <motion.circle
          cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} strokeLinecap="round"
          className="fill-none stroke-[#0C6FE0]"
          strokeDasharray={c}
          initial={{ strokeDashoffset: c }}
          animate={{ strokeDashoffset: c - c * filled }}
          transition={{ duration: 1, ease: 'easeOut' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <div className="font-heading text-4xl font-bold text-[#111827]">{nf1(pct)}%</div>
        <Label>da meta</Label>
      </div>
    </div>
  );
};

const BigNumber = ({ value, unit, label, size = 'text-4xl md:text-8xl xl:text-9xl' }) => (
  <div className="flex flex-col gap-2">
    <Label>{label}</Label>
    <div className="flex items-end gap-2">
      <span className={`font-heading font-bold leading-none tracking-tight text-[#111827] whitespace-nowrap ${size}`}>{value}</span>
      {unit && <span className="font-body pb-2 text-xl text-[#6B7280] md:text-2xl">{unit}</span>}
    </div>
  </div>
);

const Empty = memo(({ children }) => (
  <div className="flex h-full w-full flex-col items-center justify-center gap-3 text-center">
    <div className="font-heading text-2xl text-[#111827]/70">{children}</div>
    <Label>Sem dados no período</Label>
  </div>
));

const LiveClock = memo(({ shiftText, updated }) => {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const p = brParts(now);
  const clock = `${String(p.hour).padStart(2, '0')}:${String(p.minute).padStart(2, '0')}`;
  return (
    <div className="min-w-0 text-right">
      <div className="flex items-center justify-end gap-1 font-heading text-sm font-bold text-[#111827] md:gap-2 md:text-3xl">
        <Clock className="h-3.5 w-3.5 text-[#0C6FE0] md:h-5 md:w-5" /> {clock}
      </div>
      <Label className="whitespace-nowrap text-[0.5rem] tracking-normal md:text-xs md:tracking-[0.22em]">{shiftText} · atualizado às {updated}</Label>
    </div>
  );
});

const SlideProgress = memo(({ slideKey, durationMs, paused }) => (
  <div className="absolute left-0 top-0 z-20 h-1 w-full bg-[#F3F4F6]">
    <style>{`@keyframes dashSlideProgress { from { width: 0% } to { width: 100% } }`}</style>
    <div
      key={slideKey}
      className="h-full bg-[#0C6FE0]"
      style={{
        animation: `dashSlideProgress ${durationMs}ms linear forwards`,
        animationPlayState: paused ? 'paused' : 'running',
      }}
    />
  </div>
));

// ================= DADOS (API local do Gestao Conexao) =================
// Mesma forma dos dados do PCP Hub (tabela report_32_movement), so que
// servida pelo Flask local em vez de consultada direto no Supabase.
const fetchRange = async (fromISO, toISO) => {
  const url = `/api/report32?from=${encodeURIComponent(fromISO)}&to=${encodeURIComponent(toISO)}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error('Falha ao buscar dados do Dashboard Tempo Real');
  return r.json();
};

const fetchRows = (daysBack) => {
  const since = new Date();
  since.setDate(since.getDate() - daysBack);
  return fetchRange(since.toISOString(), new Date(Date.now() + 86400000).toISOString());
};

const monthRange = (month) => {
  const [y, m] = month.split('-').map(Number);
  const start = Date.UTC(y, m - 1, 1, 9, 20, 0);
  const end = Date.UTC(y, m, 1, 9, 19, 59, 999);
  return { fromISO: new Date(start).toISOString(), toISO: new Date(end).toISOString() };
};
const monthOf = (dayKey) => dayKey.slice(0, 7);
const lastDayOfMonthKey = (month) => {
  const [y, m] = month.split('-').map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return `${month}-${String(last).padStart(2, '0')}`;
};
const firstDayOfMonthKey = (month) => `${month}-01`;
const MONTH_FMT = new Intl.DateTimeFormat('pt-BR', { month: 'long', year: 'numeric', timeZone: 'UTC' });
const monthLabel = (month) => {
  const [y, m] = month.split('-').map(Number);
  const txt = MONTH_FMT.format(new Date(Date.UTC(y, m - 1, 1)));
  const [mes, ano] = txt.split(' de ');
  return `${mes.charAt(0).toUpperCase()}${mes.slice(1)}/${ano ?? y}`;
};

const DashboardTempoReal = () => {
  const isMobile = useIsMobile();
  const [slide, setSlide] = useState(0);
  const [paused, setPaused] = useState(false);
  const [isFull, setIsFull] = useState(false);
  const rootRef = useRef(null);
  const queryClient = useQueryClient();

  const [dayShift, setDayShift] = useState(() => {
    const ms = Date.now();
    return { day: prodDayKeyFast(ms), shift: shiftOfFast(ms) };
  });
  useEffect(() => {
    const t = setInterval(() => {
      const ms = Date.now();
      const day = prodDayKeyFast(ms);
      const shift = shiftOfFast(ms);
      setDayShift((prev) => (prev.day === day && prev.shift === shift ? prev : { day, shift }));
    }, 20_000);
    return () => clearInterval(t);
  }, []);
  const todayKey = dayShift.day;
  const currentShift = dayShift.shift;

  const [selectedDay, setSelectedDay] = useState(todayKey);
  const [selectedMonth, setSelectedMonth] = useState(() => monthOf(todayKey));
  const isToday = selectedDay === todayKey;
  const currentMonth = monthOf(todayKey);
  const minDay = firstDayOfMonthKey(selectedMonth);
  const maxDay = selectedMonth === currentMonth ? todayKey : lastDayOfMonthKey(selectedMonth);
  const refineMonthPick = useRef(false);

  const prevTodayKey = useRef(todayKey);
  useEffect(() => {
    if (prevTodayKey.current !== todayKey) {
      const prevDay = prevTodayKey.current;
      setSelectedDay((cur) => (cur === prevDay ? todayKey : cur));
      setSelectedMonth((cur) => (cur === monthOf(prevDay) && monthOf(prevDay) !== monthOf(todayKey) ? monthOf(todayKey) : cur));
      prevTodayKey.current = todayKey;
    }
  }, [todayKey]);

  useEffect(() => {
    if (!AUTO_VOLTAR_HOJE_MS || isToday) return;
    const t = setTimeout(() => {
      const d = prodDayKeyFast(Date.now());
      refineMonthPick.current = false;
      setSelectedDay(d);
      setSelectedMonth(monthOf(d));
    }, AUTO_VOLTAR_HOJE_MS);
    return () => clearTimeout(t);
  }, [selectedDay, selectedMonth, isToday]);

  const pickDay = useCallback((v) => {
    if (!v) return;
    refineMonthPick.current = false;
    setSelectedDay(v);
    setSelectedMonth(monthOf(v));
  }, []);

  const pickMonth = useCallback((v) => {
    if (!v) return;
    const today = prodDayKeyFast(Date.now());
    setSelectedMonth(v);
    if (v === monthOf(today)) {
      refineMonthPick.current = false;
      setSelectedDay(today);
    } else {
      refineMonthPick.current = true;
      setSelectedDay(lastDayOfMonthKey(v));
    }
  }, []);

  const { data: todayRows = [], isLoading: loadingToday, dataUpdatedAt } = useQuery({
    queryKey: ['dash_r32_hoje'],
    queryFn: () => fetchRows(2),
    refetchInterval: ATUALIZACAO_MS,
    refetchOnWindowFocus: false,
  });

  const { data: histRows = [], isLoading: loadingHist } = useQuery({
    queryKey: ['dash_r32_mes', selectedMonth],
    queryFn: () => { const { fromISO, toISO } = monthRange(selectedMonth); return fetchRange(fromISO, toISO); },
    refetchInterval: ATUALIZACAO_MS,
    refetchOnWindowFocus: false,
    placeholderData: (prev) => prev,
  });

  const dayRows = isToday ? todayRows : histRows;
  const agg = useMemo(() => {
    const dayTotals = emptyTotals();
    const byShift = new Map();
    const machines = new Set();
    const machineByShift = new Map();

    dayRows.forEach((r) => {
      if (!r.dt_real) return;
      const ms = new Date(r.dt_real).getTime();
      if (Number.isNaN(ms)) return;
      if (prodDayKeyFast(ms) !== selectedDay) return;
      const sh = shiftOfFast(ms);
      const metros = Number(r.metro_padrao ?? 0) || 0;
      const quilos = Number(r.qt_pesos ?? 0) || 0;
      const maq = String(r.cod_maquina ?? '').trim();

      dayTotals.metros += metros; dayTotals.quilos += quilos; dayTotals.pecas += 1;

      const st = byShift.get(sh) ?? emptyTotals();
      st.metros += metros; st.quilos += quilos; st.pecas += 1;
      byShift.set(sh, st);

      if (maq) {
        machines.add(maq);
        const mm = machineByShift.get(sh) ?? new Map();
        const mt = mm.get(maq) ?? emptyTotals();
        mt.metros += metros; mt.quilos += quilos; mt.pecas += 1;
        mm.set(maq, mt);
        machineByShift.set(sh, mm);
      }
    });

    const shiftTotals = SHIFT_SEQUENCE.reduce((acc, sh) => {
      acc[sh] = byShift.get(sh) ?? emptyTotals();
      return acc;
    }, {});

    let focusShift = currentShift;
    if (!isToday) {
      let last = null;
      SHIFT_SEQUENCE.forEach((sh) => { if (shiftTotals[sh].pecas > 0) last = sh; });
      focusShift = last ?? SHIFT_SEQUENCE[0];
    }

    const machineRanking = Array.from((machineByShift.get(focusShift) ?? new Map()).entries())
      .map(([machine, t]) => ({ machine, ...t }))
      .sort((a, b) => b.metros - a.metros || b.pecas - a.pecas);

    return { today: dayTotals, todayMachines: machines.size, shiftTotals, machineRanking, focusShift };
  }, [dayRows, selectedDay, currentShift, isToday]);

  const hist = useMemo(() => {
    const byDay = new Map();
    const byDayShift = new Map();

    histRows.forEach((r) => {
      if (!r.dt_real) return;
      const ms = new Date(r.dt_real).getTime();
      if (Number.isNaN(ms)) return;
      const day = prodDayKeyFast(ms);
      const sh = shiftOfFast(ms);
      const metros = Number(r.metro_padrao ?? 0) || 0;
      const quilos = Number(r.qt_pesos ?? 0) || 0;

      const dt = byDay.get(day) ?? emptyTotals();
      dt.metros += metros; dt.quilos += quilos; dt.pecas += 1;
      byDay.set(day, dt);

      const dsKey = `${day}|${sh}`;
      const st = byDayShift.get(dsKey) ?? emptyTotals();
      st.metros += metros; st.quilos += quilos; st.pecas += 1;
      byDayShift.set(dsKey, st);
    });

    const days = Array.from(byDay.entries())
      .filter(([k]) => monthOf(k) === selectedMonth)
      .map(([day, t]) => ({ day, ...t }))
      .sort((a, b) => (a.day < b.day ? -1 : 1));

    const topPecasDays = [...days].sort((a, b) => b.pecas - a.pecas).slice(0, 6);
    const aboveGoal = days.filter((d) => d.metros > META_DIARIA_METROS).sort((a, b) => b.metros - a.metros);

    const bestShifts = Array.from(byDayShift.entries())
      .map(([k, t]) => {
        const [day, sh] = k.split('|');
        return { day, shift: sh, ...t, ef: (t.metros / META_TURNO_METROS) * 100 };
      })
      .filter((x) => monthOf(x.day) === selectedMonth && x.metros > 0)
      .sort((a, b) => b.ef - a.ef)
      .slice(0, 5);

    const chartDays = days.map((d) => ({ name: dayLabel(d.day), metros: Math.round(d.metros) }));
    const lastProducedDay = days.length ? days[days.length - 1].day : null;

    return { days, topPecasDays, aboveGoal, bestShifts, chartDays, lastProducedDay };
  }, [histRows, selectedMonth]);

  useEffect(() => {
    if (!refineMonthPick.current || loadingHist) return;
    refineMonthPick.current = false;
    if (hist.lastProducedDay) setSelectedDay(hist.lastProducedDay);
  }, [hist.lastProducedDay, loadingHist]);

  const dayPct = (agg.today.metros / META_DIARIA_METROS) * 100;
  const shiftNow = agg.shiftTotals[agg.focusShift];
  const shiftPct = (shiftNow.metros / META_TURNO_METROS) * 100;
  const bestShiftOfDay = useMemo(() => {
    let best = null;
    SHIFT_SEQUENCE.forEach((sh) => {
      const m = agg.shiftTotals[sh].metros;
      if (m > 0 && (best === null || m > agg.shiftTotals[best].metros)) best = sh;
    });
    return best;
  }, [agg.shiftTotals]);

  const SLIDES = 4;
  const next = useCallback(() => setSlide((s) => (s + 1) % SLIDES), []);
  const prev = useCallback(() => setSlide((s) => (s - 1 + SLIDES) % SLIDES), []);

  useEffect(() => {
    if (paused || isMobile) return;
    const t = setTimeout(() => setSlide((s) => (s + 1) % SLIDES), SLIDE_MS);
    return () => clearTimeout(t);
  }, [paused, slide, isMobile]);

  const toggleFull = async () => {
    const el = rootRef.current;
    if (!el) return;
    if (!document.fullscreenElement) { await el.requestFullscreen?.(); setIsFull(true); }
    else { await document.exitFullscreen?.(); setIsFull(false); }
  };
  useEffect(() => {
    const h = () => setIsFull(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', h);
    return () => document.removeEventListener('fullscreenchange', h);
  }, []);

  const updated = dataUpdatedAt ? (() => { const u = brParts(new Date(dataUpdatedAt)); return `${String(u.hour).padStart(2, '0')}:${String(u.minute).padStart(2, '0')}`; })() : '--:--';

  const slideVariants = {
    enter: { opacity: 0, x: 40 },
    center: { opacity: 1, x: 0 },
    exit: { opacity: 0, x: -40 },
  };

  const loadingDay = isToday ? loadingToday : (loadingHist && histRows.length === 0);
  const dayHasRows = agg.today.pecas > 0;
  const visibleChartDays = isMobile ? hist.chartDays.slice(-15) : hist.chartDays;

  return (
    <div ref={rootRef} className={isFull ? 'relative flex h-auto w-full flex-col overflow-visible bg-[#FFFFFF] min-[1600px]:h-[100dvh] min-[1600px]:overflow-hidden' : 'relative flex h-auto min-h-0 w-full flex-col overflow-visible bg-[#FFFFFF] min-[1600px]:h-[calc(100dvh-8rem)] min-[1600px]:min-h-[520px] min-[1600px]:overflow-hidden'}>
      <div className="hidden md:block"><SlideProgress slideKey={slide} durationMs={SLIDE_MS} paused={paused} /></div>

      <div className="shrink-0 space-y-3 px-4 pt-4 xl:hidden">
        <div className="flex min-w-0 items-center gap-2">
          <img src={conexaoLogo} alt="Conexão Malhas e Desenvolvimento" className="h-6 w-auto shrink-0 object-contain" />
          <h1 className="min-w-0 whitespace-nowrap font-heading text-sm font-bold uppercase tracking-normal text-[#111827]">Dashboard Tempo Real</h1>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex min-w-0 items-center gap-1 rounded-full border border-[#E5E7EB] bg-[#FFFFFF] px-2 py-0.5 font-body text-[0.6rem] text-[#6B7280]">
            <RefreshCw className="h-3 w-3 shrink-0" /> Atualização a cada {ATUALIZACAO_TXT}
          </span>
          <LiveClock shiftText={`${SHIFT_ORDINAL[currentShift]} · ${SHIFT_LABELS[currentShift]}`} updated={updated} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-body text-[0.6rem] uppercase tracking-wide text-[#6B7280]">Malharia</span>
          <input type="date" value={selectedDay} min={minDay} max={maxDay} onChange={(e) => pickDay(e.target.value)} aria-label="Dia de produção" className="min-w-0 flex-1 rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-2 py-1 font-body text-xs text-[#111827] outline-none focus:border-[#0C6FE0]" />
          <input type="month" value={selectedMonth} max={currentMonth} onChange={(e) => pickMonth(e.target.value)} aria-label="Mês de produção" className="min-w-0 flex-1 rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-2 py-1 font-body text-xs text-[#111827] outline-none focus:border-[#0C6FE0]" />
          {!isToday && <><button onClick={() => pickDay(prodDayKeyFast(Date.now()))} className="rounded-lg border border-[#E5E7EB] px-2 py-1 font-body text-[0.65rem] uppercase tracking-wide text-[#111827]">Hoje</button><span className="rounded-full border border-[#0C6FE0] px-2 py-0.5 font-body text-[0.6rem] uppercase tracking-wide text-[#0C6FE0]">Histórico</span></>}
        </div>
        <div className="flex items-center justify-center gap-2">
          <button onClick={prev} className="rounded-full border border-[#E5E7EB] p-1.5 text-[#6B7280]" aria-label="Slide anterior"><ChevronLeft className="h-4 w-4" /></button>
          <button onClick={() => setPaused((v) => !v)} className="rounded-full border border-[#E5E7EB] p-1.5 text-[#6B7280]" aria-label={paused ? 'Retomar' : 'Pausar'}>{paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}</button>
          <button onClick={next} className="rounded-full border border-[#E5E7EB] p-1.5 text-[#6B7280]" aria-label="Próximo slide"><ChevronRight className="h-4 w-4" /></button>
        </div>
      </div>
      <div className="hidden shrink-0 items-center justify-between gap-4 px-10 pt-5 xl:flex">
        <div className="flex min-w-0 items-center gap-4">
          <img src={conexaoLogo} alt="Conexão Malhas e Desenvolvimento" className="h-[34px] w-auto shrink-0 object-contain" />
          <div className="h-9 w-px shrink-0 bg-[#E5E7EB]" />
          <div className="min-w-0">
            <h1 className="font-heading text-base font-bold uppercase tracking-[0.18em] text-[#111827] md:text-lg xl:text-xl">Dashboard Tempo Real</h1>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-[#E5E7EB] bg-[#FFFFFF] px-2.5 py-0.5 font-body text-[0.65rem] text-[#6B7280]">
                <RefreshCw className="h-3 w-3" /> Atualização a cada {ATUALIZACAO_TXT}
              </span>
              <span className="font-body text-[0.65rem] uppercase tracking-[0.2em] text-[#6B7280]">Malharia</span>
              <input
                type="date"
                value={selectedDay}
                min={minDay}
                max={maxDay}
                onChange={(e) => pickDay(e.target.value)}
                aria-label="Dia de produção"
                className="rounded-xl border border-[#E5E7EB] bg-[#FFFFFF] px-2.5 py-1 font-body text-xs text-[#111827] outline-none focus:border-[#0C6FE0]"
              />
              <input
                type="month"
                value={selectedMonth}
                max={currentMonth}
                onChange={(e) => pickMonth(e.target.value)}
                aria-label="Mês de produção"
                className="rounded-xl border border-[#E5E7EB] bg-[#FFFFFF] px-2.5 py-1 font-body text-xs text-[#111827] outline-none focus:border-[#0C6FE0]"
              />
              {!isToday && (
                <>
                  <button
                    onClick={() => pickDay(prodDayKeyFast(Date.now()))}
                    className="rounded-xl border border-[#E5E7EB] px-2.5 py-1 font-body text-xs uppercase tracking-[0.16em] text-[#111827] transition hover:bg-[#F3F4F6]"
                  >
                    Hoje
                  </button>
                  <span className="rounded-full border border-[#0C6FE0] px-2.5 py-0.5 font-body text-[0.65rem] uppercase tracking-[0.2em] text-[#0C6FE0]">Histórico</span>
                </>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-6">
          <LiveClock shiftText={`${SHIFT_ORDINAL[currentShift]} · ${SHIFT_LABELS[currentShift]}`} updated={updated} />
          <div className="flex items-center gap-2">
            <button onClick={prev} className="rounded-full border border-[#E5E7EB] p-2 text-[#6B7280] transition hover:bg-[#F3F4F6]" aria-label="Slide anterior"><ChevronLeft className="h-5 w-5" /></button>
            <button onClick={() => setPaused((v) => !v)} className="rounded-full border border-[#E5E7EB] p-2 text-[#6B7280] transition hover:bg-[#F3F4F6]" aria-label={paused ? 'Retomar' : 'Pausar'}>
              {paused ? <Play className="h-5 w-5" /> : <Pause className="h-5 w-5" />}
            </button>
            <button onClick={next} className="rounded-full border border-[#E5E7EB] p-2 text-[#6B7280] transition hover:bg-[#F3F4F6]" aria-label="Próximo slide"><ChevronRight className="h-5 w-5" /></button>
            <button onClick={toggleFull} className="rounded-full border border-[#E5E7EB] p-2 text-[#6B7280] transition hover:bg-[#F3F4F6]" aria-label="Tela cheia">
              {isFull ? <Minimize className="h-5 w-5" /> : <Maximize className="h-5 w-5" />}
            </button>
          </div>
        </div>
      </div>

      <div className="relative flex flex-none overflow-y-visible px-4 py-4 md:px-10 md:py-7 min-[1600px]:min-h-0 min-[1600px]:flex-1 min-[1600px]:overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={slide}
            variants={slideVariants}
            initial="enter" animate="center" exit="exit"
            transition={{ duration: 0.45, ease: 'easeOut' }}
            className="h-auto min-h-0 w-full min-[1600px]:h-full"
          >
            {slide !== 3 && loadingDay ? (
              <Empty>Carregando dados da malharia…</Empty>
            ) : slide !== 3 && !dayHasRows ? (
              <Empty>Nenhuma pesagem registrada em {dayLabelFull(selectedDay)}</Empty>
            ) : slide === 3 && loadingHist && histRows.length === 0 ? (
              <Empty>Carregando dados de {monthLabel(selectedMonth)}…</Empty>
            ) : slide === 0 ? (
              <div className="grid h-auto grid-cols-1 gap-4 md:gap-6 lg:grid-cols-3 min-[1600px]:h-full min-[1600px]:min-h-0">
                <Card idx={0} className="flex flex-col justify-center gap-5 overflow-visible p-5 md:gap-8 md:p-8 lg:col-span-2 min-[1600px]:min-h-0 min-[1600px]:overflow-hidden">
                  <BigNumber label="Peças produzidas" value={nf0(agg.today.pecas)} />
                  <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 md:gap-8">
                    <BigNumber label="Metragem total do dia" value={nf0(agg.today.metros)} unit="m" size="text-4xl md:text-7xl" />
                    <BigNumber label="Quilos produzidos" value={nf0(agg.today.quilos)} unit="kg" size="text-4xl md:text-7xl" />
                  </div>
                  <div className="flex items-center gap-3">
                    <Label>{isToday ? 'Máquinas que produziram hoje' : 'Máquinas que produziram no dia'}</Label>
                    <span className="font-heading text-2xl font-bold text-[#0C6FE0]">{nf0(agg.todayMachines)}</span>
                  </div>
                </Card>
                <Card idx={1} className="flex flex-col items-center justify-center gap-5 overflow-visible p-5 md:gap-7 md:p-8 min-[1600px]:min-h-0 min-[1600px]:overflow-hidden">
                  <Label>Meta diária · {nf0(META_DIARIA_METROS)} m</Label>
                  <Ring pct={dayPct} size={isMobile ? 130 : 200} />
                  <div className="w-full space-y-3">
                    <Progress pct={dayPct} />
                    <div className="flex justify-between">
                      <div>
                        <Label>Falta</Label>
                        <div className="font-heading text-2xl font-bold text-[#111827]">{nf0(Math.max(0, META_DIARIA_METROS - agg.today.metros))} m</div>
                      </div>
                      <div className="text-right">
                        <Label>Meta por turno</Label>
                        <div className="font-heading text-2xl font-bold text-[#111827]">{nf0(META_TURNO_METROS)} m</div>
                      </div>
                    </div>
                  </div>
                </Card>
              </div>
            ) : slide === 1 ? (
              <div className="grid h-auto grid-cols-1 gap-4 md:gap-6 lg:grid-cols-3 min-[1600px]:h-full min-[1600px]:min-h-0">
                <Card idx={0} className="flex flex-col justify-center gap-5 overflow-visible p-5 md:gap-6 md:p-8 min-[1600px]:min-h-0 min-[1600px]:overflow-hidden">
                  <div>
                    <Label className="mb-2">{isToday ? 'Turno atual' : `Último turno do dia ${dayLabelFull(selectedDay)}`}</Label>
                    <div className="font-heading text-4xl font-bold uppercase tracking-tight text-[#111827] md:text-5xl">
                      {SHIFT_ORDINAL[agg.focusShift]} · {SHIFT_LABELS[agg.focusShift].toUpperCase()}
                    </div>
                    <Label className="mt-2">{SHIFT_RANGES[agg.focusShift]}</Label>
                  </div>
                  <BigNumber label="Peças do turno" value={nf0(shiftNow.pecas)} size="text-4xl md:text-7xl" />
                  <div className="grid grid-cols-2 gap-6">
                    <BigNumber label="Metragem do turno" value={nf0(shiftNow.metros)} unit="m" size="text-4xl md:text-5xl" />
                    <BigNumber label="Quilos" value={nf0(shiftNow.quilos)} unit="kg" size="text-4xl md:text-5xl" />
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label>Meta do turno · {nf0(META_TURNO_METROS)} m</Label>
                      <span className="font-heading text-xl font-bold text-[#0C6FE0]">{nf1(shiftPct)}%</span>
                    </div>
                    <Progress pct={shiftPct} />
                  </div>
                </Card>
                <Card idx={2} className="flex flex-col overflow-visible p-5 md:p-8 lg:col-span-2 min-[1600px]:min-h-0 min-[1600px]:overflow-hidden">
                  <div className="mb-5 flex shrink-0 items-center gap-2">
                    <Trophy className="h-5 w-5 text-[#0C6FE0]" />
                    <Label>{isToday ? 'Máquinas que mais produzem neste turno' : 'Máquinas que mais produziram neste turno'}</Label>
                  </div>
                  {agg.machineRanking.length === 0 ? (
                    <Empty>{isToday ? 'Aguardando a primeira peça do turno' : 'Sem peças registradas neste turno'}</Empty>
                  ) : (
                    <div className="grid grid-cols-2 gap-3 overflow-visible md:gap-4 xl:grid-cols-4 min-[1600px]:min-h-0 min-[1600px]:flex-1 min-[1600px]:overflow-hidden">
                      {agg.machineRanking.slice(0, 8).map((m, i) => (
                        <div
                          key={m.machine}
                          className={`dash-laminate ${i === 0 ? 'dash-laminate-primary bg-[#EFF6FF]' : ''} flex flex-col justify-center gap-1 overflow-visible rounded-2xl border p-3 md:p-4 min-[1600px]:min-h-0 min-[1600px]:overflow-hidden ${i === 0 ? 'border-[#0C6FE0]' : 'border-[#E5E7EB] bg-[#FFFFFF]'}`}
                        >
                          <span className="dash-laminate-sheen" style={{ animationDelay: `${(i % 6) * 0.9}s` }} aria-hidden />
                          <Label>{i === 0 ? '1º lugar · Máquina' : `${i + 1}º · Máquina`}</Label>
                          <div className={`font-heading text-3xl font-bold leading-none md:text-4xl ${i === 0 ? 'text-[#0C6FE0]' : 'text-[#111827]'}`}>{m.machine}</div>
                          <div className="font-heading text-lg font-bold text-[#111827]">{nf0(m.metros)} m</div>
                          <div className="font-body text-sm text-[#6B7280]">{nf0(m.pecas)} peças</div>
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
              </div>
            ) : slide === 2 ? (
              <div className="flex h-auto flex-col gap-4 md:gap-6 min-[1600px]:h-full min-[1600px]:min-h-0">
                <div className="grid grid-cols-1 gap-4 md:grid-cols-3 md:gap-6 min-[1600px]:min-h-0 min-[1600px]:flex-1">
                  {SHIFT_SEQUENCE.map((sh) => {
                    const t = agg.shiftTotals[sh];
                    const ef = (t.metros / META_TURNO_METROS) * 100;
                    const best = bestShiftOfDay === sh;
                    return (
                      <Card key={sh} primary={best} idx={SHIFT_SEQUENCE.indexOf(sh)} className={`flex flex-col justify-center gap-3 overflow-visible p-5 md:p-6 min-[1600px]:min-h-0 min-[1600px]:overflow-hidden ${best ? 'bg-[#EFF6FF]' : ''}`}>
                        <div>
                          <div className="font-heading text-xl font-bold uppercase tracking-tight text-[#111827] xl:text-2xl">{SHIFT_ORDINAL[sh]} · {SHIFT_LABELS[sh]}</div>
                          <Label className="mt-1">{SHIFT_RANGES[sh]}</Label>
                        </div>
                        <div className="font-heading text-3xl font-bold leading-none text-[#111827] md:text-4xl xl:text-5xl">{nf0(t.pecas)}<span className="ml-2 font-body text-base text-[#6B7280]">peças</span></div>
                        <div className="flex gap-6">
                          <div><Label>Metros</Label><div className="font-heading text-xl font-bold text-[#111827]">{nf0(t.metros)}</div></div>
                          <div><Label>Quilos</Label><div className="font-heading text-xl font-bold text-[#111827]">{nf0(t.quilos)}</div></div>
                        </div>
                        <div className="space-y-2">
                          <div className="flex items-center justify-between">
                            <Label>Meta {nf0(META_TURNO_METROS)} m</Label>
                            <span className={`font-heading text-lg font-bold ${best ? 'text-[#0C6FE0]' : 'text-[#111827]'}`}>{nf1(ef)}%</span>
                          </div>
                          <Progress pct={ef} />
                        </div>
                        {best && <div className="flex items-center gap-2 text-[#0C6FE0]"><Trophy className="h-4 w-4" /><Label className="text-[#0C6FE0]">Melhor eficiência do dia</Label></div>}
                      </Card>
                    );
                  })}
                </div>
                <Card idx={3} className="shrink-0 p-5">
                  <div className="mb-3 flex items-center gap-2"><Target className="h-4 w-4 text-[#0C6FE0]" /><Label>Turnos com melhor eficiência · {monthLabel(selectedMonth)}</Label></div>
                  {hist.bestShifts.length === 0 ? (
                    <div className="font-body py-3 text-center text-[#6B7280]">Sem turnos com produção em {monthLabel(selectedMonth)}</div>
                  ) : (
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
                      {hist.bestShifts.map((b, i) => (
                        <div key={`${b.day}-${b.shift}`} className={`dash-laminate ${i === 0 ? 'dash-laminate-primary bg-[#EFF6FF]' : ''} rounded-2xl border p-3 ${i === 0 ? 'border-[#0C6FE0]' : 'border-[#E5E7EB]'}`}>
                          <span className="dash-laminate-sheen" style={{ animationDelay: `${(i % 6) * 0.9}s` }} aria-hidden />
                          <Label>{dayLabelFull(b.day)}</Label>
                          <div className="font-heading text-base font-bold text-[#111827]">{SHIFT_ORDINAL[b.shift]} · {SHIFT_LABELS[b.shift]}</div>
                          <div className="font-body text-sm text-[#6B7280]">{nf0(b.metros)} m</div>
                          <div className={`font-heading text-lg font-bold ${i === 0 ? 'text-[#0C6FE0]' : 'text-[#111827]'}`}>{nf1(b.ef)}%</div>
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
              </div>
            ) : (
              <div className="grid h-auto grid-cols-1 gap-4 md:gap-6 lg:grid-cols-2 min-[1600px]:h-full min-[1600px]:min-h-0">
                <Card idx={0} className="flex flex-col overflow-visible p-5 md:p-7 min-[1600px]:min-h-0 min-[1600px]:overflow-hidden">
                  <Label className="mb-4 shrink-0">Dias com mais peças · {monthLabel(selectedMonth)}</Label>
                  {hist.topPecasDays.length === 0 ? (
                    <Empty>Sem produção em {monthLabel(selectedMonth)}</Empty>
                  ) : (
                    <div className="flex flex-col gap-2 min-[1600px]:min-h-0 min-[1600px]:flex-1">
                      {hist.topPecasDays.map((d, i) => (
                        <div key={d.day} className={`dash-laminate ${i === 0 ? 'dash-laminate-primary bg-[#EFF6FF]' : ''} flex items-center justify-between rounded-2xl border px-3 py-2 md:px-5 min-[1600px]:min-h-0 min-[1600px]:flex-1 ${i === 0 ? 'border-[#0C6FE0]' : 'border-[#E5E7EB]'}`}>
                          <span className="dash-laminate-sheen" style={{ animationDelay: `${(i % 6) * 0.9}s` }} aria-hidden />
                          <div className="flex items-center gap-4">
                            <span className="font-heading text-sm text-[#6B7280]">{i + 1}º</span>
                            <span className="font-heading text-base font-bold text-[#111827] xl:text-lg">{dayLabelFull(d.day)}</span>
                          </div>
                          <div className="flex items-center gap-3 md:gap-8">
                            <div className="text-right"><Label>Peças</Label><div className={`font-heading text-lg font-bold xl:text-xl ${i === 0 ? 'text-[#0C6FE0]' : 'text-[#111827]'}`}>{nf0(d.pecas)}</div></div>
                            <div className="text-right"><Label>Metros</Label><div className="font-heading text-lg font-bold text-[#111827] xl:text-xl">{nf0(d.metros)}</div></div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
                <div className="flex flex-col gap-4 md:gap-6 min-[1600px]:min-h-0">
                  <Card idx={2} className="flex flex-col overflow-visible p-5 md:p-7 min-[1600px]:min-h-0 min-[1600px]:flex-1 min-[1600px]:overflow-hidden">
                    <div className="mb-3 flex shrink-0 items-center justify-between">
                      <Label>Dias acima da meta · {monthLabel(selectedMonth)} · {nf0(META_DIARIA_METROS)} m</Label>
                      <span className="font-heading text-lg font-bold text-[#0C6FE0]">{nf0(hist.aboveGoal.length)} de {nf0(hist.days.length)} dias</span>
                    </div>
                    {hist.aboveGoal.length === 0 ? (
                      <div className="font-body py-6 text-center text-[#6B7280]">Nenhum dia acima da meta em {monthLabel(selectedMonth)}</div>
                    ) : (
                      <div
                        className="space-y-2 overflow-visible md:pr-1 min-[1600px]:min-h-0 min-[1600px]:flex-1 min-[1600px]:overflow-y-auto"
                        style={{ scrollbarWidth: 'thin', scrollbarColor: '#D1D5DB transparent' }}
                      >
                        {hist.aboveGoal.map((d) => (
                          <div key={d.day} className="dash-laminate dash-laminate-primary flex items-center justify-between rounded-xl border px-4 py-2">
                            <span className="dash-laminate-sheen" aria-hidden />
                            <span className="font-heading text-base font-bold text-[#111827]">{dayLabelFull(d.day)}</span>
                            <div className="flex items-center gap-3 md:gap-6">
                              <span className="font-body text-sm text-[#6B7280]">{nf0(d.metros)} m</span>
                              <span className="font-heading text-base font-bold text-[#0C6FE0]">+{nf0(d.metros - META_DIARIA_METROS)} m</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </Card>
                  <Card idx={4} className="flex flex-col overflow-hidden p-5 md:p-6 min-[1600px]:min-h-0 min-[1600px]:flex-1">
                    <Label className="mb-2 shrink-0">Dias de {monthLabel(selectedMonth)} · metros x meta</Label>
                    {hist.chartDays.length === 0 ? (
                      <div className="font-body py-6 text-center text-[#6B7280]">Sem dados</div>
                    ) : (
                      <div className="h-[200px] min-[1600px]:min-h-0 min-[1600px]:flex-1">
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart data={visibleChartDays} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" vertical={false} />
                            <XAxis dataKey="name" tick={{ fontSize: isMobile ? 8 : hist.chartDays.length > 20 ? 9 : 11, fill: '#6B7280' }} interval={0} stroke="#E5E7EB" />
                            <YAxis tick={{ fontSize: 11, fill: '#6B7280' }} stroke="#E5E7EB" tickFormatter={(v) => nf0(v)} />
                            <Tooltip
                              formatter={(v) => [`${nf0(v)} m`, 'Metros']}
                              contentStyle={{ backgroundColor: '#FFFFFF', border: '1px solid #E5E7EB', borderRadius: 8 }}
                              labelStyle={{ color: '#111827' }}
                              itemStyle={{ color: '#111827' }}
                            />
                            <ReferenceLine y={META_DIARIA_METROS} stroke="#0C6FE0" strokeDasharray="4 4" />
                            <Bar dataKey="metros" radius={[6, 6, 0, 0]} fill="#0C6FE0" barSize={isMobile ? 7 : hist.chartDays.length > 24 ? 8 : hist.chartDays.length > 16 ? 12 : 18} />
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    )}
                  </Card>
                </div>
              </div>
            )}
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="flex shrink-0 items-center justify-center gap-3 pb-5">
        {Array.from({ length: SLIDES }).map((_, i) => (
          <button
            key={i}
            onClick={() => setSlide(i)}
            aria-label={`Ir para slide ${i + 1}`}
            className={`h-2.5 rounded-full transition-all ${i === slide ? 'w-8 bg-[#0C6FE0]' : 'w-2.5 bg-[#D1D5DB] hover:bg-[#9CA3AF]'}`}
          />
        ))}
      </div>
    </div>
  );
};

export default DashboardTempoReal;
