/**
 * App-wide state: local settings, per-module connection health, the current
 * Mnemosyne user and their study sets, the due-card count for the sidebar,
 * and the locally remembered list of Socratic sessions.
 *
 * Each module's health is tracked separately and nothing here throws: a
 * module that is down shows up as `down` and as an `error` on the data that
 * came from it, and every consumer renders that for its own area only.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { config } from '../config';
import { mnemosyne, type StudySet, type User } from '../api/mnemosyne';
import { ks, proxyStatus, type ProxyStatus } from '../api/ks';
import { loadRange } from '../api/calendar';
import { addDays, keyBoundsPadded, todayKey } from '../lib/time';
import { load, loadList, save } from '../lib/storage';
import { useAsync } from '../lib/useAsync';

export const ACCENTS = ['#88C0D0', '#81A1C1', '#B48EAD', '#A3BE8C'] as const;

export interface Settings {
  /** Chosen Mnemosyne user. Empty → CHIRON_MNEMOSYNE_USER_ID → the only user, if exactly one. */
  userId: string;
  accent: string;
  sidebarCollapsed: boolean;
  starTexture: boolean;
}

const DEFAULT_SETTINGS: Settings = { userId: '', accent: ACCENTS[0], sidebarCollapsed: false, starTexture: true };
const SETTINGS_KEY = 'chiron.settings.v1';

/**
 * Mnemosyne has no "list sessions" endpoint, so the sidebar's "Gần đây" is
 * this browser's own record of the sessions it started. The messages always
 * come from GET /socratic/{id}; only the index lives here.
 */
export interface RecentSession {
  sessionId: string;
  userId: string;
  setId: string;
  setName: string;
  startedAt: string;
  ended: boolean;
}
const RECENT_KEY = 'chiron.recent.v1';
const RECENT_MAX = 40;

export type Health = 'checking' | 'ok' | 'down';

interface AppState {
  settings: Settings;
  updateSettings: (patch: Partial<Settings>) => void;

  health: { mnemosyne: Health; ks: Health; proxy: ProxyStatus | null };
  recheck: () => void;

  users: User[] | undefined;
  usersError: unknown;
  userId: string;
  user: User | undefined;

  studySets: StudySet[] | undefined;
  studySetsError: unknown;
  reloadSets: () => void;

  dueCount: number | undefined;
  refreshDue: () => void;

  todayEventCount: number | undefined;
  setTodayEventCount: (n: number | undefined) => void;

  recent: RecentSession[];
  upsertRecent: (s: RecentSession) => void;
}

const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useApp outside AppProvider');
  return v;
}

const HEALTH_INTERVAL_MS = 20_000;

export function AppProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(() => load(SETTINGS_KEY, DEFAULT_SETTINGS));
  const updateSettings = useCallback((patch: Partial<Settings>) => {
    setSettings((s) => {
      const next = { ...s, ...patch };
      save(SETTINGS_KEY, next);
      return next;
    });
  }, []);

  // Design props (accent / sidebarCollapsed / starTexture) applied to :root,
  // the same way Chiron.dc.html's DCLogic.apply() does.
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--frost', settings.accent);
    root.classList.toggle('sb-collapsed', settings.sidebarCollapsed);
    root.classList.toggle('no-stars', !settings.starTexture);
  }, [settings.accent, settings.sidebarCollapsed, settings.starTexture]);

  // ---------------------------------------------------------------- health
  const [health, setHealth] = useState<AppState['health']>({ mnemosyne: 'checking', ks: 'checking', proxy: null });
  const [healthTick, setHealthTick] = useState(0);
  const recheck = useCallback(() => setHealthTick((t) => t + 1), []);

  useEffect(() => {
    let live = true;
    const run = async () => {
      const [m, k, p] = await Promise.allSettled([mnemosyne.health(), ks.health(), proxyStatus()]);
      if (!live) return;
      setHealth({
        mnemosyne: m.status === 'fulfilled' ? 'ok' : 'down',
        ks: k.status === 'fulfilled' ? 'ok' : 'down',
        proxy: p.status === 'fulfilled' ? p.value : null,
      });
    };
    run();
    const id = window.setInterval(run, HEALTH_INTERVAL_MS);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [healthTick]);

  const mOk = health.mnemosyne === 'ok';

  // ---------------------------------------------------------------- user
  const usersQ = useAsync(() => mnemosyne.listUsers(), [mOk], mOk);
  const users = usersQ.data;

  const userId = useMemo(() => {
    const wanted = settings.userId || config.defaultUserId;
    if (!users) return wanted; // not loaded yet (or Mnemosyne down): trust the setting
    if (wanted) return users.some((u) => u.id === wanted) ? wanted : '';
    return users.length === 1 ? users[0].id : '';
  }, [settings.userId, users]);
  const user = users?.find((u) => u.id === userId);

  // Remember an automatic pick (the only user), so a later Mnemosyne outage
  // still knows who is learning instead of reading as "no user chosen".
  useEffect(() => {
    if (!settings.userId && !config.defaultUserId && users?.length === 1) updateSettings({ userId: users[0].id });
  }, [settings.userId, users, updateSettings]);

  const setsQ = useAsync(() => mnemosyne.listStudySets(userId), [userId, mOk], mOk && Boolean(userId));

  const [dueTick, setDueTick] = useState(0);
  const refreshDue = useCallback(() => setDueTick((t) => t + 1), []);
  const dueQ = useAsync(() => mnemosyne.due(userId, 100), [userId, mOk, dueTick], mOk && Boolean(userId));

  // ---------------------------------------------------------------- calendar badge
  const [todayEventCount, setTodayEventCount] = useState<number>();
  const gcalConfigured = health.proxy?.gcal.configured ?? false;
  useEffect(() => {
    if (!gcalConfigured) {
      setTodayEventCount(undefined);
      return;
    }
    let live = true;
    const today = todayKey();
    const { timeMin, timeMax } = keyBoundsPadded(today, addDays(today, 1));
    loadRange(timeMin, timeMax).then(
      (r) => live && setTodayEventCount(r.events.filter((e) => e.dayKey === today).length),
      () => live && setTodayEventCount(undefined), // the Lịch học view reports the error itself
    );
    return () => {
      live = false;
    };
  }, [gcalConfigured]);

  // ---------------------------------------------------------------- recent sessions
  const [recentAll, setRecentAll] = useState<RecentSession[]>(() => loadList<RecentSession>(RECENT_KEY));
  const upsertRecent = useCallback((s: RecentSession) => {
    setRecentAll((list) => {
      const next = [s, ...list.filter((x) => x.sessionId !== s.sessionId)].slice(0, RECENT_MAX);
      save(RECENT_KEY, next);
      return next;
    });
  }, []);
  const recent = useMemo(() => recentAll.filter((r) => r.userId === userId), [recentAll, userId]);

  const value: AppState = {
    settings,
    updateSettings,
    health,
    recheck,
    users,
    usersError: usersQ.error,
    userId,
    user,
    studySets: setsQ.data,
    studySetsError: setsQ.error,
    reloadSets: setsQ.reload,
    dueCount: dueQ.error ? undefined : dueQ.data?.count,
    refreshDue,
    todayEventCount,
    setTodayEventCount,
    recent,
    upsertRecent,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
