import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { format } from 'date-fns';
import { ptBR } from 'date-fns/locale';
import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import { FileSpreadsheet, Activity, TrendingUp, Layers, AlertTriangle, Download, Grid3x3, ListOrdered, Clock, Database } from 'lucide-react';
import {
  Card, CardHeader, CardTitle, CardContent, Button, Input,
  Dialog, DialogContent, DialogHeader, DialogTitle,
  Popover, PopoverTrigger, PopoverContent, useToast,
} from '../../components/ui.jsx';
import {
  parseISODateOnly, getLocalDateKey, addDays, parseTimeToMinutes,
  buildLocalDateTime, isInShiftWindow, getDefaultShiftAnchor,
} from './dateUtils.js';

const ROOT = document.getElementById('gestao-malharia-root');
const URLS = {
  movimentos: ROOT?.dataset.movimentosUrl,
  sequencia: ROOT?.dataset.sequenciaUrl,
  status: ROOT?.dataset.statusUrl,
};

const todayISO = (() => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
})();

const KPI = [
  { key: 'pecas', label: 'Qt. Peças', icon: FileSpreadsheet, colors: ['#0f9d63', '#1caf74', '#0f9d63'], lowColors: ['#b91c1c', '#e5484d', '#b91c1c'] },
  { key: 'movimento', label: 'Qt. Movimento Total (MT)', icon: Activity, colors: ['#0b4d9e', '#2f7fe0', '#0b4d9e'] },
  { key: 'pesos', label: 'Qt. Pesos Total', icon: TrendingUp, colors: ['#0f9d63', '#1caf74', '#0f9d63'] },
  { key: 'maquinas', label: 'Máquinas Ativas', icon: Layers, colors: ['#b8860b', '#d4a017', '#b8860b'] },
];

function KpiCard({ label, icon: Icon, value, colors }) {
  const [a, b, c] = colors;
  return (
    <div className="relative overflow-hidden rounded-xl shadow-lg p-4" style={{ background: `linear-gradient(90deg, ${a}, ${b}, ${c})` }}>
      <div className="flex items-center gap-3">
        <Icon className="w-8 h-8 text-white drop-shadow-md" />
        <div>
          <p className="text-xs text-white/90 font-semibold uppercase tracking-wider">{label}</p>
          <p className="text-2xl font-heading font-bold text-white">{value}</p>
        </div>
      </div>
    </div>
  );
}

const MAP_ALL_GROUPS = [
  { name: 'GRUPO A', machines: ['11', '16', '47', '25', '23', '49', '17', '50', '46', '07', '10', '43'] },
  { name: 'GRUPO B', machines: ['04', '08', '02', '03', '05', '01', '51', '06', '53', '52', '09', '12', '22', '48', '40', '41', '45', '44'] },
];
const MAP_SHIFTS = {
  MANHA: { label: '1º TURNO (06:20–14:19)', from: 6 * 60 + 20, to: 14 * 60 + 19 },
  TARDE: { label: '2º TURNO (14:20–22:19)', from: 14 * 60 + 20, to: 22 * 60 + 19 },
  NOITE: { label: '3º TURNO (22:20–06:19)', from: 22 * 60 + 20, to: 6 * 60 + 19 },
};

