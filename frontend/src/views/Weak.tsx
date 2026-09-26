/**
 * Weak spots — Mnemosyne `GET /weak_cards`.
 *
 * Every number here comes from the server, including the rule behind them
 * (window and threshold): the same judgement that decides whether a card joins
 * its set's weak-card todo item. Nothing is estimated in the browser, so
 * this screen cannot drift from what the rest of the system believes.
 */
import { useState } from 'react';
import { mnemosyne, type WeakCard, type WeakTask } from '../api/mnemosyne';
import { useApp } from '../state/app';
import { useAsync } from '../lib/useAsync';
import { href } from '../lib/route';
import { doneAgo, relative } from '../lib/time';
import { ErrorNotice, Loading, NeedToken, PageHeader } from '../components/ui';

export function WeakView() {
  const { user } = useApp();
  return (
    <main className="main">
      <PageHeader title="Weak spots" />
      <div className="page">
        <div className="page-inner">{user ? <Weak /> : <NeedToken />}</div>
      </div>
    </main>
  );
}

/** Share of correct answers across a task's cards, over the server's window. */
function recall(cards: WeakCard[]): { correct: number; total: number; pct: number } | null {
  const total = cards.reduce((n, c) => n + c.recent_reviews, 0);
  if (total === 0) return null;
  const wrong = cards.reduce((n, c) => n + c.recent_wrong, 0);
  const correct = total - wrong;
  return { correct, total, pct: Math.round((correct / total) * 100) };
}

function Weak() {
  const { dueCount, stats } = useApp();
  const [includeClosed, setIncludeClosed] = useState(false);
  const q = useAsync(() => mnemosyne.weakCards(includeClosed), [includeClosed]);
  const [open, setOpen] = useState<string>();

  if (q.loading && !q.data) return <Loading label="Loading weak spots…" />;
  if (q.error) return <ErrorNotice error={q.error} onRetry={q.reload} />;
  const data = q.data!;

  return (
    <>
      <div className="page-head">
        <div>
          <h2>
            {data.still_weak_count > 0
              ? `${data.still_weak_count} card${data.still_weak_count === 1 ? '' : 's'} still weak`
              : data.card_count > 0
                ? 'No card is weak any more'
                : 'No weak spots yet'}
          </h2>
          <p>
            A card counts as weak when at least {Math.round(data.error_threshold * 100)}% of its last {data.window} reviews
            were “Again”. It is the same rule Mnemosyne uses to add a “Review weak cards” item to your to-do list.
          </p>
        </div>
        <button className="btn btn-secondary btn-soft" onClick={q.reload}>
          <i className="ph ph-arrow-clockwise" />Reload
        </button>
      </div>

      <div className="stats">
        <div><div className="stat-k">Still weak</div><div className="stat-v" style={{ color: 'var(--red)' }}>{data.still_weak_count}</div></div>
        <div><div className="stat-k">Due</div><div className="stat-v" style={{ color: 'var(--yel)' }}>{stats?.cards.due_now ?? dueCount ?? '—'}</div></div>
        <div>
          <div className="stat-k">Accuracy, {stats?.range_days ?? 14} days</div>
          <div className="stat-v" style={{ color: 'var(--green)' }}>
            {stats?.reviews.accuracy != null ? `${Math.round(stats.reviews.accuracy * 100)}%` : '—'}
          </div>
        </div>
        <div><div className="stat-k">Day streak</div><div className="stat-v" style={{ color: 'var(--frost)' }}>{stats?.streak_days ?? '—'}</div></div>
      </div>

      <div className="toolbar" style={{ marginBottom: 14 }}>
        <label className="radio" style={{ fontSize: 12.5, color: 'var(--mut)' }}>
          <input type="checkbox" checked={includeClosed} onChange={(e) => setIncludeClosed(e.target.checked)} style={{ position: 'static', width: 'auto', height: 'auto', opacity: 1, pointerEvents: 'auto' }} />
          Show finished rounds too
        </label>
      </div>
      <hr className="rule" style={{ marginBottom: 18 }} />

      {data.tasks.length === 0 ? (
        <div className="notice notice-info" style={{ maxWidth: 760 }}>
          <i className="ph ph-check-circle" />
          <div>
            <div className="notice-title">No card has been marked weak</div>
            <div>
              Mnemosyne only judges a card once it has {data.window} reviews. Keep reviewing in Flashcards; this
              fills in by itself when a card keeps being forgotten.
            </div>
          </div>
        </div>
      ) : (
        <div className="list">
          {data.tasks.map((t) => (
            <TaskCard key={t.id} task={t} expanded={open === t.id} onToggle={() => setOpen(open === t.id ? undefined : t.id)} />
          ))}
        </div>
      )}
    </>
  );
}

function TaskCard({ task, expanded, onToggle }: { task: WeakTask; expanded: boolean; onToggle: () => void }) {
  const r = recall(task.cards);
  const closed = task.closed_at !== null;
  const barColor = !r ? 'var(--dim)' : r.pct < 50 ? 'var(--red)' : r.pct < 75 ? 'var(--yel)' : 'var(--green)';

  return (
    <div className="wk">
      <div style={{ minWidth: 0 }}>
        <div className="wk-head">
          <span className="wk-title">{task.study_set_name}</span>
          {closed ? (
            <span className="tag tag-dim tag-sm">{doneAgo(task.closed_at!)}</span>
          ) : task.still_weak_count > 0 ? (
            <span className="tag tag-red tag-sm">{task.still_weak_count} still weak</span>
          ) : (
            <span className="tag tag-green tag-sm">Recovered</span>
          )}
          <span className="wk-meta">{task.cards.length} card{task.cards.length === 1 ? '' : 's'} in this round</span>
        </div>
        <p className="wk-desc">
          Opened {relative(task.opened_at)} · last weak card {relative(task.last_weak_card_at)}.
          {task.still_weak_count === 0 && !closed && ' These cards have recovered but stay listed until you tick off this set’s item in To-do.'}
        </p>
        {r && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 9 }}>
            <div className="bar"><div style={{ width: `${r.pct}%`, background: barColor }} /></div>
            <span style={{ fontSize: 11, color: 'var(--dim)' }}>
              Recalled {r.correct}/{r.total} recent reviews
            </span>
          </div>
        )}
        {expanded && (
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {task.cards.map((c) => (
              <div key={c.card_id} style={{ paddingLeft: 12, borderLeft: '2px solid rgba(216,222,233,.12)' }}>
                <div style={{ fontSize: 13.5, color: 'var(--tx2)', lineHeight: 1.5 }}>{c.question}</div>
                <div className="wk-meta" style={{ marginTop: 3 }}>
                  {c.recent_reviews === 0
                    ? 'Not reviewed since it was listed'
                    : `Missed ${c.recent_wrong}/${c.recent_reviews} recent reviews`}
                  {c.last_reviewed_at && ` · reviewed ${relative(c.last_reviewed_at)}`}
                  {c.still_weak ? (
                    <span className="tag tag-red tag-sm" style={{ marginLeft: 8 }}>weak</span>
                  ) : (
                    <span className="tag tag-green tag-sm" style={{ marginLeft: 8 }}>recovered</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="wk-side">
        <button className="btn btn-soft" onClick={onToggle}>
          <i className={`ph ${expanded ? 'ph-caret-up' : 'ph-caret-down'}`} />
          {expanded ? 'Collapse' : 'Show cards'}
        </button>
        <a className="btn btn-frost" href={href({ view: 'chat', newSetId: task.study_set_id })}>
          <i className="ph ph-student" />Study
        </a>
      </div>
    </div>
  );
}
