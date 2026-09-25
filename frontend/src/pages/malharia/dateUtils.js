/* Utilidades de data/turno — porte 1:1 da logica real do PCP Hub (Malharia.tsx). */

export function parseISODateOnly(raw) {
  const m = String(raw || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  const d = new Date(year, month - 1, day, 0, 0, 0, 0);
  if (d.getFullYear() !== year || d.getMonth() !== month - 1 || d.getDate() !== day) return null;
  return d;
}

const brDateParts = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit', day: '2-digit',
});
export function getLocalDateKey(d) {
  const parts = Object.fromEntries(brDateParts.formatToParts(d).map((p) => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function addDays(date, days) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

export function parseTimeToMinutes(raw) {
  const m = String(raw || '').match(/^(\d{2}):(\d{2})$/);
  if (!m) return null;
  const hours = Number(m[1]);
  const minutes = Number(m[2]);
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;
  return hours * 60 + minutes;
}

export function buildLocalDateTime(dateStr, timeStr, boundary) {
  const base = parseISODateOnly(dateStr);
  if (!base) return null;
  if (!timeStr) {
    if (boundary === 'start') base.setHours(0, 0, 0, 0);
    else base.setHours(23, 59, 59, 999);
    return base;
  }
  const totalMinutes = parseTimeToMinutes(timeStr);
  if (totalMinutes === null) return null;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  base.setHours(hours, minutes, boundary === 'end' ? 59 : 0, boundary === 'end' ? 999 : 0);
  return base;
}

export function isInShiftWindow(d, shift, anchorDateStr) {
  const anchor = parseISODateOnly(anchorDateStr);
  if (!anchor) return false;
  let start, end;
  if (shift === 'MANHA') {
    start = new Date(anchor); start.setHours(6, 20, 0, 0);
    end = new Date(anchor); end.setHours(14, 19, 59, 999);
  } else if (shift === 'TARDE') {
    start = new Date(anchor); start.setHours(14, 20, 0, 0);
    end = new Date(anchor); end.setHours(22, 19, 59, 999);
  } else {
    start = new Date(anchor); start.setHours(22, 20, 0, 0);
    end = new Date(anchor); end.setDate(end.getDate() + 1); end.setHours(6, 19, 59, 999);
  }
  return d >= start && d <= end;
}

export function getDefaultShiftAnchor(shift, requestedDate) {
  const now = new Date();
  const todayKey = getLocalDateKey(now);
  if (shift === 'NOITE' && (!requestedDate || requestedDate === todayKey)) {
    const minutesNow = now.getHours() * 60 + now.getMinutes();
    if (minutesNow < 22 * 60 + 20) return getLocalDateKey(addDays(now, -1));
  }
  return requestedDate || todayKey;
}
