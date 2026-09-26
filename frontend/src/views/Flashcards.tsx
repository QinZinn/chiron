/**
 * Flashcards — Mnemosyne GET /due (the FSRS review queue) and POST /review.
 * Layout follows the design's list screen (1c): header, title + summary,
 * stats row, faded rule, then the content.
 */
import { useEffect, useState } from 'react';
import { mnemosyne, type DueCard, type DueOrder, type Rating, type ReviewResponse } from '../api/mnemosyne';
import { useApp } from '../state/app';
import { useAsync } from '../lib/useAsync';
import { relative } from '../lib/time';
import { ErrorNotice, Loading, NeedToken, PageHeader } from '../components/ui';

const RATINGS: { id: Rating; label: string; hint: string; cls: string; key: string }[] = [
  { id: 'again', label: 'Again', hint: 'could not recall', cls: 'rate-again', key: '1' },
  { id: 'hard', label: 'Hard', hint: 'recalled with effort', cls: 'rate-hard', key: '2' },
  { id: 'good', label: 'Good', hint: 'recalled correctly', cls: 'rate-good', key: '3' },
  { id: 'easy', label: 'Easy', hint: 'recalled instantly', cls: 'rate-easy', key: '4' },
];

const ORDERS: { id: DueOrder; label: string; hint: string }[] = [
  { id: 'due', label: 'By schedule (default)', hint: 'New cards first, then the most overdue.' },
  { id: 'interleave', label: 'Interleave sets', hint: 'Consecutive cards come from different study sets whenever possible.' },
  { id: 'hardest', label: 'Hardest first', hint: 'Cards missed most in their last 5 reviews come first (Eat That Frog).' },
];
const ORDER_KEY = 'chiron.dueOrder';

function loadOrder(): DueOrder {
  try {
    const v = localStorage.getItem(ORDER_KEY);
    return ORDERS.some((o) => o.id === v) ? (v as DueOrder) : 'due';
  } catch {
    return 'due';
  }
}

function saveOrder(order: DueOrder) {
  try {
    localStorage.setItem(ORDER_KEY, order);
  } catch {
    // Storage blocked: the choice lasts until the page reloads.
  }
}

export function FlashcardsView() {
  const { user } = useApp();
  return (
    <main className="main">
      <PageHeader title="Flashcards" />
      <div className="page">
        <div className="page-inner">{user ? <Review /> : <NeedToken />}</div>
      </div>
    </main>
  );
}