export default function Malharia() {
  const { toast } = useToast();
  const [dateFrom, setDateFrom] = useState(todayISO);
  const [dateTo, setDateTo] = useState(todayISO);
  const [timeFrom, setTimeFrom] = useState('');
  const [timeTo, setTimeTo] = useState('');
  const [machineFilter, setMachineFilter] = useState('');
  const [shiftFilter, setShiftFilter] = useState('');
  const [clientFilter, setClientFilter] = useState('');
  const [selectedMachine, setSelectedMachine] = useState(null);
  const autoSelectedLatestDate = useRef(false);

  const { data: statusData } = useQuery({
    queryKey: ['malharia_status'],
    queryFn: () => fetch(URLS.status).then((r) => r.json()),
    refetchInterval: 60_000,
  });

  const { data: queryResult } = useQuery({
    queryKey: ['malharia_movimentos'],
    queryFn: async () => {
      const data = await fetch(URLS.movimentos).then((r) => r.json());
      const seen = new Map();
      const out = [];
      const dups = [];
      for (const r of [...data].sort((a, b) => new Date(b.dt_real) - new Date(a.dt_real))) {
        const key = [r.cod_maquina, r.dt_real, r.nr_item, r.cod_artigo, r.qt_movimento, r.qt_pesos, r.op, r.local_code].map((v) => v ?? '').join('|');
        if (seen.has(key)) { dups.push(r); continue; }
        seen.set(key, r);
        out.push(r);
      }
      return { rows: out, duplicates: dups };
    },
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const rows = queryResult?.rows ?? [];
  const duplicateRows = queryResult?.duplicates ?? [];

  useEffect(() => {
    if (autoSelectedLatestDate.current || rows.length === 0) return;
    const latest = rows.map((r) => (r.dt_real ? new Date(r.dt_real) : null)).filter((d) => d && !isNaN(d.getTime())).sort((a, b) => b - a)[0];
    if (latest) {
      const key = getLocalDateKey(latest);
      setDateFrom(key);
      setDateTo(key);
      autoSelectedLatestDate.current = true;
    }
  }, [rows]);

  const hasActiveFilter = !!(dateFrom || dateTo || timeFrom || timeTo || machineFilter || shiftFilter || clientFilter);

  const matchesDateTime = (raw) => {
    if (!raw) return false;
    const dt = new Date(raw);
    if (isNaN(dt.getTime())) return false;
    const fromMin = timeFrom ? parseTimeToMinutes(timeFrom) : null;
    const toMin = timeTo ? parseTimeToMinutes(timeTo) : null;
    const currentMin = dt.getHours() * 60 + dt.getMinutes();
    if (dateFrom || dateTo) {
      const start = dateFrom ? buildLocalDateTime(dateFrom, timeFrom || null, 'start') : (dateTo ? buildLocalDateTime(dateTo, null, 'start') : null);
      let end = null;
      if (dateTo) {
        end = buildLocalDateTime(dateTo, timeTo || null, 'end');
      } else if (dateFrom) {
        const baseEndDate = parseISODateOnly(dateFrom);
        if (baseEndDate) {
          const shouldAdvanceDay = fromMin !== null && toMin !== null && fromMin > toMin;
          const endDate = shouldAdvanceDay ? addDays(baseEndDate, 1) : baseEndDate;
          end = buildLocalDateTime(getLocalDateKey(endDate), timeTo || null, 'end');
        }
      }
      if (start && dt < start) return false;
      if (end && dt > end) return false;
      return true;
    }
    if (fromMin !== null && toMin !== null) {
      if (fromMin > toMin) return currentMin >= fromMin || currentMin <= toMin;
      return currentMin >= fromMin && currentMin <= toMin;
    }
    if (fromMin !== null && currentMin < fromMin) return false;
    if (toMin !== null && currentMin > toMin) return false;
    return true;
  };

  const filteredRows = useMemo(() => {
    if (!hasActiveFilter) return [];
    let r = rows;
    if (shiftFilter) {
      const anchor = getDefaultShiftAnchor(shiftFilter, dateFrom);
      r = r.filter((row) => row.dt_real && isInShiftWindow(new Date(row.dt_real), shiftFilter, anchor));
    } else if (dateFrom || dateTo || timeFrom || timeTo) {
      r = r.filter((row) => matchesDateTime(row.dt_real));
    }
    if (machineFilter) r = r.filter((row) => row.cod_maquina === machineFilter);
    if (clientFilter) r = r.filter((row) => row.cliente === clientFilter);
    r = r.filter((row) => !row.cod_artigo || !/amostra/i.test(row.cod_artigo));
    return r;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, dateFrom, dateTo, timeFrom, timeTo, machineFilter, shiftFilter, clientFilter, hasActiveFilter]);

  const totalMovimento = filteredRows.reduce((s, r) => s + (Number(r.metro_padrao) || 0), 0);
  const totalPesos = filteredRows.reduce((s, r) => s + (Number(r.qt_pesos) || 0), 0);
  const uniqueMachines = [...new Set(filteredRows.map((r) => r.cod_maquina).filter(Boolean))];

  const machineDetail = useMemo(() => {
    if (!selectedMachine) return [];
    return filteredRows.filter((r) => r.cod_maquina === selectedMachine).map((r) => ({
      op: r.op || 'S/OP', artigo: r.cod_artigo || '', nrItem: r.nr_item || '-',
      qtMovimento: Number(r.qt_movimento) || 0, qtPesos: Number(r.qt_pesos) || 0,
      dtReal: r.dt_real ? new Date(r.dt_real) : null, localCode: r.local_code || '',
    })).sort((a, b) => (a.dtReal?.getTime() ?? Infinity) - (b.dtReal?.getTime() ?? Infinity));
  }, [filteredRows, selectedMachine]);

  const allMachines = useMemo(() => [...new Set(rows.map((r) => r.cod_maquina).filter(Boolean))].sort((a, b) => Number(a) - Number(b)), [rows]);
  const allClients = useMemo(() => {
    const set = new Set();
    for (const r of rows) {
      if (!r.cod_artigo || /amostra/i.test(r.cod_artigo) || !r.cliente) continue;
      set.add(r.cliente);
    }
    return [...set].sort((a, b) => {
      const pr = (n) => (n === 'ORTOBOM' ? 0 : n === 'ECOFLEX' ? 1 : 2);
      return pr(a) - pr(b) || a.localeCompare(b);
    });
  }, [rows]);

  const byMachine = useMemo(() => {
    const map = {};
    allMachines.forEach((m) => { map[m] = 0; });
    filteredRows.forEach((r) => {
      const m = r.cod_maquina || 'N/A';
      map[m] = (map[m] || 0) + (Number(r.qt_movimento) || 0);
    });
    return Object.entries(map).sort((a, b) => b[1] - a[1]);
  }, [filteredRows, allMachines]);

  const bottom5Articles = useMemo(() => {
    if (filteredRows.length === 0) return [];
    const refMs = Math.max(...filteredRows.map((r) => (r.dt_real ? new Date(r.dt_real).getTime() : 0)));
    if (!refMs) return [];
    const byMach = {};
    filteredRows.forEach((r) => {
      if (!r.dt_real || !r.cod_maquina) return;
      (byMach[r.cod_maquina] ??= []).push({ dt: new Date(r.dt_real), op: r.op || 'S/OP', artigo: r.cod_artigo || 'N/A' });
    });
    const results = [];
    Object.entries(byMach).forEach(([maquina, entries]) => {
      entries.sort((a, b) => a.dt - b.dt);
      if (entries.length === 1) {
        const only = entries[0];
        results.push({
          op: only.op, maquina, artigo: only.artigo, horasSemProd: 0, ultimaProd: only.dt, retomou: null,
          comentario: `Atenção, Líder. A máquina ${maquina} apresenta apenas um único registro de pesagem no período analisado, em ${format(only.dt, 'dd/MM/yyyy', { locale: ptBR })} às ${format(only.dt, 'HH:mm', { locale: ptBR })}. Solicitamos que verifique o equipamento ou reporte ao PCP o motivo da parada de produção.`,
        });
        return;
      }
      let biggest = null;
      for (let i = 1; i < entries.length; i++) {
        const diffH = (entries[i].dt - entries[i - 1].dt) / 3.6e6;
        if (diffH >= 3 && (!biggest || diffH > biggest.hours)) biggest = { prev: entries[i - 1], next: entries[i], hours: diffH };
      }
      const last = entries[entries.length - 1];
      const diffRefH = (refMs - last.dt.getTime()) / 3.6e6;
      if (diffRefH >= 3 && (!biggest || diffRefH > biggest.hours)) biggest = { prev: last, next: null, hours: diffRefH };
      if (biggest) {
        const dataFmt = format(biggest.prev.dt, 'dd/MM/yyyy', { locale: ptBR });
        const horaUltima = format(biggest.prev.dt, 'HH:mm', { locale: ptBR });
        const horaRetomou = biggest.next ? format(biggest.next.dt, 'HH:mm', { locale: ptBR }) : null;
        const horasInt = Math.floor(biggest.hours);
        results.push({
          op: biggest.prev.op, maquina, artigo: biggest.prev.artigo, horasSemProd: biggest.hours,
          ultimaProd: biggest.prev.dt, retomou: biggest.next ? biggest.next.dt : null,
          comentario: horaRetomou
            ? `Atenção, Líder. A máquina ${maquina} permaneceu ${horasInt}h sem registrar pesagem no dia ${dataFmt}. Última peça pesada às ${horaUltima} e retomada da produção às ${horaRetomou}. Solicitamos a verificação da causa da parada.`
            : `Atenção, Líder. A máquina ${maquina} encontra-se sem registrar pesagem há ${horasInt}h. Última peça pesada em ${dataFmt} às ${horaUltima}. Solicitamos verificação imediata.`,
        });
      }
    });
    return results.sort((a, b) => b.horasSemProd - a.horasSemProd).slice(0, 5);
  }, [filteredRows]);

  const qtPecas = filteredRows.length;
  const isLow = qtPecas < 100;

  const generatePdf = () => {
    const doc = new jsPDF();
    const pageWidth = doc.internal.pageSize.getWidth();
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(14);
    doc.text('Dashboard Malharia — Gestão Conexão', 14, 16);
    doc.setFontSize(9);
    doc.setTextColor(100);
    doc.text(`Gerado em ${format(new Date(), 'dd/MM/yyyy HH:mm', { locale: ptBR })}`, 14, 22);
    let y = 30;
    autoTable(doc, {
      startY: y,
      head: [['Qt. Peças', 'Qt. Movimento (MT)', 'Qt. Pesos', 'Máquinas Ativas']],
      body: [[qtPecas, totalMovimento.toLocaleString('pt-BR', { maximumFractionDigits: 2 }), totalPesos.toLocaleString('pt-BR', { maximumFractionDigits: 2 }), uniqueMachines.length]],
      theme: 'grid', headStyles: { fillColor: [11, 77, 158] },
    });
    y = doc.lastAutoTable.finalY + 8;
    if (byMachine.length > 0) {
      doc.setFontSize(10);
      doc.text('Movimento por Máquina', 14, y);
      y += 4;
      autoTable(doc, {
        startY: y,
        head: [['Máquina', 'Movimento (Qt. Movimento)']],
        body: byMachine.map(([m, v]) => [m, Number(v).toLocaleString('pt-BR', { maximumFractionDigits: 2 })]),
        theme: 'striped', headStyles: { fillColor: [30, 41, 59] }, bodyStyles: { fontSize: 8 },
      });
      y = doc.lastAutoTable.finalY + 8;
    }
    if (bottom5Articles.length > 0) {
      if (y > 220) { doc.addPage(); y = 20; }
      doc.setFontSize(10);
      doc.text('Tempo Ocioso de Máquina > 3h', 14, y);
      y += 4;
      autoTable(doc, {
        startY: y,
        head: [['Máquina', 'OP', 'Artigo', 'Última Produção', 'Tempo Sem Produzir']],
        body: bottom5Articles.map((i) => [i.maquina, i.op, i.artigo, format(i.ultimaProd, 'dd/MM HH:mm', { locale: ptBR }), `${Math.floor(i.horasSemProd)}h`]),
        theme: 'striped', headStyles: { fillColor: [185, 28, 28] }, bodyStyles: { fontSize: 8 },
      });
    }
    const pageCount = doc.internal.getNumberOfPages();
    for (let i = 1; i <= pageCount; i++) {
      doc.setPage(i);
      doc.setFontSize(8);
      doc.setTextColor(100);
      doc.text(`Página ${i} de ${pageCount}`, pageWidth - 14, doc.internal.pageSize.getHeight() - 6, { align: 'right' });
      doc.text('Gestão Conexão • Relatório Malharia', 14, doc.internal.pageSize.getHeight() - 6);
    }
    doc.save(`dashboard-malharia-${format(new Date(), 'yyyy-MM-dd')}.pdf`);
    toast({ title: 'PDF gerado', description: 'O relatório foi baixado.' });
  };

  // ---- Mapeamento de Produção ----
  const [mapDialogOpen, setMapDialogOpen] = useState(false);
  const [mapWhich, setMapWhich] = useState('ALL');
  const [mapShift, setMapShift] = useState('ALL');
  const [mapDate, setMapDate] = useState(todayISO);
  const [mapResult, setMapResult] = useState(null);

  const num = (v) => { const m = String(v ?? '').match(/(\d+)/); return m ? String(parseInt(m[1], 10)) : String(v ?? '').trim().toUpperCase(); };

  const generateMapeamento = () => {
    const GROUPS = mapWhich === 'ALL' ? MAP_ALL_GROUPS : MAP_ALL_GROUPS.filter((g) => g.name === `GRUPO ${mapWhich}`);
    const startIso = new Date(`${mapDate}T09:00:00.000Z`);
    const endIso = new Date(startIso.getTime() + 24 * 60 * 60 * 1000);
    const brMinutes = (d) => {
      const s = d.toLocaleTimeString('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit', hour12: false });
      const [h, m] = s.split(':').map(Number);
      return h * 60 + m;
    };
    const matchShift = (d) => {
      if (mapShift === 'ALL') return true;
      const { from, to } = MAP_SHIFTS[mapShift];
      const mm = brMinutes(d);
      return from <= to ? mm >= from && mm <= to : mm >= from || mm <= to;
    };
    const inWindow = rows.filter((r) => {
      if (!r.dt_real) return false;
      const d = new Date(r.dt_real);
      if (d < startIso || d >= endIso) return false;
      if (r.cod_artigo && /amostra/i.test(r.cod_artigo)) return false;
      return matchShift(d);
    });
    const byMach = new Map();
    inWindow.forEach((r) => {
      const k = num(r.cod_maquina);
      if (!k) return;
      if (!byMach.has(k)) byMach.set(k, []);
      byMach.get(k).push(r);
    });
    const hora = (d) => (d ? new Date(d).toLocaleTimeString('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit' }) : '—');
    let totMet = 0, totPcs = 0, totKg = 0;
    const groups = GROUPS.map((g) => ({
      name: g.name,
      machines: g.machines.map((mRaw) => {
        const key = num(mRaw);
        const list = (byMach.get(key) ?? []).sort((a, b) => new Date(a.dt_real) - new Date(b.dt_real));
        const met = list.reduce((s, r) => s + (Number(r.metro_padrao) || 0), 0);
        const kg = list.reduce((s, r) => s + (Number(r.qt_pesos) || 0), 0);
        totMet += met; totPcs += list.length; totKg += kg;
        const status = list.length === 0 ? 'SEM PRODUÇÃO NO PERÍODO' : 'PRODUZINDO';
        const cls = list.length === 0 ? 'idle' : 'run';
        return {
          machine: mRaw, status, cls, metros: met, kg,
          pieces: list.map((r) => ({ op: String(r.op ?? '—'), artigo: String(r.cod_artigo ?? '—'), item: String(r.nr_item ?? '—'), metros: Number(r.metro_padrao) || 0, qtMov: Number(r.qt_movimento) || 0, kg: Number(r.qt_pesos) || 0, hora: hora(r.dt_real) })),
        };
      }),
    }));
    setMapResult({ groups, totPcs, totMet, totKg, startIso, endIso, which: mapWhich, shift: mapShift, date: mapDate });
  };

  const printMapeamento = () => {
    if (!mapResult) return;
    const esc = (v) => String(v ?? '—').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
    const n2 = (v) => v.toLocaleString('pt-BR', { maximumFractionDigits: 1 });
    const shiftLabel = mapResult.shift === 'ALL' ? 'TODOS OS TURNOS' : MAP_SHIFTS[mapResult.shift].label;
    const groupsHtml = mapResult.groups.map((g) => {
      const machinesHtml = g.machines.map((m) => {
        const trs = m.pieces.map((p, i) => `<tr><td class="c">${i + 1}</td><td class="c">${esc(p.op)}</td><td class="c">${esc(p.artigo)}</td><td class="c">${esc(p.item)}</td><td class="c">${n2(p.metros)}</td><td class="c">${n2(p.qtMov)}</td><td class="c">${n2(p.kg)}</td><td class="c">${esc(p.hora)}</td></tr>`).join('');
        return `<div class="mblock"><div class="mh"><span>MÁQUINA ${esc(m.machine)}</span><span class="badge st-${m.cls}">${esc(m.status)}</span></div><div class="msum">Peças: <b>${m.pieces.length}</b> · Metros: <b>${n2(m.metros)}</b> · Quilos: <b>${n2(m.kg)}</b></div>${m.pieces.length ? `<table><thead><tr><th>Nº</th><th>OP</th><th>ARTIGO</th><th>ITEM</th><th>METROS</th><th>QT.MOVIM.</th><th>KG</th><th>HORA</th></tr></thead><tbody>${trs}</tbody></table>` : '<div class="empty">Nenhuma peça pesada neste período.</div>'}</div>`;
      }).join('');
      return `<div class="group"><div class="gh">${g.name} — ${g.machines.length} máquinas</div>${machinesHtml}</div>`;
    }).join('');
    const fmtDT = (d) => d.toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    const html = `<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8" /><title>Mapeamento de Produção</title><style>
      @page { size: A4 portrait; margin: 8mm; } * { box-sizing: border-box; }
      body { font-family: Arial, Helvetica, sans-serif; margin: 0; color: #111; font-size: 9px; }
      h1 { font-size: 14px; margin: 0 0 2mm; text-align: center; background: #0b4d9e; color: #fff; padding: 2mm; }
      .sub { text-align: center; font-size: 9px; color: #444; margin-bottom: 3mm; }
      .tot { text-align:center; font-size:10px; margin-bottom:3mm; padding:1.5mm; background:#F1F5F9; border:0.5px solid #cbd5e1; }
      .group { margin-bottom: 4mm; } .gh { background: #0f9d63; color: #fff; font-weight: bold; font-size: 11px; padding: 1.5mm 2mm; margin-bottom: 1.5mm; }
      .mblock { break-inside: avoid; page-break-inside: avoid; margin-bottom: 2.5mm; border: 0.5px solid #cbd5e1; }
      .mh { display: flex; justify-content: space-between; align-items: center; background: #1E3A5F; color: #fff; font-weight: bold; padding: 1mm 2mm; font-size: 10px; }
      .badge { font-size: 8px; padding: 0.5mm 1.5mm; border-radius: 2px; } .st-run { background:#16A34A; color:#fff; } .st-idle { background:#94A3B8; color:#111; }
      .msum { padding: 1mm 2mm; background:#F8FAFC; font-size: 9px; } table { width: 100%; border-collapse: collapse; }
      th { background: #E2E8F0; font-size: 8px; padding: 0.6mm; border: 0.4px solid #b6bfcc; } td { padding: 0.6mm; border: 0.4px solid #d5dae2; font-size: 8px; }
      tbody tr:nth-child(even) td { background: #F7F9FB; } .c { text-align: center; } .empty { padding: 1.5mm 2mm; font-size: 8.5px; color: #64748B; }
      </style></head><body>
      <h1>MAPEAMENTO DE PRODUÇÃO — GRUPOS DE MÁQUINAS · ${esc(shiftLabel)}</h1>
      <div class="sub">Ciclo 06h → 06h · ${fmtDT(mapResult.startIso)} até ${fmtDT(mapResult.endIso)}</div>
      <div class="tot">TOTAL GERAL — Peças: <b>${mapResult.totPcs}</b> · Metros: <b>${n2(mapResult.totMet)}</b> · Quilos: <b>${n2(mapResult.totKg)}</b></div>
      ${groupsHtml}
      <script>window.onload = () => { window.focus(); window.print(); };<\/script></body></html>`;
    const w = window.open('', '_blank');
    if (!w) { toast({ title: 'Bloqueado pelo navegador', description: 'Permita pop-ups para gerar o PDF.', variant: 'destructive' }); return; }
    w.document.write(html);
    w.document.close();
  };

  // ---- Mapeamento de Sequência ----
  // Reordena por maquina (PRODUZINDO primeiro, depois EM_SEQUENCIA) e numera
  // a posicao real da fila (1º = rodando agora), em vez do sequence_order
  // bruto do banco (que so cresce e fica com "buracos" conforme OPs terminam).
  const withRank = (list) => {
    const byMach = new Map();
    list.forEach((r) => { (byMach.get(r.machine) ?? byMach.set(r.machine, []).get(r.machine)).push(r); });
    const out = [];
    byMach.forEach((items) => {
      items.sort((a, b) => (a.sequence_order ?? 0) - (b.sequence_order ?? 0));
      items.forEach((r, i) => out.push({ ...r, posicao: i + 1 }));
    });
    return out;
  };

  const [seqDialogOpen, setSeqDialogOpen] = useState(false);
  const [seqMachine, setSeqMachine] = useState('');
  const [seqAll, setSeqAll] = useState([]);
  const [seqMachines, setSeqMachines] = useState([]);
  const [seqLoading, setSeqLoading] = useState(false);

  const loadSeqData = async () => {
    if (seqAll.length || seqLoading) return;
    setSeqLoading(true);
    try {
      const data = await fetch(URLS.sequencia).then((r) => r.json());
      setSeqAll(data);
      setSeqMachines([...new Set(data.map((r) => String(r.machine)))].sort((a, b) => (parseInt(a) || 0) - (parseInt(b) || 0)));
    } finally {
      setSeqLoading(false);
    }
  };

  const printMapeamentoSequencia = () => {
    const list = withRank(seqMachine ? seqAll.filter((r) => String(r.machine) === seqMachine) : seqAll);
    const esc = (v) => String(v ?? '—').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
    const byMach = new Map();
    list.forEach((r) => { (byMach.get(r.machine) ?? byMach.set(r.machine, []).get(r.machine)).push(r); });
    const rowsHtml = [...byMach.entries()].sort((a, b) => (parseInt(a[0]) || 0) - (parseInt(b[0]) || 0)).map(([m, items]) => {
      const trs = items.sort((a, b) => a.posicao - b.posicao).map((it) => `<tr><td class="c">${esc(it.posicao)}º</td><td class="c">${esc(it.op)}</td><td>${esc(it.article)}</td><td>${esc(it.client)}</td></tr>`).join('');
      return `<div class="mblock"><div class="mh">MÁQUINA ${esc(m)}</div><table><thead><tr><th>POSIÇÃO</th><th>OP</th><th>ARTIGO</th><th>CLIENTE</th></tr></thead><tbody>${trs}</tbody></table></div>`;
    }).join('');
    const html = `<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8" /><title>Mapeamento de Sequência</title><style>
      @page { size: A4 portrait; margin: 8mm; } body { font-family: Arial, Helvetica, sans-serif; margin: 0; color: #111; font-size: 9px; }
      h1 { font-size: 14px; margin: 0 0 3mm; text-align: center; background: #2f7fe0; color: #fff; padding: 2mm; }
      .mblock { margin-bottom: 3mm; border: 0.5px solid #cbd5e1; } .mh { background: #1E3A5F; color: #fff; font-weight: bold; padding: 1mm 2mm; font-size: 10px; }
      table { width: 100%; border-collapse: collapse; } th { background: #E2E8F0; font-size: 8px; padding: 0.6mm; border: 0.4px solid #b6bfcc; }
      td { padding: 0.6mm; border: 0.4px solid #d5dae2; font-size: 8px; } .c { text-align: center; }
      </style></head><body><h1>MAPEAMENTO DE SEQUÊNCIA — ${seqMachine ? `MÁQUINA ${esc(seqMachine)}` : 'TODAS AS MÁQUINAS'}</h1>${rowsHtml}
      <script>window.onload = () => { window.focus(); window.print(); };<\/script></body></html>`;
    const w = window.open('', '_blank');
    if (!w) { toast({ title: 'Bloqueado pelo navegador', description: 'Permita pop-ups para gerar o PDF.', variant: 'destructive' }); return; }
    w.document.write(html);
    w.document.close();
  };

  return (
    <div className="gc-scope p-4 md:p-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-heading text-xl font-bold" style={{ color: 'var(--gc-foreground)' }}>Gestão da Malharia</h1>
          <p className="text-xs" style={{ color: 'var(--gc-muted-foreground)' }}>Controle de produção em tempo real · Relatório 32 (LOCAL 23)</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={generatePdf}><Download className="w-3.5 h-3.5" /> Baixar PDF</Button>
          <Button variant="outline" size="sm" onClick={() => { setMapDialogOpen(true); setMapResult(null); }}><Grid3x3 className="w-3.5 h-3.5" /> Mapeamento de Produção</Button>
          <Button variant="outline" size="sm" onClick={() => { setSeqDialogOpen(true); loadSeqData(); }}><ListOrdered className="w-3.5 h-3.5" /> Mapeamento de Sequência</Button>
        </div>
      </div>

      {statusData && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg px-4 py-2 text-xs"
          style={{ background: 'var(--gc-muted)', border: '1px solid var(--gc-border)', color: 'var(--gc-muted-foreground)' }}>
          <div className="flex items-center gap-2">
            <Database className="w-3.5 h-3.5" style={{ color: 'var(--gc-primary)' }} />
            <span>Dados sincronizados por <strong style={{ color: 'var(--gc-foreground)' }}>Claude</strong></span>
          </div>
          <div className="flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5" style={{ color: 'var(--gc-primary)' }} />
            <span>
              {statusData.last_sync
                ? new Date(statusData.last_sync).toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
                : '—'}
              {' · '}{(statusData.count ?? 0).toLocaleString('pt-BR')} registros
            </span>
          </div>
        </div>
      )}

      <Card>
        <CardContent className="pt-4">
          <div className="flex flex-wrap gap-3 items-end">
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Data Início</label>
              <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-44" />
            </div>
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Data Final</label>
              <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-44" />
            </div>
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Hora de</label>
              <Input type="time" value={timeFrom} onChange={(e) => setTimeFrom(e.target.value)} className="w-32" />
            </div>
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Hora até</label>
              <Input type="time" value={timeTo} onChange={(e) => setTimeTo(e.target.value)} className="w-32" />
            </div>
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Máquina</label>
              <select value={machineFilter} onChange={(e) => setMachineFilter(e.target.value)} className="gc-select">
                <option value="">Todas</option>
                {allMachines.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Turno</label>
              <select value={shiftFilter} onChange={(e) => {
                const val = e.target.value;
                setShiftFilter(val);
                if (val) { setDateFrom(getDefaultShiftAnchor(val)); setDateTo(''); setTimeFrom(''); setTimeTo(''); }
              }} className="gc-select">
                <option value="">Todos</option>
                <option value="MANHA">Manhã (06:20–14:19)</option>
                <option value="TARDE">Tarde (14:20–22:19)</option>
                <option value="NOITE">Noite (22:20–06:19)</option>
              </select>
            </div>
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Cliente</label>
              <select value={clientFilter} onChange={(e) => setClientFilter(e.target.value)} className="gc-select" style={{ minWidth: 180 }}>
                <option value="">Todos</option>
                {allClients.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <Button variant="ghost" size="sm" onClick={() => { setDateFrom(''); setDateTo(''); setTimeFrom(''); setTimeTo(''); setMachineFilter(''); setShiftFilter(''); setClientFilter(''); }}>
              Limpar filtros
            </Button>
          </div>
        </CardContent>
      </Card>

      {duplicateRows.length > 0 && (
        <Card style={{ borderColor: 'rgba(184,134,11,0.5)', background: 'rgba(184,134,11,0.06)' }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2" style={{ color: '#8a6d19' }}>
              <AlertTriangle className="w-4 h-4" /> {duplicateRows.length} pesagem{duplicateRows.length > 1 ? 's' : ''} duplicada{duplicateRows.length > 1 ? 's' : ''} ignorada{duplicateRows.length > 1 ? 's' : ''}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <p className="text-[11px]" style={{ color: '#8a6d19' }}>Mesma metragem + quilos para o mesmo artigo, item e máquina — contadas só 1 vez.</p>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard label="Qt. Peças" icon={FileSpreadsheet} value={qtPecas.toLocaleString('pt-BR')} colors={isLow ? KPI[0].lowColors : KPI[0].colors} />
        <KpiCard label="Qt. Movimento Total (MT)" icon={Activity} value={totalMovimento.toLocaleString('pt-BR', { maximumFractionDigits: 3 })} colors={KPI[1].colors} />
        <KpiCard label="Qt. Pesos Total" icon={TrendingUp} value={totalPesos.toLocaleString('pt-BR', { maximumFractionDigits: 3 })} colors={KPI[2].colors} />
        <KpiCard label="Máquinas Ativas" icon={Layers} value={uniqueMachines.length} colors={KPI[3].colors} />
      </div>

      <Card>
        <CardHeader><CardTitle className="text-base">Metragem por Máquina (Produzidas)</CardTitle></CardHeader>
        <CardContent>
          {byMachine.length === 0 ? (
            <p className="text-sm text-center py-8" style={{ color: 'var(--gc-muted-foreground)' }}>Nenhum dado disponível para o filtro atual.</p>
          ) : (
            <div className="space-y-2 max-h-72 overflow-y-auto">
              {byMachine.map(([machine, total]) => {
                const maxVal = byMachine[0][1] || 1;
                return (
                  <div key={machine} className="flex items-center gap-3 cursor-pointer rounded-lg px-2 py-1 hover:bg-black/5" onClick={() => setSelectedMachine(selectedMachine === machine ? null : machine)}>
                    <span className="text-sm font-mono w-16" style={{ color: 'var(--gc-muted-foreground)' }}>Máq. {machine}</span>
                    <div className="flex-1 rounded-full h-6 overflow-hidden" style={{ background: 'var(--gc-muted)' }}>
                      <div className="h-full rounded-full flex items-center justify-end pr-2" style={{ width: `${Math.max((total / maxVal) * 100, 5)}%`, background: selectedMachine === machine ? 'var(--gc-accent)' : 'var(--gc-primary)' }}>
                        <span className="text-xs text-white font-semibold">{total.toLocaleString('pt-BR', { maximumFractionDigits: 3 })}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!selectedMachine} onOpenChange={(v) => !v && setSelectedMachine(null)}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle>OPs da Máquina {selectedMachine}</DialogTitle></DialogHeader>
          <div className="overflow-x-auto max-h-[60vh] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0" style={{ background: 'var(--gc-card)' }}>
                <tr style={{ borderBottom: '1px solid var(--gc-border)' }}>
                  {['OP', 'Artigo', 'Nr. Item', 'Qt. Movimento (MT)', 'Qt. Pesos', 'Local', 'Dt. Real (Pesagem)'].map((h) => (
                    <th key={h} className="text-left py-2 font-heading text-xs" style={{ color: 'var(--gc-muted-foreground)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {machineDetail.map((row, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--gc-border)' }}>
                    <td className="py-2 font-semibold">{row.op}</td>
                    <td className="py-2 text-xs truncate max-w-[250px]">{row.artigo}</td>
                    <td className="py-2">{row.nrItem}</td>
                    <td className="py-2 font-semibold">{row.qtMovimento.toLocaleString('pt-BR', { maximumFractionDigits: 3 })}</td>
                    <td className="py-2">{row.qtPesos.toLocaleString('pt-BR', { maximumFractionDigits: 3 })}</td>
                    <td className="py-2 text-center text-xs font-semibold">{row.localCode || '—'}</td>
                    <td className="py-2 text-xs whitespace-nowrap">{row.dtReal ? format(row.dtReal, 'dd/MM/yyyy HH:mm', { locale: ptBR }) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="pt-2 text-xs text-right" style={{ color: 'var(--gc-muted-foreground)' }}>
              Total: {machineDetail.length} peças — {machineDetail.reduce((s, r) => s + r.qtMovimento, 0).toLocaleString('pt-BR', { maximumFractionDigits: 3 })} mt
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Card style={{ borderColor: 'rgba(229,72,77,0.35)' }}>
        <CardHeader><CardTitle className="text-base flex items-center gap-2" style={{ color: 'var(--gc-destructive)' }}><AlertTriangle className="w-4 h-4" /> Tempo Ocioso de Máquina &gt; +3h</CardTitle></CardHeader>
        <CardContent>
          {bottom5Articles.length === 0 ? (
            <p className="text-sm text-center py-8" style={{ color: 'var(--gc-muted-foreground)' }}>Nenhuma máquina ficou parada por mais de 3h entre pesagens.</p>
          ) : (
            <div className="overflow-x-auto">
              <p className="text-[11px] mb-2" style={{ color: 'var(--gc-muted-foreground)' }}>Clique no nome da máquina para ver a observação técnica.</p>
              <table className="w-full text-sm">
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--gc-border)' }}>
                    {['OP', 'Cod. Máquina', 'Cod. Artigo', 'Última Produção', 'Tempo Sem Produzir'].map((h) => (
                      <th key={h} className="text-left py-2 font-heading text-xs" style={{ color: 'var(--gc-muted-foreground)' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {bottom5Articles.map((item, i) => {
                    const h = Math.floor(item.horasSemProd);
                    const m = Math.round((item.horasSemProd - h) * 60);
                    return (
                      <tr key={i} style={{ borderBottom: '1px solid var(--gc-border)' }}>
                        <td className="py-2 text-xs font-semibold">{item.op}</td>
                        <td className="py-2 text-xs font-semibold whitespace-nowrap">
                          <MachinePop item={item} />
                        </td>
                        <td className="py-2 text-xs truncate max-w-[200px]">{item.artigo}</td>
                        <td className="py-2 text-xs whitespace-nowrap">{format(item.ultimaProd, "dd/MM/yyyy 'às' HH:mm", { locale: ptBR })}</td>
                        <td className="py-2 text-right font-semibold whitespace-nowrap" style={{ color: 'var(--gc-destructive)' }}>{h}h {m}min</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Dados ({filteredRows.length} registros)</CardTitle></CardHeader>
        <CardContent>
          <div className="overflow-x-auto max-h-96">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ borderBottom: '1px solid var(--gc-border)', background: 'var(--gc-muted)' }}>
                  {['Máquina', 'OP', 'Artigo', 'Qt. Movimento', 'Qt. Pesos', 'Dt. Real'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left font-heading uppercase" style={{ color: 'var(--gc-muted-foreground)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredRows.slice(0, 100).map((r, i) => (
                  <tr key={r.id || i} style={{ borderBottom: '1px solid var(--gc-border)' }}>
                    <td className="px-3 py-1.5">{r.cod_maquina}</td>
                    <td className="px-3 py-1.5">{r.op}</td>
                    <td className="px-3 py-1.5 truncate max-w-[200px]">{r.cod_artigo}</td>
                    <td className="px-3 py-1.5 font-semibold">{Number(r.qt_movimento).toLocaleString('pt-BR', { maximumFractionDigits: 3 })}</td>
                    <td className="px-3 py-1.5">{Number(r.qt_pesos).toLocaleString('pt-BR', { maximumFractionDigits: 3 })}</td>
                    <td className="px-3 py-1.5">{r.dt_real ? format(new Date(r.dt_real), 'dd/MM/yyyy HH:mm', { locale: ptBR }) : '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Dialog open={mapDialogOpen} onOpenChange={setMapDialogOpen}>
        <DialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle>Mapeamento de Produção</DialogTitle></DialogHeader>
          <div className="flex flex-wrap gap-3 items-end mb-4">
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Grupo</label>
              <select value={mapWhich} onChange={(e) => setMapWhich(e.target.value)} className="gc-select">
                <option value="ALL">Todos</option><option value="A">Grupo A</option><option value="B">Grupo B</option>
              </select>
            </div>
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Turno</label>
              <select value={mapShift} onChange={(e) => setMapShift(e.target.value)} className="gc-select">
                <option value="ALL">Todos</option>
                <option value="MANHA">1º Turno</option><option value="TARDE">2º Turno</option><option value="NOITE">3º Turno</option>
              </select>
            </div>
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Data (ciclo 06h→06h)</label>
              <Input type="date" value={mapDate} onChange={(e) => setMapDate(e.target.value)} className="w-44" />
            </div>
            <Button size="sm" onClick={generateMapeamento}>Gerar</Button>
            {mapResult && <Button size="sm" variant="outline" onClick={printMapeamento}>Imprimir / PDF</Button>}
          </div>
          {mapResult && (
            <div className="space-y-4">
              <p className="text-xs font-semibold" style={{ color: 'var(--gc-muted-foreground)' }}>
                TOTAL — Peças: {mapResult.totPcs} · Metros: {mapResult.totMet.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} · Quilos: {mapResult.totKg.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}
              </p>
              {mapResult.groups.map((g) => (
                <div key={g.name}>
                  <p className="text-sm font-heading font-bold mb-2" style={{ color: 'var(--gc-primary)' }}>{g.name}</p>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    {g.machines.map((m) => (
                      <div key={m.machine} className="rounded-lg border p-2 text-xs" style={{ borderColor: 'var(--gc-border)' }}>
                        <div className="flex justify-between font-semibold">
                          <span>MÁQ. {m.machine}</span>
                          <span style={{ color: m.cls === 'run' ? 'var(--gc-success)' : 'var(--gc-muted-foreground)' }}>{m.status}</span>
                        </div>
                        <p style={{ color: 'var(--gc-muted-foreground)' }}>Peças: {m.pieces.length} · Metros: {m.metros.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} · Kg: {m.kg.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}</p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={seqDialogOpen} onOpenChange={setSeqDialogOpen}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle>Mapeamento de Sequência</DialogTitle></DialogHeader>
          <div className="flex flex-wrap gap-3 items-end mb-4">
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--gc-muted-foreground)' }}>Máquina</label>
              <select value={seqMachine} onChange={(e) => setSeqMachine(e.target.value)} className="gc-select">
                <option value="">Todas</option>
                {seqMachines.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <Button size="sm" variant="outline" onClick={printMapeamentoSequencia}>Imprimir / PDF</Button>
          </div>
          {seqLoading ? (
            <p className="text-sm" style={{ color: 'var(--gc-muted-foreground)' }}>Carregando...</p>
          ) : (
            <div className="overflow-x-auto max-h-[60vh] overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0" style={{ background: 'var(--gc-card)' }}>
                  <tr style={{ borderBottom: '1px solid var(--gc-border)' }}>
                    {['Posição', 'Máquina', 'OP', 'Artigo', 'Cliente'].map((h) => (
                      <th key={h} className="text-left py-2 font-heading text-xs" style={{ color: 'var(--gc-muted-foreground)' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {withRank(seqAll.filter((r) => !seqMachine || String(r.machine) === seqMachine))
                    .sort((a, b) => String(a.machine).localeCompare(String(b.machine), undefined, { numeric: true }) || a.posicao - b.posicao)
                    .map((r, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--gc-border)' }}>
                      <td className="py-2 font-semibold" style={{ color: 'var(--gc-primary)' }}>{r.posicao}º</td>
                      <td className="py-2">{r.machine}{r.status === 'PRODUZINDO' && <span className="ml-1 text-[10px] font-semibold" style={{ color: 'var(--gc-success)' }}>(produzindo)</span>}</td>
                      <td className="py-2">{r.op}</td>
                      <td className="py-2 text-xs truncate max-w-[220px]">{r.article}</td>
                      <td className="py-2 text-xs">{r.client || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function MachinePop({ item }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 font-bold"
          style={{ border: '1px solid var(--gc-destructive)', background: 'rgba(229,72,77,0.1)', color: 'var(--gc-destructive)' }}
        >
          Máq. {item.maquina}
        </button>
      </PopoverTrigger>
      {open && (
        <PopoverContent className="w-80">
          <p className="text-[10px] font-bold uppercase mb-1" style={{ color: '#b8860b' }}>Observação para o líder</p>
          <p className="text-xs leading-relaxed">{item.comentario}</p>
        </PopoverContent>
      )}
    </Popover>
  );
}
