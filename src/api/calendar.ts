/**
 * Google Calendar, READ-ONLY, through withone.ai. The browser calls the Vite
 * proxy (/api/gcal/*), which adds the One secret + connection key server-side
 * and exposes only calendarList and events.list — there is no route that
 * writes. Creating/moving study blocks is Horae's job alone.
 *
 * Mirrors Horae's reader (Horae/horae/adapters/gcal.py): calendars are
 * filtered by `summary` against the ignore list, events are expanded with
 * singleEvents=true, and study blocks are the ones Horae writes into the
 * Auto-Study calendar with an "[Auto]" title prefix.
 */
import { config } from '../config';
import { ApiError, request } from './http';
import { dayKey } from '../lib/time';

const S = 'Google Calendar' as const;

/** Horae/horae/settings.py AUTO_BLOCK_PREFIX. */
export const AUTO_BLOCK_PREFIX = '[Auto]';

interface CalendarListEntry {
  id: string;
  summary?: string;
  summaryOverride?: string;
  primary?: boolean;
  backgroundColor?: string;
}

interface GEventTime {
  dateTime?: string;
  date?: string;
}

interface GEvent {
  id: string;
  status?: string;
  summary?: string;
  start?: GEventTime;
  end?: GEventTime;
  htmlLink?: string;
}

interface Page<T> {
  items?: T[];
  nextPageToken?: string;
}

export interface CalendarInfo {
  id: string;
  name: string;
  color?: string;
  isAutoStudy: boolean;
}

export interface CalEvent {
  id: string;
  title: string;
  start: Date;
  end: Date;
  allDay: boolean;
  /** YYYY-MM-DD in CHIRON_TIMEZONE (all-day events: their own date). */
  dayKey: string;
  calendar: CalendarInfo;
  /** A Horae study block: in the Auto-Study calendar and titled "[Auto] …". */
  isAuto: boolean;
  link?: string;
}

export interface WeekResult {
  calendars: CalendarInfo[];
  /** null when no calendar named config.autoStudyCalendar exists at all. */
  autoStudy: CalendarInfo | null;
  events: CalEvent[];
  /** Calendars whose events could not be read; the others still render. */
  failures: { calendar: CalendarInfo; message: string }[];
}

/** Guard against a runaway pagination loop; 5 × 250 items is far past a week. */
const MAX_PAGES = 5;

async function allPages<T>(fetchPage: (token?: string) => Promise<Page<T>>): Promise<T[]> {
  const out: T[] = [];
  let token: string | undefined;
  for (let i = 0; i < MAX_PAGES; i++) {
    const page = await fetchPage(token);
    out.push(...(page.items ?? []));
    token = page.nextPageToken;
    if (!token) break;
  }
  return out;
}

function parseTime(t: GEventTime | undefined): { date: Date; allDay: boolean; dayKey: string } | null {
  if (t?.dateTime) {
    const date = new Date(t.dateTime);
    return { date, allDay: false, dayKey: dayKey(date) };
  }
  if (t?.date) {
    // All-day: a calendar date, not an instant — its key is the date itself.
    return { date: new Date(`${t.date}T00:00:00Z`), allDay: true, dayKey: t.date };
  }
  return null;
}

export async function loadRange(timeMin: Date, timeMax: Date): Promise<WeekResult> {
  const entries = await allPages<CalendarListEntry>((token) =>
    request<Page<CalendarListEntry>>(S, `/api/gcal/calendars${token ? `?pageToken=${encodeURIComponent(token)}` : ''}`, {
      timeoutMs: 25_000,
    }),
  );

  const ignored = new Set(config.ignoredCalendars);
  const calendars: CalendarInfo[] = entries
    .filter((c) => !ignored.has(c.summary ?? ''))
    .map((c) => ({
      id: c.id,
      name: c.summaryOverride || c.summary || c.id,
      color: c.backgroundColor,
      isAutoStudy: (c.summary ?? '') === config.autoStudyCalendar,
    }));

  const results = await Promise.allSettled(
    calendars.map(async (cal) => {
      const items = await allPages<GEvent>((token) => {
        const q = new URLSearchParams({
          calendarId: cal.id,
          timeMin: timeMin.toISOString(),
          timeMax: timeMax.toISOString(),
        });
        if (token) q.set('pageToken', token);
        return request<Page<GEvent>>(S, `/api/gcal/events?${q}`, { timeoutMs: 25_000 });
      });
      return { cal, items };
    }),
  );

  const events: CalEvent[] = [];
  const failures: WeekResult['failures'] = [];
  results.forEach((r, i) => {
    if (r.status === 'rejected') {
      const message = r.reason instanceof ApiError ? r.reason.message : String(r.reason);
      failures.push({ calendar: calendars[i], message });
      return;
    }
    const { cal, items } = r.value;
    for (const ev of items) {
      if (ev.status === 'cancelled') continue;
      const start = parseTime(ev.start);
      const end = parseTime(ev.end);
      if (!start || !end) continue;
      const rawTitle = ev.summary?.trim() || '(không có tiêu đề)';
      const isAuto = cal.isAutoStudy && rawTitle.startsWith(AUTO_BLOCK_PREFIX);
      events.push({
        id: `${cal.id}/${ev.id}`,
        title: isAuto ? rawTitle.slice(AUTO_BLOCK_PREFIX.length).trim() || rawTitle : rawTitle,
        start: start.date,
        end: end.date,
        allDay: start.allDay,
        dayKey: start.dayKey,
        calendar: cal,
        isAuto,
        link: ev.htmlLink,
      });
    }
  });
  events.sort((a, b) => a.start.getTime() - b.start.getTime());

  return {
    calendars,
    autoStudy: calendars.find((c) => c.isAutoStudy) ?? null,
    events,
    failures,
  };
}
