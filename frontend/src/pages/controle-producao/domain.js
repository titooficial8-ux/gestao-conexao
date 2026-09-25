/* Regras de negocio do Controle de Producao — porte 1:1 da logica real do
   PCP Hub (aba "Dados Malharia" dentro de Qualidade2.tsx). */

export const CICLO_MIN = 480;
export const CICLO_POR_PECA = 120;
export const PECAS_POR_TURNO_POR_MAQ = CICLO_MIN / CICLO_POR_PECA; // 4
export const TURNOS = 3;
export const CAP_METROS_INSTALADA_TURNO_FIXA = 5184;
export const CAP_PECAS_INSTALADA_TURNO_FIXA = 108;

export const SHIFT_META = {
  MANHA: { key: 'MANHA', label: '1º Turno', horario: '06:20 — 14:19', from: 6 * 60 + 20, to: 14 * 60 + 19 },
  TARDE: { key: 'TARDE', label: '2º Turno', horario: '14:20 — 22:19', from: 14 * 60 + 20, to: 22 * 60 + 19 },
  NOITE: { key: 'NOITE', label: '3º Turno', horario: '22:20 — 06:19', from: 22 * 60 + 20, to: 6 * 60 + 19 },
};

const brMinutes = (d) => {
  const s = d.toLocaleTimeString('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit', hour12: false });
  const [h, m] = s.split(':').map(Number);
  return h * 60 + m;
};

export function productionDateOf(d) {
  const mm = brMinutes(d);
  const local = new Date(d);
  const dateKey = local.toLocaleDateString('en-CA', { timeZone: 'America/Sao_Paulo' });
  if (mm < 6 * 60 + 20) {
    const prev = new Date(`${dateKey}T12:00:00Z`);
    prev.setUTCDate(prev.getUTCDate() - 1);
    return prev.toLocaleDateString('en-CA', { timeZone: 'America/Sao_Paulo' });
  }
  return dateKey;
}

export function getShift(d) {
  const mm = brMinutes(d);
  if (mm >= SHIFT_META.MANHA.from && mm <= SHIFT_META.MANHA.to) return 'MANHA';
  if (mm >= SHIFT_META.TARDE.from && mm <= SHIFT_META.TARDE.to) return 'TARDE';
  return 'NOITE';
}

export function normalizeMachine(v) {
  const m = String(v ?? '').match(/(\d+)/);
  return m ? String(parseInt(m[1], 10)) : String(v ?? '').trim().toUpperCase();
}

export const nf = (v) => (Number(v) || 0).toLocaleString('pt-BR', { maximumFractionDigits: 0 });
export const nf1 = (v) => (Number(v) || 0).toLocaleString('pt-BR', { maximumFractionDigits: 1 });
export const nfKg = (v) => (Number(v) || 0).toLocaleString('pt-BR', { maximumFractionDigits: 3 });
export const pct = (a, b) => (b > 0 ? (a / b) * 100 : 0);

export function toneFor(p) {
  if (p >= 90) return 'emerald';
  if (p >= 85) return 'amber';
  return 'rose';
}

export const TONE_COLOR = {
  emerald: 'var(--gc-success)',
  amber: '#b8860b',
  rose: 'var(--gc-destructive)',
};

export function isSample(codArtigo) {
  return !!codArtigo && /amostra/i.test(codArtigo);
}
