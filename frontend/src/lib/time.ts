/**
 * Date formatting pinned to CHIRON_TIMEZONE rather than the browser's zone, so
 * every screen shows times in the learner's configured zone.
 */
import { config } from '../config';

const tz = config.timezone;

const timeFmt = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false });
const dateTimeFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: tz, hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short', hour12: false,
});

export function clock(d: Date): string {
  return timeFmt.format(d);
}

export function dateTime(d: Date | string): string {
  return dateTimeFmt.format(typeof d === 'string' ? new Date(d) : d);
}

/** "Done 5 min ago", or "Done just now". */
export function doneAgo(d: Date | string): string {
  return `Done ${relative(d)}`;
}

/** "just now", "5 min ago", "3 days ago", "in 2 days". */
export function relative(d: Date | string): string {
  const t = typeof d === 'string' ? new Date(d).getTime() : d.getTime();
  const diff = t - Date.now();
  const abs = Math.abs(diff);
  const min = 60_000, hour = 60 * min, day = 24 * hour;
  let s: string;
  if (abs < min) return 'just now';
  if (abs < hour) s = `${Math.round(abs / min)} min`;
  else if (abs < day) {
    const n = Math.round(abs / hour);
    s = `${n} hour${n === 1 ? '' : 's'}`;
  } else {
    const n = Math.round(abs / day);
    s = `${n} day${n === 1 ? '' : 's'}`;
  }
  return diff < 0 ? `${s} ago` : `in ${s}`;
}

export function minutesBetween(a: string | Date, b: string | Date): number {
  const ta = typeof a === 'string' ? new Date(a).getTime() : a.getTime();
  const tb = typeof b === 'string' ? new Date(b).getTime() : b.getTime();
  return Math.max(0, Math.round((tb - ta) / 60_000));
}
