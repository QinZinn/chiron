/**
 * Blurting (brain dump) — Mnemosyne `POST /study_sets/{id}/blurting` and
 * its `/history`. A mode of the Teach back screen.
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
            <label htmlFor="blurt-text">Write down everything you remember — no notes, no cards</label>
            <textarea
              id="blurt-text"
              className="input"
              lang="en"
              style={{ minHeight: 200 }}
              placeholder="Concepts, definitions, formulas, examples… Write what comes to mind, in any order, no need for full sentences."
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <button className="btn btn-primary btn-main" onClick={submit} disabled={sending || !text.trim()}>
              {sending ? <><span className="spin" />Chiron is checking…</> : <><i className="ph ph-brain" />Check against the set</>}
            </button>
            <span className="wk-meta">{text.trim().length} characters</span>
          </div>
          {error != null && <ErrorNotice error={error} compact />}
        </div>
      ) : (
        <BlurtingOutcome result={result} text={text} onAgain={again} />
      )}

      <div className="section-label">Earlier attempts · {history.length}</div>
      {historyQ.loading && !historyQ.data && <Loading />}
      {historyQ.error != null && <ErrorNotice error={historyQ.error} onRetry={historyQ.reload} compact />}
      {!historyQ.loading && history.length === 0 && <div className="wk-desc">No attempts for this set yet.</div>}
      <div className="list" style={{ maxWidth: 760 }}>
        {[...history].reverse().map((h) => (
          <div className="wk" key={h.id}>
            <div style={{ minWidth: 0 }}>
              <div className="wk-head">
                <span className="wk-meta">{dateTime(h.created_at)}</span>
                <span className="tag tag-sm tag-green">Remembered {h.remembered}</span>
                <span className="tag tag-sm tag-yel">Missed {h.missing}</span>
                <span className="tag tag-sm tag-red">Wrong {h.wrong}</span>
                {h.cards_considered < h.cards_total && (
                  <span className="wk-meta">checked {h.cards_considered}/{h.cards_total} cards</span>
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
      <div className="section-label" style={{ marginTop: 0 }}>Result</div>
      <div className="fc">
        <div className="stats" style={{ padding: 0 }}>
          <div><div className="stat-k">Remembered</div><div className="stat-v" style={{ color: 'var(--green)' }}>{r.remembered.length}/{total}</div></div>
          <div><div className="stat-k">Missed</div><div className="stat-v" style={{ color: 'var(--yel)' }}>{r.missing.length}</div></div>
          <div><div className="stat-k">Wrong</div><div className="stat-v" style={{ color: 'var(--red)' }}>{r.wrong.length}</div></div>
        </div>
        <hr className="rule" />
        <div className="msg-ai-text">{r.feedback}</div>
        {r.cards_considered < r.cards_total && (
          <div className="wk-meta" style={{ marginTop: 8 }}>
            The set has {r.cards_total} cards; only the first {r.cards_considered} were checked this time (length limit). The
            rest were not scored.
          </div>
        )}
      </div>

      <VerdictGroup title="Wrong" tone="red" cards={r.wrong} showAnswer />
      <VerdictGroup title="Missed" tone="yel" cards={r.missing} showAnswer />
      <VerdictGroup title="Remembered" tone="green" cards={r.remembered} />

      <details className="blurt-own">
        <summary className="wk-meta">What you wrote</summary>
        <div className="msg-ai-text" style={{ whiteSpace: 'pre-wrap' }}>{text}</div>
      </details>
      <button className="btn btn-secondary btn-soft" style={{ marginTop: 14 }} onClick={onAgain}>
        <i className="ph ph-arrow-counter-clockwise" />Start over
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
              {showAnswer && <p className="wk-desc">Answer: {c.answer}</p>}
              {c.note && <p className="wk-desc blurt-note">{c.note}</p>}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
