/**
 * Pomodoro timer — browser only. Nothing is sent to a server and no session
 * history is kept; only the two durations are remembered, per browser.
 *
 * The countdown is computed from a fixed end time, not by subtracting a second
 * per tick: browsers throttle timers in background tabs, and a tick-counting
 * timer would fall minutes behind while the learner is in another tab.
 *
 * At the end of a phase the timer stops and waits for the learner to start
 * the next one, rather than rolling on by itself — a timer left running on an
 * empty desk would otherwise count phantom rounds.
 */
import { useEffect, useRef, useState } from 'react';

type Phase = 'work' | 'break';
type Status = 'idle' | 'running' | 'paused';

const STORAGE_KEY = 'chiron.pomodoro';
const DEFAULTS = { work: 25, break: 5 };
const LIMITS = { work: [1, 120], break: [1, 60] } as const;

function loadDurations(): { work: number; break: number } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const v = JSON.parse(raw) as Partial<typeof DEFAULTS>;
    return { work: clamp(v.work, 'work'), break: clamp(v.break, 'break') };
  } catch {
    return DEFAULTS;
  }
}

function saveDurations(d: { work: number; break: number }) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(d));
  } catch {
    // Private window or blocked storage: the timer still works, it just forgets.
  }
}

function clamp(n: unknown, phase: Phase): number {
  const [lo, hi] = LIMITS[phase];
  const v = typeof n === 'number' && Number.isFinite(n) ? Math.round(n) : DEFAULTS[phase];
  return Math.min(hi, Math.max(lo, v));
}

function mmss(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

/** Two short tones. Web Audio needs no asset and no permission. */
function chime() {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    [0, 0.35].forEach((at, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = i === 0 ? 880 : 660;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime + at);
      gain.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + at + 0.3);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + at);
      osc.stop(ctx.currentTime + at + 0.32);
    });
    setTimeout(() => void ctx.close(), 1000);
  } catch {
    // No audio device: the on-screen message is enough.
  }
}

const PHASE_LABEL: Record<Phase, string> = { work: 'Focus', break: 'Break' };

export function Pomodoro({ task }: { task?: string }) {
  const [durations, setDurations] = useState(loadDurations);
  const [phase, setPhase] = useState<Phase>('work');
  const [status, setStatus] = useState<Status>('idle');
  const [remaining, setRemaining] = useState(durations.work * 60_000);
  const [endsAt, setEndsAt] = useState<number>();
  const [rounds, setRounds] = useState(0);
  const [message, setMessage] = useState<string>();
  const [, setNow] = useState(0);
  const titleRef = useRef(document.title);

  const left = status === 'running' && endsAt ? endsAt - Date.now() : remaining;

  // Tick while running; finish the phase when its end time has passed.
  useEffect(() => {
    if (status !== 'running' || !endsAt) return;
    const id = window.setInterval(() => {
      if (Date.now() >= endsAt) {
        window.clearInterval(id);
        chime();
        const next: Phase = phase === 'work' ? 'break' : 'work';
        if (phase === 'work') setRounds((r) => r + 1);
        setMessage(phase === 'work' ? 'Focus time is up — take a break.' : 'Break is over — ready for the next round.');
        setPhase(next);
        setStatus('idle');
        setEndsAt(undefined);
        setRemaining(durations[next] * 60_000);
      } else {
        setNow(Date.now());
      }
    }, 250);
    return () => window.clearInterval(id);
  }, [status, endsAt, phase, durations]);

  // The tab title shows the countdown, so it is visible from another tab.
  useEffect(() => {
    const original = titleRef.current;
    if (status === 'running') document.title = `${mmss(left)} · ${PHASE_LABEL[phase]} — Chiron`;
    else document.title = original;
    return () => {
      document.title = original;
    };
  }, [status, left, phase]);

  const start = () => {
    setMessage(undefined);
    setEndsAt(Date.now() + remaining);
    setStatus('running');
  };
  const pause = () => {
    if (endsAt) setRemaining(Math.max(0, endsAt - Date.now()));
    setEndsAt(undefined);
    setStatus('paused');
  };
  const reset = () => {
    setStatus('idle');
    setEndsAt(undefined);
    setRemaining(durations[phase] * 60_000);
    setMessage(undefined);
  };
  const switchTo = (p: Phase) => {
    setPhase(p);
    setStatus('idle');
    setEndsAt(undefined);
    setRemaining(durations[p] * 60_000);
    setMessage(undefined);
  };
  const setDuration = (p: Phase, value: string) => {
    const next = { ...durations, [p]: clamp(Number(value), p) };
    setDurations(next);
    saveDurations(next);
    if (p === phase && status === 'idle') setRemaining(next[p] * 60_000);
  };

  const total = durations[phase] * 60_000;
  const progress = Math.min(1, Math.max(0, 1 - left / total));

  return (
    <aside className="kn-detail pomo" aria-label="Pomodoro">
      <div className="section-label" style={{ marginTop: 0 }}>Pomodoro</div>
      <div className="pomo-tabs" role="tablist">
        {(['work', 'break'] as Phase[]).map((p) => (
          <button
            key={p}
            role="tab"
            aria-selected={phase === p}
            className={`pomo-tab${phase === p ? ' pomo-tab-on' : ''}`}
            onClick={() => switchTo(p)}
            disabled={status === 'running'}
          >
            {PHASE_LABEL[p]} · {durations[p]}′
          </button>
        ))}
      </div>
      <div className={`pomo-time pomo-${phase}`} data-testid="pomo-time">{mmss(left)}</div>
      <div className="bar pomo-bar"><div style={{ width: `${progress * 100}%` }} /></div>
      <div className="wk-meta pomo-task">
        {task ? <>Working on: <span style={{ color: 'var(--tx2)' }}>{task}</span></> : 'Pick an item on the left to attach to this session (optional).'}
      </div>
      <div className="pomo-actions">
        {status === 'running' ? (
          <button className="btn btn-secondary btn-soft" onClick={pause}><i className="ph ph-pause" />Pause</button>
        ) : (
          <button className="btn btn-primary btn-main" onClick={start}>
            <i className="ph ph-play" />{status === 'paused' ? 'Resume' : 'Start'}
          </button>
        )}
        <button className="btn btn-soft" onClick={reset} disabled={status === 'idle' && left === total}>
          <i className="ph ph-arrow-counter-clockwise" />Reset
        </button>
      </div>
      <div aria-live="polite" className="pomo-msg">{message}</div>
      <div className="wk-meta">Focus rounds finished since this page opened: {rounds}</div>

      <div className="section-label">Durations (minutes)</div>
      <div className="pomo-durations">
        <label className="field">
          <span className="wk-meta">Focus</span>
          <input
            className="input input-sm"
            type="number"
            min={LIMITS.work[0]}
            max={LIMITS.work[1]}
            value={durations.work}
            onChange={(e) => setDuration('work', e.target.value)}
            disabled={status !== 'idle'}
            aria-label="Focus minutes"
          />
        </label>
        <label className="field">
          <span className="wk-meta">Break</span>
          <input
            className="input input-sm"
            type="number"
            min={LIMITS.break[0]}
            max={LIMITS.break[1]}
            value={durations.break}
            onChange={(e) => setDuration('break', e.target.value)}
            disabled={status !== 'idle'}
            aria-label="Break minutes"
          />
        </label>
      </div>
      <div className="wk-meta" style={{ marginTop: 8 }}>Just a timer: no history is kept and nothing is sent anywhere.</div>
    </aside>
  );
}
