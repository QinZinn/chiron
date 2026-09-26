/**
 * Blurting (viết ra trí nhớ) — Mnemosyne `POST /study_sets/{id}/blurting` and
 * its `/history`. A mode of the Giảng lại screen.
 *
 * The learner writes everything they remember of a set without looking; the
 * result lists, card by card, what was remembered, missed and got wrong. The
 * cards are deliberately not shown until after submitting.
 */
import { useState } from 'react';
import { mnemosyne, type BlurtingCard, type BlurtingResult } from '../api/mnemosyne';
import { useAsync } from '../lib/useAsync';
import { dateTime } from '../lib/time';
import { ErrorNotice, Loading } from '../components/ui';

export function Blurting({ setId }: { setId: string }) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<unknown>();
  const [result, setResult] = useState<BlurtingResult>();
  const historyQ = useAsync(() => mnemosyne.blurtingHistory(setId), [setId, result], Boolean(setId));

  const submit = async () => {
    if (!text.trim()) return;
    setSending(true);
    setError(undefined);
    try {
      setResult(await mnemosyne.blurting(setId, text));
    } catch (e) {
      setError(e);
    } finally {
      setSending(false);
    }
  };

  const again = () => {
    setResult(undefined);
    setText('');
  };

  const history = historyQ.data?.attempts ?? [];

  return (
    <>
      {!result ? (
        <div className="gen" style={{ maxWidth: 760 }}>
          <div className="field">
            <label htmlFor="blurt-text">Viết ra mọi thứ bạn nhớ — đừng mở vở hay thẻ</label>
            <textarea
              id="blurt-text"
              className="input"
              lang="vi"
              style={{ minHeight: 200 }}
              placeholder="Khái niệm, định nghĩa, công thức, ví dụ… Nhớ gì viết nấy, không cần đúng thứ tự hay câu cú."
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <button className="btn btn-primary btn-main" onClick={submit} disabled={sending || !text.trim()}>
              {sending ? <><span className="spin" />Chiron đang đối chiếu…</> : <><i className="ph ph-brain" />Đối chiếu với bộ thẻ</>}
            </button>
            <span className="wk-meta">{text.trim().length} ký tự</span>
          </div>
          {error != null && <ErrorNotice error={error} compact />}
        </div>
      ) : (
        <BlurtingOutcome result={result} text={text} onAgain={again} />
      )}

      <div className="section-label">Lần viết trước · {history.length}</div>
      {historyQ.loading && !historyQ.data && <Loading />}
      {historyQ.error != null && <ErrorNotice error={historyQ.error} onRetry={historyQ.reload} compact />}
      {!historyQ.loading && history.length === 0 && <div className="wk-desc">Chưa có lần viết nào cho bộ thẻ này.</div>}
      <div className="list" style={{ maxWidth: 760 }}>
        {[...history].reverse().map((h) => (
          <div className="wk" key={h.id}>
            <div style={{ minWidth: 0 }}>
              <div className="wk-head">
                <span className="wk-meta">{dateTime(h.created_at)}</span>
                <span className="tag tag-sm tag-green">Nhớ {h.remembered}</span>
                <span className="tag tag-sm tag-yel">Thiếu {h.missing}</span>
                <span className="tag tag-sm tag-red">Sai {h.wrong}</span>
                {h.cards_considered < h.cards_total && (
                  <span className="wk-meta">đối chiếu {h.cards_considered}/{h.cards_total} thẻ</span>
                )}
              </div>
              <p className="wk-desc clamp2">{h.feedback}</p>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function BlurtingOutcome({ result: r, text, onAgain }: { result: BlurtingResult; text: string; onAgain: () => void }) {
  const total = r.remembered.length + r.missing.length + r.wrong.length;
  return (
    <div style={{ maxWidth: 760 }}>
      <div className="section-label" style={{ marginTop: 0 }}>Kết quả</div>
      <div className="fc">
        <div className="stats" style={{ padding: 0 }}>
          <div><div className="stat-k">Nhớ được</div><div className="stat-v" style={{ color: 'var(--green)' }}>{r.remembered.length}/{total}</div></div>
          <div><div className="stat-k">Bị thiếu</div><div className="stat-v" style={{ color: 'var(--yel)' }}>{r.missing.length}</div></div>
          <div><div className="stat-k">Hiểu sai</div><div className="stat-v" style={{ color: 'var(--red)' }}>{r.wrong.length}</div></div>
        </div>
        <hr className="rule" />
        <div className="msg-ai-text">{r.feedback}</div>
        {r.cards_considered < r.cards_total && (
          <div className="wk-meta" style={{ marginTop: 8 }}>
            Bộ thẻ có {r.cards_total} thẻ; lần này chỉ đối chiếu {r.cards_considered} thẻ đầu (giới hạn độ dài). Các thẻ còn
            lại không được chấm.
          </div>
        )}
      </div>

      <VerdictGroup title="Hiểu sai" tone="red" cards={r.wrong} showAnswer />
      <VerdictGroup title="Bị thiếu" tone="yel" cards={r.missing} showAnswer />
      <VerdictGroup title="Nhớ được" tone="green" cards={r.remembered} />

      <details className="blurt-own">
        <summary className="wk-meta">Bài bạn đã viết</summary>
        <div className="msg-ai-text" style={{ whiteSpace: 'pre-wrap' }}>{text}</div>
      </details>
      <button className="btn btn-secondary btn-soft" style={{ marginTop: 14 }} onClick={onAgain}>
        <i className="ph ph-arrow-counter-clockwise" />Viết lại từ đầu
      </button>
    </div>
  );
}

function VerdictGroup({
  title,
  tone,
  cards,
  showAnswer,
}: {
  title: string;
  tone: 'red' | 'yel' | 'green';
  cards: BlurtingCard[];
  showAnswer?: boolean;
}) {
  if (cards.length === 0) return null;
  return (
    <>
      <div className="section-label">{title} · {cards.length}</div>
      <ul className="list todo-list" aria-label={title}>
        {cards.map((c) => (
          <li key={c.card_id} className={`wk blurt-card blurt-${tone}`}>
            <div style={{ minWidth: 0 }}>
              <div className="wk-title" style={{ fontSize: 14.5 }}>{c.question}</div>
              {showAnswer && <p className="wk-desc">Đáp án: {c.answer}</p>}
              {c.note && <p className="wk-desc blurt-note">{c.note}</p>}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
