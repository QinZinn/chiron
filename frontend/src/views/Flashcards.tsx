/**
 * Thẻ ghi nhớ — Mnemosyne GET /due (the FSRS review queue) and POST /review.
 * Layout follows the design's list screen (1c): header, title + summary,
 * stats row, faded rule, then the content.
 */
import { useEffect, useState } from 'react';
import { mnemosyne, type DueCard, type Rating, type ReviewResponse } from '../api/mnemosyne';
import { useApp } from '../state/app';
import { useAsync } from '../lib/useAsync';
import { relative } from '../lib/time';
import { ErrorNotice, Loading, NeedToken, PageHeader } from '../components/ui';

const RATINGS: { id: Rating; label: string; hint: string; cls: string; key: string }[] = [
  { id: 'again', label: 'Quên', hint: 'không nhớ ra', cls: 'rate-again', key: '1' },
  { id: 'hard', label: 'Khó', hint: 'nhớ nhưng vất vả', cls: 'rate-hard', key: '2' },
  { id: 'good', label: 'Được', hint: 'nhớ đúng', cls: 'rate-good', key: '3' },
  { id: 'easy', label: 'Dễ', hint: 'nhớ ngay', cls: 'rate-easy', key: '4' },
];

export function FlashcardsView() {
  const { user } = useApp();
  return (
    <main className="main">
      <PageHeader title="Thẻ ghi nhớ" />
      <div className="page">
        <div className="page-inner">{user ? <Review /> : <NeedToken />}</div>
      </div>
    </main>
  );
}

function Review() {
  const { studySets, refreshDue, stats } = useApp();
  const dueQ = useAsync(() => mnemosyne.due(100), []);
  const [queue, setQueue] = useState<DueCard[]>([]);
  const [revealed, setRevealed] = useState(false);
  const [rating, setRating] = useState<Rating>();
  const [reviewError, setReviewError] = useState<unknown>();
  const [last, setLast] = useState<{ rating: Rating; res: ReviewResponse }>();
  const [done, setDone] = useState(0);

  useEffect(() => {
    if (dueQ.data) {
      setQueue(dueQ.data.due_cards);
      setRevealed(false);
    }
  }, [dueQ.data]);

  const current = queue[0];
  const setName = (id: string) => studySets?.find((s) => s.id === id)?.name ?? 'Bộ thẻ';

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

  // Space/Enter reveals, 1–4 rates — the usual spaced-repetition keys.
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (!current || e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
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

  if (dueQ.loading && !dueQ.data) return <Loading label="Đang tải hàng đợi ôn tập…" />;
  if (dueQ.error) return <ErrorNotice error={dueQ.error} onRetry={dueQ.reload} />;

  const total = dueQ.data?.count ?? 0;
  const fresh = queue.filter((c) => c.is_new).length;
  const capped = total >= 100;

  return (
    <>
      <div className="page-head">
        <div>
          <h2>{queue.length > 0 ? `${queue.length}${capped && done === 0 ? '+' : ''} thẻ đến hạn` : 'Không còn thẻ đến hạn'}</h2>
          <p>Thẻ mới và thẻ đã tới lịch ôn theo FSRS, lấy từ Mnemosyne. Mỗi lần chấm được ghi lại và dời lịch ôn kế tiếp.</p>
        </div>
        <button className="btn btn-secondary btn-soft" onClick={dueQ.reload}>
          <i className="ph ph-arrow-clockwise" />Tải lại
        </button>
      </div>
      <div className="stats">
        <div><div className="stat-k">Đến hạn</div><div className="stat-v" style={{ color: 'var(--yel)' }}>{queue.length}{capped && done === 0 ? '+' : ''}</div></div>
        <div><div className="stat-k">Thẻ mới</div><div className="stat-v" style={{ color: 'var(--frost)' }}>{fresh}</div></div>
        <div><div className="stat-k">Đã ôn lần này</div><div className="stat-v" style={{ color: 'var(--green)' }}>{done}</div></div>
        <div>
          <div className="stat-k">Độ chính xác {stats?.range_days ?? 14} ngày</div>
          <div className="stat-v" style={{ color: 'var(--green)' }}>
            {stats?.reviews.accuracy != null ? `${Math.round(stats.reviews.accuracy * 100)}%` : '—'}
          </div>
        </div>
        <div><div className="stat-k">Chuỗi ngày học</div><div className="stat-v" style={{ color: 'var(--frost)' }}>{stats?.streak_days ?? '—'}</div></div>
      </div>
      <hr className="rule" style={{ marginBottom: 18 }} />

      {last && (
        <div className="notice notice-info" style={{ marginBottom: 14, maxWidth: 760 }}>
          <i className="ph ph-check-circle" />
          <div>
            Đã ghi “{RATINGS.find((r) => r.id === last.rating)?.label}” — ôn lại sau {last.res.interval_days} ngày
            ({relative(last.res.next_review_at)}).
          </div>
        </div>
      )}

      {current ? (
        <div className="fc">
          <div className="wk-head">
            {current.is_new ? <span className="tag tag-frost tag-sm">Thẻ mới</span> : <span className="tag tag-yel tag-sm">Đến hạn {current.next_review_at ? relative(current.next_review_at) : ''}</span>}
            <span className="wk-meta">{setName(current.set_id)}</span>
          </div>
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
                <i className="ph ph-eye" />Hiện đáp án <span style={{ color: 'var(--dim)', fontSize: 11 }}>Space</span>
              </button>
            </div>
          )}
          {reviewError != null && <ErrorNotice error={reviewError} compact />}
        </div>
      ) : (
        <div className="notice notice-info" style={{ maxWidth: 760 }}>
          <i className="ph ph-confetti" />
          <div>
            <div className="notice-title">Hết thẻ đến hạn</div>
            <div>Thẻ sẽ quay lại đây khi tới lịch ôn. {capped && 'Hàng đợi có thể còn thẻ — bấm Tải lại để lấy tiếp.'}</div>
          </div>
        </div>
      )}

      {queue.length > 1 && (
        <>
          <div className="section-label">Tiếp theo trong hàng đợi · {queue.length - 1}</div>
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
                  {c.is_new ? <span className="tag tag-frost tag-sm">Mới</span> : <span className="wk-meta">{c.next_review_at ? relative(c.next_review_at) : ''}</span>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
