/**
 * Date helpers pinned to CHIRON_TIMEZONE rather than the browser's zone, so
 * the schedule groups days the way Horae plans them. Days are handled as
 * "YYYY-MM-DD" keys; arithmetic on keys goes through UTC to dodge DST.
 */
import { config } from '../config';

const tz = config.timezone;

const keyFmt = new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' });
const timeFmt = new Intl.DateTimeFormat('vi-VN', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false });
const dateTimeFmt = new Intl.DateTimeFormat('vi-VN', {
  timeZone: tz, hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', hour12: false,
});

export function dayKey(d: Date): string {
  return keyFmt.format(d);
}

export function todayKey(): string {
  return dayKey(new Date());
}

function keyToUtc(key: string): Date {
  return new Date(`${key}T00:00:00Z`);
}

export function addDays(key: string, n: number): string {
  const d = keyToUtc(key);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Monday of the week containing `key` (Vietnamese weeks start on Monday). */
export function mondayOf(key: string): string {
  const dow = keyToUtc(key).getUTCDay(); // 0 = Sunday
  return addDays(key, dow === 0 ? -6 : 1 - dow);
}

/** A UTC instant safely before/after every instant that falls on `key` in any zone. */
export function keyBoundsPadded(fromKey: string, toKeyExclusive: string): { timeMin: Date; timeMax: Date } {
  const min = keyToUtc(fromKey);
  min.setUTCDate(min.getUTCDate() - 1);
  const max = keyToUtc(toKeyExclusive);
  max.setUTCDate(max.getUTCDate() + 1);
  return { timeMin: min, timeMax: max };
}

const WEEKDAYS = ['Chủ nhật', 'Thứ hai', 'Thứ ba', 'Thứ tư', 'Thứ năm', 'Thứ sáu', 'Thứ bảy'];

export function dayLabel(key: string): string {
  const d = keyToUtc(key);
  return `${WEEKDAYS[d.getUTCDay()]}, ${d.getUTCDate()}/${d.getUTCMonth() + 1}`;
}

export function shortDate(key: string): string {
  const d = keyToUtc(key);
  return `${d.getUTCDate()}/${d.getUTCMonth() + 1}`;
}

export function clock(d: Date): string {
  return timeFmt.format(d);
}

export function dateTime(d: Date | string): string {
  return dateTimeFmt.format(typeof d === 'string' ? new Date(d) : d);
}

/** "vừa xong", "5 phút trước", "3 ngày trước", "sau 2 ngày". */
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