function Review() {
  const { studySets, refreshDue, stats } = useApp();
  const [order, setOrder] = useState<DueOrder>(loadOrder);
  const dueQ = useAsync(() => mnemosyne.due(100, order), [order]);
  const [queue, setQueue] = useState<DueCard[]>([]);
  const [revealed, setRevealed] = useState(false);
  const [rating, setRating] = useState<Rating>();
  const [reviewError, setReviewError] = useState<unknown>();
  const [last, setLast] = useState<{ rating: Rating; res: ReviewResponse }>();
  const [done, setDone] = useState(0);
  // Editing exists because these cards are often LLM-written: a wrong answer
  // used to need psql to fix.
  const [edit, setEdit] = useState<{ question: string; answer: string }>();
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    if (dueQ.data) {
      setQueue(dueQ.data.due_cards);
      setRevealed(false);
    }
  }, [dueQ.data]);

  const current = queue[0];
  const setName = (id: string) => studySets?.find((s) => s.id === id)?.name ?? 'Study set';

  const rate = async (r: Rating) => {
    if (!current || rating) return;
    setRating(r);
    setReviewError(undefined);
    try {
      const res = await mnemosyne.review(current.card_id, r);
      setLast({ rating: r, res });
      setQueue((q) => q.slice(1));
      setRevealed(false);
      setDone((n) => n + 1);
      refreshDue();
    } catch (e) {
      setReviewError(e);
    } finally {
      setRating(undefined);
    }
  };

  const saveEdit = async () => {
    if (!current || !edit) return;
    setSaving(true);
    setReviewError(undefined);
    try {
      const res = await mnemosyne.patchCard(current.card_id, { question: edit.question, answer: edit.answer });
      setQueue((q) => q.map((c) => (c.card_id === current.card_id ? { ...c, question: res.question, answer: res.answer } : c)));
      setEdit(undefined);
    } catch (e) {
      setReviewError(e);
    } finally {
      setSaving(false);
    }
  };

  const removeCard = async () => {
    if (!current) return;
    setSaving(true);
    setReviewError(undefined);
    try {
      await mnemosyne.deleteCard(current.card_id);
      setQueue((q) => q.slice(1));
      setRevealed(false);
      setConfirmDelete(false);
      refreshDue();
    } catch (e) {
      setReviewError(e);
    } finally {
      setSaving(false);
    }
  };

  // Space/Enter reveals, 1–4 rates — the usual spaced-repetition keys.
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (!current || edit || e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (!revealed && (e.key === ' ' || e.key === 'Enter')) {
        e.preventDefault();
        setRevealed(true);
      } else if (revealed) {
        const r = RATINGS.find((x) => x.key === e.key);
        if (r) void rate(r.id);
      }
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  });

  if (dueQ.loading && !dueQ.data) return <Loading label="Loading the review queue…" />;
  if (dueQ.error) return <ErrorNotice error={dueQ.error} onRetry={dueQ.reload} />;

  const total = dueQ.data?.count ?? 0;
  const fresh = queue.filter((c) => c.is_new).length;
  const capped = total >= 100;

  return (
    <>
      <div className="page-head">
        <div>
          <h2>{queue.length > 0 ? `${queue.length}${capped && done === 0 ? '+' : ''} card${queue.length === 1 ? '' : 's'} due` : 'Nothing due'}</h2>
          <p>New cards and cards whose FSRS review date has come, from Mnemosyne. Every rating is recorded and moves the next review.</p>
        </div>
        <button className="btn btn-secondary btn-soft" onClick={dueQ.reload}>
          <i className="ph ph-arrow-clockwise" />Reload
        </button>
      </div>
      <div className="toolbar due-order">
        <label htmlFor="due-order" className="wk-meta">Review order</label>
        <select
          id="due-order"
          className="input"
          value={order}
          onChange={(e) => {
            const next = e.target.value as DueOrder;
            setOrder(next);
            saveOrder(next);
          }}
        >
          {ORDERS.map((o) => (
            <option key={o.id} value={o.id}>{o.label}</option>
          ))}
        </select>
        <span className="wk-meta">{ORDERS.find((o) => o.id === order)?.hint}</span>
      </div>
      <div className="stats">
        <div><div className="stat-k">Due</div><div className="stat-v" style={{ color: 'var(--yel)' }}>{queue.length}{capped && done === 0 ? '+' : ''}</div></div>
        <div><div className="stat-k">New</div><div className="stat-v" style={{ color: 'var(--frost)' }}>{fresh}</div></div>
        <div><div className="stat-k">Reviewed now</div><div className="stat-v" style={{ color: 'var(--green)' }}>{done}</div></div>
        <div>
          <div className="stat-k">Accuracy, {stats?.range_days ?? 14} days</div>
          <div className="stat-v" style={{ color: 'var(--green)' }}>
            {stats?.reviews.accuracy != null ? `${Math.round(stats.reviews.accuracy * 100)}%` : '—'}
          </div>
        </div>
        <div><div className="stat-k">Day streak</div><div className="stat-v" style={{ color: 'var(--frost)' }}>{stats?.streak_days ?? '—'}</div></div>
      </div>
      <hr className="rule" style={{ marginBottom: 18 }} />

      {last && (
        <div className="notice notice-info" style={{ marginBottom: 14, maxWidth: 760 }}>
          <i className="ph ph-check-circle" />
          <div>
            Recorded “{RATINGS.find((r) => r.id === last.rating)?.label}” — next review in {last.res.interval_days} day{last.res.interval_days === 1 ? '' : 's'}
            ({relative(last.res.next_review_at)}).
          </div>
        </div>
      )}

      {current && edit ? (
        <div className="fc">
          <div className="wk-head">
            <span className="tag tag-frost tag-sm">Editing card</span>
            <span className="wk-meta">{setName(current.set_id)}</span>
          </div>
          <div className="field">
            <label>Question</label>
            <textarea className="input" lang="en" value={edit.question} onChange={(e) => setEdit({ ...edit, question: e.target.value })} />
          </div>
          <div className="field">
            <label>Answer</label>
            <textarea className="input" lang="en" value={edit.answer} onChange={(e) => setEdit({ ...edit, answer: e.target.value })} />
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary btn-main" onClick={saveEdit} disabled={saving || !edit.question.trim() || !edit.answer.trim()}>
              {saving ? <span className="spin" /> : <i className="ph ph-check" />}Save changes
            </button>
            <button className="btn btn-soft" onClick={() => setEdit(undefined)} disabled={saving}>Cancel</button>
          </div>
          {reviewError != null && <ErrorNotice error={reviewError} compact />}
        </div>
      ) : current ? (
        <div className="fc">
          <div className="wk-head">
            {current.is_new ? <span className="tag tag-frost tag-sm">New card</span> : <span className="tag tag-yel tag-sm">Due {current.next_review_at ? relative(current.next_review_at) : ''}</span>}
            <span className="wk-meta">{setName(current.set_id)}</span>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              <button className="icon-btn" title="Edit this card" onClick={() => setEdit({ question: current.question, answer: current.answer })}>
                <i className="ph ph-pencil-simple" />
              </button>
              <button className="icon-btn" title="Delete this card" onClick={() => setConfirmDelete(true)}>
                <i className="ph ph-trash" />
              </button>
            </div>
          </div>
          {confirmDelete && (
            <div className="notice notice-warn">
              <i className="ph ph-warning" />
              <div>
                <div className="notice-title">Delete this card?</div>
                <div>Its review history is deleted with it and cannot be recovered.</div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn btn-soft rate-again" onClick={removeCard} disabled={saving}>
                    {saving ? <span className="spin" /> : <i className="ph ph-trash" />}Delete
                  </button>
                  <button className="btn btn-soft" onClick={() => setConfirmDelete(false)} disabled={saving}>Keep</button>
                </div>
              </div>
            </div>
          )}
          <div className="fc-q">{current.question}</div>
          {revealed ? (
            <>
              <hr className="rule" />
              <div className="fc-a" style={{ paddingTop: 0 }}>{current.answer}</div>
              <div className="rate-row">
                {RATINGS.map((r) => (
                  <button key={r.id} className={`btn rate ${r.cls}`} disabled={Boolean(rating)} onClick={() => rate(r.id)}>
                    {rating === r.id ? <span className="spin" /> : r.label}
                    <small>{r.key} · {r.hint}</small>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <div>
              <button className="btn btn-primary btn-main" onClick={() => setRevealed(true)}>
                <i className="ph ph-eye" />Show answer <span style={{ color: 'var(--dim)', fontSize: 11 }}>Space</span>
              </button>
            </div>
          )}
          {reviewError != null && <ErrorNotice error={reviewError} compact />}
        </div>
      ) : (
        <div className="notice notice-info" style={{ maxWidth: 760 }}>
          <i className="ph ph-confetti" />
          <div>
            <div className="notice-title">All caught up</div>
            <div>Cards come back here when they are due. {capped && 'There may be more in the queue — press Reload to fetch them.'}</div>
          </div>
        </div>
      )}

      {queue.length > 1 && (
        <>
          <div className="section-label">Up next · {queue.length - 1}</div>
          <div className="list" style={{ maxWidth: 760 }}>
            {queue.slice(1, 30).map((c) => (
              <div className="wk" key={c.card_id}>
                <div>
                  <div className="wk-head">
                    <span className="wk-title clamp2" style={{ fontSize: 14.5 }}>{c.question}</span>
                  </div>
                  <span className="wk-meta">{setName(c.set_id)}</span>
                </div>
                <div className="wk-side">
                  {c.is_new ? <span className="tag tag-frost tag-sm">New</span> : <span className="wk-meta">{c.next_review_at ? relative(c.next_review_at) : ''}</span>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
