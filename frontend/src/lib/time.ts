/**
 * Date formatting pinned to CHIRON_TIMEZONE rather than the browser's zone, so
 * every screen shows times in the learner's configured zone.
 */
import { config } from '../config';

const tz = config.timezone;

const timeFmt = new Intl.DateTimeFormat('vi-VN', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false });
const dateTimeFmt = new Intl.DateTimeFormat('vi-VN', {
  timeZone: tz, hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', hour12: false,
});

export function clock(d: Date): string {
  return timeFmt.format(d);
}

export function dateTime(d: Date | string): string {
  return dateTimeFmt.format(typeof d === 'string' ? new Date(d) : d);
}

/** "vừa xong", "5 phút trước", "3 ngày trước", "sau 2 ngày". */
/** "Xong 5 phút trước", or "Vừa xong" — never the doubled "Xong vừa xong". */
export function doneAgo(d: Date | string): string {
  const r = relative(d);
  return r === 'vừa xong' ? 'Vừa xong' : `Xong ${r}`;
}

export function relative(d: Date | string): string {
  const t = typeof d === 'string' ? new Date(d).getTime() : d.getTime();
  const diff = t - Date.now();
  const abs = Math.abs(diff);
  const min = 60_000, hour = 60 * min, day = 24 * hour;
  let s: string;
  if (abs < min) return 'vừa xong';
  if (abs < hour) s = `${Math.round(abs / min)} phút`;
  else if (abs < day) s = `${Math.round(abs / hour)} giờ`;
  else s = `${Math.round(abs / day)} ngày`;
  return diff < 0 ? `${s} trước` : `sau ${s}`;
}

export function minutesBetween(a: string | Date, b: string | Date): number {
  const ta = typeof a === 'string' ? new Date(a).getTime() : a.getTime();
  const tb = typeof b === 'string' ? new Date(b).getTime() : b.getTime();
  return Math.max(0, Math.round((tb - ta) / 60_000));
}
