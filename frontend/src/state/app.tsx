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
import { mnemosyne, type StudySet, type User } from '../api/mnemosyne';
import { getToken, onTokenChange, setToken } from '../api/session';
import { ks, proxyStatus, type ProxyStatus } from '../api/ks';
import { load, save } from '../lib/storage';
import { useAsync } from '../lib/useAsync';

export const ACCENTS = ['#88C0D0', '#81A1C1', '#B48EAD', '#A3BE8C'] as const;

export interface Settings {
  accent: string;
  sidebarCollapsed: boolean;
  starTexture: boolean;
}

const DEFAULT_SETTINGS: Settings = { accent: ACCENTS[0], sidebarCollapsed: false, starTexture: true };
const SETTINGS_KEY = 'chiron.settings.v1';

/**
 * One row of the sidebar's "Gần đây", from the server rather than from this
 * browser's memory: `GET /socratic` and `GET /chat`. The old localStorage
 * index only knew about sessions started in this browser, and could not know
 * that one of them had since been ended somewhere else.
 */
export interface RecentSession {
  kind: 'socratic' | 'chat';
  id: string;
  title: string;
  subtitle: string;
  updatedAt: string;
  ended: boolean;
  setId?: string;
}

export type Health = 'checking' | 'ok' | 'down';

interface AppState {
  settings: Settings;
  updateSettings: (patch: Partial<Settings>) => void;

  health: { mnemosyne: Health; ks: Health; proxy: ProxyStatus | null };
  recheck: () => void;

  /** The learner's Mnemosyne token, held in this browser (api/session.ts). */
  token: string;
  saveToken: (token: string) => void;
  /** Who that token belongs to, from GET /me. */
  user: User | undefined;
  userError: unknown;
  userLoading: boolean;
  reloadUser: () => void;

  studySets: StudySet[] | undefined;
  studySetsError: unknown;
  reloadSets: () => void;

  stats: import('../api/mnemosyne').Stats | undefined;
  statsError: unknown;
  dueCount: number | undefined;
  /** Cards still failing the window test — the sidebar badge on Điểm yếu. */
  weakCount: number | undefined;
  refreshDue: () => void;
  /** Open items on the review todo list — the sidebar badge on Việc cần ôn. */
  todoOpenCount: number | undefined;
  /** Call after adding or ticking off a todo item. */
  refreshTodos: () => void;

  recent: RecentSession[];
  recentError: unknown;
  /** Call after starting or ending a conversation so the sidebar catches up. */
  refreshSessions: () => void;
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

  // ---------------------------------------------------------------- learner
  // The token identifies the learner; there is no picker any more, because a
  // browser can only be whoever its token says it is.
  const [token, setTokenState] = useState(getToken);
  useEffect(() => onTokenChange(setTokenState), []);
  const saveToken = useCallback((next: string) => setToken(next), []);

  const userQ = useAsync(() => mnemosyne.me(), [mOk, token], mOk && Boolean(token));
  const user = userQ.data;
  const authed = mOk && Boolean(token) && Boolean(user);

  const setsQ = useAsync(() => mnemosyne.listStudySets(), [token, mOk], authed);

  const [dueTick, setDueTick] = useState(0);
  const refreshDue = useCallback(() => setDueTick((t) => t + 1), []);
  const dueQ = useAsync(() => mnemosyne.due(100), [token, mOk, dueTick], authed);
  const weakQ = useAsync(() => mnemosyne.weakCards(), [token, mOk, dueTick], authed);
  const statsQ = useAsync(() => mnemosyne.stats(14), [token, mOk, dueTick], authed);
  // A review can open a weak-card item, so the badge follows dueTick too.
  const [todoTick, setTodoTick] = useState(0);
  const refreshTodos = useCallback(() => setTodoTick((t) => t + 1), []);
  const todoQ = useAsync(() => mnemosyne.todos(false), [token, mOk, dueTick, todoTick], authed);

  // ---------------------------------------------------------------- recent sessions
  const [sessionTick, setSessionTick] = useState(0);
  const refreshSessions = useCallback(() => setSessionTick((t) => t + 1), []);
  const socraticQ = useAsync(() => mnemosyne.socraticList(), [token, mOk, sessionTick], authed);
  const chatQ = useAsync(() => mnemosyne.chatList(), [token, mOk, sessionTick], authed);

  const recent: RecentSession[] = useMemo(() => {
    const socratic: RecentSession[] = (socraticQ.data?.sessions ?? []).map((x) => ({
      kind: 'socratic',
      id: x.id,
      title: x.set_name,
      subtitle: 'Học bài',
      updatedAt: x.last_message_at ?? x.created_at,
      ended: x.ended,
      setId: x.set_id,
    }));
    const chats: RecentSession[] = (chatQ.data?.sessions ?? []).map((x) => ({
      kind: 'chat',
      id: x.id,
      title: x.title,
      subtitle: x.mode === 'ask' ? 'Hỏi bài' : 'Giải bài',
      updatedAt: x.updated_at,
      ended: false,
      setId: x.set_id ?? undefined,
    }));
    return [...socratic, ...chats].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }, [socraticQ.data, chatQ.data]);

  const value: AppState = {
    settings,
    updateSettings,
    health,
    recheck,
    token,
    saveToken,
    user,
    userError: userQ.error,
    userLoading: userQ.loading,
    reloadUser: userQ.reload,
    studySets: setsQ.data,
    studySetsError: setsQ.error,
    reloadSets: setsQ.reload,
    stats: statsQ.data,
    statsError: statsQ.error,
    dueCount: dueQ.error ? undefined : dueQ.data?.count,
    weakCount: weakQ.error ? undefined : weakQ.data?.still_weak_count,
    refreshDue,
    todoOpenCount: todoQ.error ? undefined : todoQ.data?.open_count,
    refreshTodos,
    recent,
    recentError: socraticQ.error ?? chatQ.error,
    refreshSessions,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
