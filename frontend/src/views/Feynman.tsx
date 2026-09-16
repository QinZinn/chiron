/**
 * Giảng lại (phương pháp Feynman) — Mnemosyne
 * `POST /study_sets/{id}/feynman_evaluate` and its `/history`.
 *
 * The fourth methodology Mnemosyne implements, and the one that had a working
 * backend and no screen at all: the learner explains a topic in their own
 * words and the tutor scores clarity, completeness and correctness.
 */
import { useState } from 'react';
import { mnemosyne, type FeynmanEvaluation } from '../api/mnemosyne';
import { useApp } from '../state/app';
import { useAsync } from '../lib/useAsync';
import { dateTime } from '../lib/time';
import { ErrorNotice, Loading, NeedToken, PageHeader } from '../components/ui';

const SCORES: { key: 'clarity_score' | 'completeness_score' | 'correctness_score'; label: string; hint: string }[] = [
  { key: 'clarity_score', label: 'Rõ ràng', hint: 'Người chưa biết có hiểu được không' },
  { key: 'completeness_score', label: 'Đầy đủ', hint: 'Có bỏ sót ý chính nào không' },
  { key: 'correctness_score', label: 'Chính xác', hint: 'Có chỗ nào sai kiến thức không' },
];

function scoreColor(n: number): string {
  if (n >= 8) return 'var(--green)';
  if (n >= 5) return 'var(--yel)';
  return 'var(--red)';
}

export function FeynmanView() {
  const { user } = useApp();
  return (
    <main className="main">
      <PageHeader title="Giảng lại" />
      <div className="page">
        <div className="page-inner">{user ? <Feynman /> : <NeedToken />}</div>
      </div>
    </main>
  );
}

function Feynman() {
  const { studySets, studySetsError, reloadSets } = useApp();
  const [setId, setSetId] = useState('');
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<unknown>();
  const [result, setResult] = useState<FeynmanEvaluation>();

  const effectiveSet = setId || studySets?.[0]?.id || '';
  const historyQ = useAsync(() => mnemosyne.feynmanHistory(effectiveSet), [effectiveSet, result], Boolean(effectiveSet));

  if (studySetsError) return <ErrorNotice error={studySetsError} onRetry={reloadSets} />;
  if (!studySets) return <Loading label="Đang tải bộ thẻ…" />;
  if (studySets.length === 0) {
    return (
      <div className="notice notice-info">
        <i className="ph ph-info" />
        <div>Giảng lại chấm điểm dựa trên thẻ của một bộ thẻ. Người học này chưa có bộ thẻ nào.</div>
      </div>
    );
  }

  const submit = async () => {
    if (!text.trim() || !effectiveSet) return;
    setSending(true);
    setError(undefined);
    try {
      setResult(await mnemosyne.feynmanEvaluate(effectiveSet, text));
    } catch (e) {
      setError(e);
    } finally {
      setSending(false);
    }
  };

  const history = historyQ.data?.evaluations ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Giảng lại bằng lời của bạn</h2>
          <p>
            Phương pháp Feynman: giải thích chủ đề như đang dạy cho người chưa biết. Chiron chấm ba mặt — rõ ràng,
            đầy đủ, chính xác — dựa trên các thẻ trong bộ thẻ bạn chọn.
          </p>
        </div>
      </div>

      <div className="toolbar" style={{ marginTop: 10, marginBottom: 16 }}>
        <select className="input" style={{ width: 'auto', minWidth: 240 }} value={effectiveSet} onChange={(e) => { setSetId(e.target.value); setResult(undefined); }}>
          {studySets.map((s) => (
            <option key={s.id} value={s.id}>{s.name}{s.topic ? ` · ${s.topic}` : ''}</option>
          ))}
        </select>
      </div>

      <div className="gen" style={{ maxWidth: 760 }}>
        <div className="field">
          <label>Bài giảng của bạn</label>
          <textarea
            className="input"
            lang="vi"
            style={{ minHeight: 160 }}
            placeholder="Giải thích chủ đề này như thể người nghe chưa biết gì: định nghĩa, vì sao đúng, ví dụ…"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button className="btn btn-primary btn-main" onClick={submit} disabled={sending || !text.trim()}>
            {sending ? <><span className="spin" />Chiron đang đọc…</> : <><i className="ph ph-chalkboard-teacher" />Chấm bài giảng</>}
          </button>
          <span className="wk-meta">{text.trim().length} ký tự</span>
        </div>
        {error != null && <ErrorNotice error={error} compact />}
      </div>

      {result && (
        <>
          <div className="section-label">Kết quả</div>
          <div className="fc" style={{ maxWidth: 760 }}>
            <div className="stats" style={{ padding: 0 }}>
              {SCORES.map((s) => (
                <div key={s.key}>
                  <div className="stat-k" title={s.hint}>{s.label}</div>
                  <div className="stat-v" style={{ color: scoreColor(result[s.key]) }}>{result[s.key]}/10</div>
                </div>
              ))}
            </div>
            <hr className="rule" />
            <div>
              <div className="section-label" style={{ margin: '0 0 6px' }}>Nhận xét</div>
              <div className="msg-ai-text">{result.feedback}</div>
            </div>
            <div>
              <div className="section-label" style={{ margin: '10px 0 6px' }}>Gợi ý cải thiện</div>
              <div className="msg-ai-text">{result.suggestions}</div>
            </div>
          </div>
        </>
      )}

      <div className="section-label">Lần giảng trước · {history.length}</div>
      {historyQ.loading && !historyQ.data && <Loading />}
      {historyQ.error != null && <ErrorNotice error={historyQ.error} onRetry={historyQ.reload} compact />}
      {!historyQ.loading && history.length === 0 && (
        <div className="wk-desc">Chưa có lần giảng nào cho bộ thẻ này.</div>
      )}
      <div className="list" style={{ maxWidth: 760 }}>
        {[...history].reverse().map((h) => (
          <div className="wk" key={h.id}>
            <div style={{ minWidth: 0 }}>
              <div className="wk-head">
                <span className="wk-meta">{dateTime(h.created_at)}</span>
                {SCORES.map((s) => (
                  <span key={s.key} className="tag tag-sm tag-dim" style={{ color: scoreColor(h[s.key]) }}>
                    {s.label} {h[s.key]}
                  </span>
                ))}
              </div>
              <p className="wk-desc clamp2">{h.explanation_text}</p>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
