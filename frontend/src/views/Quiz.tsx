/**
 * Quiz — Mnemosyne GET /quiz/{set_id}, POST /quiz/generate,
 * POST /quiz/{question_id}/attempt. The correct answer never reaches the
 * browser before the learner commits: list/generate responses carry no
 * correct_index (enforced in quiz.rs); only the attempt response reveals it.
 */
import { useEffect, useState } from 'react';
import { mnemosyne, QUIZ_MAX_COUNT, type QuizQuestion } from '../api/mnemosyne';
import { useApp } from '../state/app';
import { useAsync } from '../lib/useAsync';
import { ErrorNotice, Loading, NeedToken, PageHeader } from '../components/ui';

type Attempt =
  | { state: 'sending'; selected: number }
  | { state: 'done'; selected: number; isCorrect: boolean; correctIndex: number }
  | { state: 'error'; selected: number; error: unknown };

const KEYS = 'ABCDEF';

export function QuizView() {
  const { user } = useApp();
  return (
    <main className="main">
      <PageHeader title="Quiz" />
      <div className="page">
        <div className="page-inner">{user ? <QuizBody /> : <NeedToken />}</div>
      </div>
    </main>
  );
}

function QuizBody() {
  const { studySets, studySetsError, reloadSets } = useApp();
  const [setId, setSetId] = useState('');
  useEffect(() => {
    if (!setId && studySets?.length) setSetId(studySets[0].id);
  }, [studySets, setId]);

  const listQ = useAsync(() => mnemosyne.quizList(setId), [setId], Boolean(setId));
  const [questions, setQuestions] = useState<QuizQuestion[]>([]);
  useEffect(() => {
    setQuestions(listQ.data?.questions ?? []);
  }, [listQ.data]);

  const [attempts, setAttempts] = useState<Record<string, Attempt>>({});
  useEffect(() => setAttempts({}), [setId]);

  // ── generation form
  const [source, setSource] = useState<'topic' | 'knowledge_store'>('topic');
  const [topic, setTopic] = useState('');
  const [subject, setSubject] = useState('');
  const [count, setCount] = useState(5);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<unknown>();
  const [genInfo, setGenInfo] = useState<string>();

  if (studySetsError) return <ErrorNotice error={studySetsError} onRetry={reloadSets} />;
  if (!studySets) return <Loading label="Loading study sets…" />;
  if (studySets.length === 0) {
    return (
      <div className="notice notice-info">
        <i className="ph ph-info" />
        <div>This learner has no study set in Mnemosyne yet — a quiz belongs to a study set.</div>
      </div>
    );
  }

  const generate = async () => {
    setGenerating(true);
    setGenError(undefined);
    setGenInfo(undefined);
    try {
      const res = await mnemosyne.quizGenerate({
        study_set_id: setId,
        source,
        topic: source === 'topic' ? topic.trim() : undefined,
        subject_filter: source === 'knowledge_store' && subject.trim() ? subject.trim() : undefined,
        count,
      });
      setQuestions((q) => [...res.questions, ...q.filter((x) => !res.questions.some((n) => n.id === x.id))]);
      setGenInfo(`Created ${res.questions.length} question${res.questions.length === 1 ? '' : 's'} · ${res.tokens_used.toLocaleString('en-US')} tokens`);
    } catch (e) {
      setGenError(e);
    } finally {
      setGenerating(false);
    }
  };

  const answer = async (q: QuizQuestion, idx: number) => {
    const cur = attempts[q.id];
    if (cur && cur.state !== 'error') return;
    setAttempts((a) => ({ ...a, [q.id]: { state: 'sending', selected: idx } }));
    try {
      const r = await mnemosyne.quizAttempt(q.id, idx);
      setAttempts((a) => ({ ...a, [q.id]: { state: 'done', selected: idx, isCorrect: r.is_correct, correctIndex: r.correct_index } }));
    } catch (e) {
      setAttempts((a) => ({ ...a, [q.id]: { state: 'error', selected: idx, error: e } }));
    }
  };

  const removeQuestion = async (id: string) => {
    try {
      await mnemosyne.deleteQuizQuestion(id);
      setQuestions((qs) => qs.filter((q) => q.id !== id));
    } catch (e) {
      setGenError(e);
    }
  };

  const doneList = Object.values(attempts).filter((a): a is Extract<Attempt, { state: 'done' }> => a.state === 'done');
  const correct = doneList.filter((a) => a.isCorrect).length;
  const canGenerate = !generating && count >= 1 && count <= QUIZ_MAX_COUNT && (source === 'knowledge_store' || topic.trim().length > 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Multiple-choice quiz</h2>
          <p>Questions belong to a Mnemosyne study set. Grading needs no LLM — the answer is revealed only after you pick one.</p>
        </div>
      </div>
      <div className="toolbar" style={{ marginTop: 14 }}>
        <select className="input" style={{ width: 'auto', minWidth: 240 }} value={setId} onChange={(e) => setSetId(e.target.value)}>
          {studySets.map((s) => (
            <option key={s.id} value={s.id}>{s.name}{s.topic ? ` · ${s.topic}` : ''}</option>
          ))}
        </select>
        <button className="btn btn-secondary btn-soft" onClick={listQ.reload}>
          <i className="ph ph-arrow-clockwise" />Reload
        </button>
      </div>
      <div className="stats">
        <div><div className="stat-k">Questions</div><div className="stat-v" style={{ color: 'var(--frost)' }}>{questions.length}</div></div>
        <div><div className="stat-k">Answered</div><div className="stat-v">{doneList.length}</div></div>
        <div><div className="stat-k">Correct</div><div className="stat-v" style={{ color: 'var(--green)' }}>{doneList.length ? `${correct}/${doneList.length}` : '—'}</div></div>
      </div>
      <hr className="rule" style={{ marginBottom: 18 }} />

      <div className="gen">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 14, color: 'var(--tx2)' }}>Generate new questions</span>
          <div className="seg" role="radiogroup">
            <label className="seg-opt">
              <input type="radio" checked={source === 'topic'} onChange={() => setSource('topic')} />
              <i className="ph ph-text-aa" />From a topic
            </label>
            <label className="seg-opt">
              <input type="radio" checked={source === 'knowledge_store'} onChange={() => setSource('knowledge_store')} />
              <i className="ph ph-graph" />From the Knowledge Store
            </label>
          </div>
        </div>
        {source === 'topic' ? (
          <div className="field">
            <label>Topic or a passage of material</label>
            <textarea className="input" lang="en" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. Faraday's law of electromagnetic induction and Lenz's law" maxLength={8000} />
          </div>
        ) : (
          <p className="wk-desc">
            Mnemosyne writes questions from the concepts you have learned in the Knowledge Store (needs <code>KS_HTTP_TOKEN</code> in <code>mnemosyne/.env</code>).
          </p>
        )}
        <div className="gen-row">
          {source === 'knowledge_store' && (
            <div className="field">
              <label>Filter by subject (optional)</label>
              <input className="input input-sm" lang="en" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. Physics" />
            </div>
          )}
          <div className="field" style={{ flex: 'none', minWidth: 0, width: 120 }}>
            <label>Number of questions (1–{QUIZ_MAX_COUNT})</label>
            <input className="input input-sm" type="number" min={1} max={QUIZ_MAX_COUNT} value={count} onChange={(e) => setCount(Number(e.target.value))} />
          </div>
          <button className="btn btn-primary btn-main" onClick={generate} disabled={!canGenerate}>
            {generating ? <><span className="spin" />Generating… (can take up to a minute)</> : <><i className="ph ph-sparkle" />Generate questions</>}
          </button>
        </div>
        {genInfo && <div className="wk-meta" style={{ color: 'var(--green)' }}>{genInfo}</div>}
        {genError != null && <ErrorNotice error={genError} compact />}
      </div>

      <div className="section-label">Questions in this set</div>
      {listQ.loading && !listQ.data && <Loading label="Loading questions…" />}
      {listQ.error != null && <ErrorNotice error={listQ.error} onRetry={listQ.reload} />}
      {!listQ.loading && !listQ.error && questions.length === 0 && (
        <div className="wk-desc">This set has no quiz questions yet. Generate a few above to start.</div>
      )}
      <div className="list">
        {questions.map((q, n) => {
          const a = attempts[q.id];
          return (
            <div className="q-card" key={q.id}>
              <div className="wk-head">
                <span className="wk-meta">Question {n + 1}</span>
                <span className={`tag tag-sm ${q.source === 'knowledge_store' ? 'tag-pur' : 'tag-dim'}`}>
                  {q.source === 'knowledge_store' ? 'From the Knowledge Store' : 'From a topic'}
                </span>
                <button
                  className="icon-btn"
                  style={{ marginLeft: 'auto' }}
                  title="Delete this question"
                  onClick={() => removeQuestion(q.id)}
                >
                  <i className="ph ph-trash" />
                </button>
              </div>
              <div className="q-text">{q.question}</div>
              <div className="choices">
                {q.choices.map((c, i) => {
                  let cls = 'choice';
                  if (a?.state === 'done') {
                    if (i === a.correctIndex) cls += ' choice-correct';
                    else if (i === a.selected) cls += ' choice-wrong';
                  }
                  const locked = Boolean(a && a.state !== 'error');
                  return (
                    <button key={i} className={cls} disabled={locked} onClick={() => answer(q, i)}>
                      <span className="choice-key">{a?.state === 'sending' && a.selected === i ? <span className="spin" /> : KEYS[i]}</span>
                      <span>{c}</span>
                    </button>
                  );
                })}
              </div>
              {a?.state === 'done' && (
                <div className="q-result" style={{ color: a.isCorrect ? 'var(--green)' : 'var(--red)' }}>
                  {a.isCorrect ? 'Correct.' : `Not quite — the answer is ${KEYS[a.correctIndex]}.`}
                </div>
              )}
              {a?.state === 'error' && <div style={{ marginTop: 10 }}><ErrorNotice error={a.error} compact /></div>}
            </div>
          );
        })}
      </div>
    </>
  );
}
