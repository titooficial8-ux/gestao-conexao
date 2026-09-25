// Helpers de turno / dia de producao da Malharia — porte 1:1 do
// src/lib/malhariaShifts.ts do PCP Hub. Dia de producao vira as 06:20 BRT.

export const SHIFT_RANGES = {
  MANHA: '06:20 – 14:19',
  TARDE: '14:20 – 22:19',
  NOITE: '22:20 – 06:19',
};

export const SHIFT_LABELS = {
  MANHA: 'Manhã',
  TARDE: 'Tarde',
  NOITE: 'Noite',
};

export const SHIFT_ORDINAL = {
  MANHA: '1º Turno',
  TARDE: '2º Turno',
  NOITE: '3º Turno',
};

export const SHIFT_SEQUENCE = ['MANHA', 'TARDE', 'NOITE'];

const BR_FORMATTER = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
});

export const brParts = (d) => {
  const fmt = BR_FORMATTER.formatToParts(d).reduce((acc, p) => {
    if (p.type !== 'literal') acc[p.type] = p.value;
    return acc;
  }, {});
  return {
    year: Number(fmt.year), month: Number(fmt.month), day: Number(fmt.day),
    hour: Number(fmt.hour === '24' ? '0' : fmt.hour), minute: Number(fmt.minute),
  };
};

// ---------- Versoes rapidas (para uso em laco, sem Intl) ----------
// BRT = UTC-3 fixo o ano inteiro.
const BRT_OFFSET_MS = 3 * 60 * 60 * 1000;
const PROD_DAY_START_MS = (6 * 60 + 20) * 60 * 1000;

export const prodDayKeyFast = (ms) => {
  const d = new Date(ms - BRT_OFFSET_MS - PROD_DAY_START_MS);
  const y = d.getUTCFullYear();
  const m = d.getUTCMonth() + 1;
  const day = d.getUTCDate();
  return `${y}-${String(m).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
};

export const shiftOfFast = (ms) => {
  const d = new Date(ms - BRT_OFFSET_MS);
  const min = d.getUTCHours() * 60 + d.getUTCMinutes();
  if (min >= 6 * 60 + 20 && min <= 14 * 60 + 19) return 'MANHA';
  if (min >= 14 * 60 + 20 && min <= 22 * 60 + 19) return 'TARDE';
  return 'NOITE';
};
