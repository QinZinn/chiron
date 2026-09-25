/**
 * Việc cần ôn — Mnemosyne `/todos`, plus a Pomodoro timer.
 *
 * A plain list the learner works through in their own order. Weak-card items
 * are opened by Mnemosyne when a set's cards keep being failed (one open item
 * per set); the learner adds the rest by hand. Nothing here schedules or
 * orders by deadline — ticking an item off is the only state change.
 */
import { useState, type FormEvent } from 'react';
import { mnemosyne, type Todo } from '../api/mnemosyne';
import { useApp } from '../state/app';
import { useAsync } from '../lib/useAsync';
import { href } from '../lib/route';
import { doneAgo, relative } from '../lib/time';
import { ErrorNotice, Loading, NeedToken, PageHeader } from '../components/ui';
import { Pomodoro } from '../components/Pomodoro';

export function TodosView() {
  const { user } = useApp();
  return (
    <main className="main">
      <PageHeader title="Việc cần ôn" />
      <div className="page">
        <div className="page-inner">{user ? <Todos /> : <NeedToken />}</div>
      </div>
    </main>
  );
}

const MAX_TITLE = 200;

function Todos() {
  const { studySets, refreshTodos } = useApp();
  const q = useAsync(() => mnemosyne.todos(), []);
  const [title, setTitle] = useState('');
  const [setId, setSetId] = useState('');
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<unknown>();
  const [busy, setBusy] = useState<string>();
  const [actionError, setActionError] = useState<unknown>();
  const [showDone, setShowDone] = useState(false);
  const [current, setCurrent] = useState<string>();

  const reload = () => {
    q.reload();
    refreshTodos();
  };

  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    setAdding(true);
    setAddError(undefined);
    try {
      await mnemosyne.createTodo(title.trim(), setId || undefined);
      setTitle('');
      reload();
    } catch (err) {
      setAddError(err);
    } finally {
      setAdding(false);
    }
  };

  const complete = async (t: Todo) => {
    setBusy(t.id);
    setActionError(undefined);
    try {
      await mnemosyne.completeTodo(t.id);
      if (current === t.id) setCurrent(undefined);
      reload();
    } catch (err) {
      setActionError(err);
    } finally {
      setBusy(undefined);
    }
  };

  if (q.loading && !q.data) return <Loading label="Đang tải danh sách…" />;
  if (q.error) return <ErrorNotice error={q.error} onRetry={q.reload} />;
  const all = q.data!.todos;
  const open = all.filter((t) => !t.done);
  const done = all.filter((t) => t.done);
  const currentTitle = open.find((t) => t.id === current);

  return (
    <>
      <div className="page-head">
        <div>
          <h2>{open.length > 0 ? `${open.length} việc chưa xong` : 'Không còn việc nào'}</h2>
          <p>
            Danh sách việc cần ôn, làm theo thứ tự bạn muốn. Khi một bộ thẻ có thẻ liên tục bị quên, Mnemosyne tự thêm
            việc “Ôn lại các thẻ đang yếu” cho bộ đó (mỗi bộ tối đa một việc chưa xong). Chiron không tự xếp giờ.
          </p>
        </div>
        <button className="btn btn-secondary btn-soft" onClick={q.reload}>
          <i className="ph ph-arrow-clockwise" />Tải lại
        </button>
      </div>
      <hr className="rule" style={{ margin: '0 0 18px' }} />

      <div className="note-grid">
        <div style={{ minWidth: 0 }}>
          <form className="gen todo-add" onSubmit={add}>
            <div className="gen-row">
              <div className="field">
                <label htmlFor="todo-title">Thêm việc</label>
                <input
                  id="todo-title"
                  className="input input-sm"
                  lang="vi"
                  placeholder="Ví dụ: Làm lại đề cương chương 3"
                  value={title}
                  maxLength={MAX_TITLE}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={adding}
                />
              </div>
              <div className="field" style={{ flex: '0 1 220px' }}>
                <label htmlFor="todo-set">Bộ thẻ (tuỳ chọn)</label>
                <select id="todo-set" className="input" value={setId} onChange={(e) => setSetId(e.target.value)} disabled={adding}>
                  <option value="">Không gắn bộ thẻ</option>
                  {(studySets ?? []).map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </div>
              <button className="btn btn-primary btn-main" type="submit" disabled={adding || !title.trim()}>
                {adding ? <span className="spin" /> : <i className="ph ph-plus" />}Thêm
              </button>
            </div>
            {addError !== undefined && <ErrorNotice error={addError} compact />}
          </form>

          {actionError !== undefined && <ErrorNotice error={actionError} compact />}

          <div className="section-label">Chưa xong · {open.length}</div>
          {open.length === 0 ? (
            <div className="wk-desc">Chưa có việc nào. Thêm việc ở trên, hoặc ôn thẻ ở Thẻ ghi nhớ — thẻ nào bị quên nhiều sẽ tự thành một việc ở đây.</div>
          ) : (
            <ul className="list todo-list" aria-label="Việc chưa xong">
              {open.map((t) => (
                <TodoRow
                  key={t.id}
                  todo={t}
                  busy={busy === t.id}
                  current={current === t.id}
                  onComplete={() => complete(t)}
                  onFocus={() => setCurrent(current === t.id ? undefined : t.id)}
                />
              ))}
            </ul>
          )}

          {done.length > 0 && (
            <>
              <button className="btn btn-soft" style={{ marginTop: 18 }} onClick={() => setShowDone(!showDone)} aria-expanded={showDone}>
                <i className={`ph ${showDone ? 'ph-caret-down' : 'ph-caret-right'}`} />Đã xong · {done.length}
              </button>
              {showDone && (
                <ul className="list todo-list" aria-label="Việc đã xong" style={{ marginTop: 10 }}>
                  {done.map((t) => <TodoRow key={t.id} todo={t} />)}
                </ul>
              )}
            </>
          )}
        </div>

        <Pomodoro task={currentTitle ? label(currentTitle) : undefined} />
      </div>
    </>
  );
}

function addedAgo(iso: string): string {
  const r = relative(iso);
  return r === 'vừa xong' ? 'Vừa thêm' : `Thêm ${r}`;
}

function label(t: Todo): string {
  return t.study_set_name ? `${t.title} · ${t.study_set_name}` : t.title;
}

function TodoRow({
  todo: t,
  busy,
  current,
  onComplete,
  onFocus,
}: {
  todo: Todo;
  busy?: boolean;
  current?: boolean;
  onComplete?: () => void;
  onFocus?: () => void;
}) {
  const weak = t.source === 'weak_card';
  return (
    <li className={`wk todo${t.done ? ' wk-dim' : ''}${current ? ' todo-current' : ''}`}>
      <div style={{ minWidth: 0 }}>
        <div className="wk-head">
          <span className="wk-title" style={{ fontSize: 15 }}>{t.title}</span>
          {t.study_set_name && <span className="wk-meta">{t.study_set_name}</span>}
          {weak ? <span className="tag tag-red tag-sm">Thẻ yếu · {t.card_count} thẻ</span> : <span className="tag tag-dim tag-sm">Tự thêm</span>}
        </div>
        <p className="wk-desc">
          {t.done
            ? doneAgo(t.done_at!)
            : weak && t.last_weak_card_at
              ? `${addedAgo(t.created_at)} · thẻ yếu gần nhất ${relative(t.last_weak_card_at)}`
              : addedAgo(t.created_at)}
        </p>
        {weak && !t.done && t.study_set_id && (
          <div className="todo-links">
            <a href={href({ view: 'weak' })}>Xem thẻ yếu</a>
            <a href={href({ view: 'flashcards' })}>Ôn ở Thẻ ghi nhớ</a>
            <a href={href({ view: 'chat', newSetId: t.study_set_id })}>Học lại với Socratic</a>
          </div>
        )}
      </div>
      {!t.done && (
        <div className="wk-side">
          <button className="btn btn-frost" onClick={onComplete} disabled={busy}>
            {busy ? <span className="spin" /> : <i className="ph ph-check" />}Xong
          </button>
          <button className={`btn btn-soft${current ? ' btn-on' : ''}`} onClick={onFocus} aria-pressed={current} title="Gắn với đồng hồ Pomodoro">
            <i className="ph ph-timer" />{current ? 'Đang làm' : 'Làm việc này'}
          </button>
        </div>
      )}
    </li>
  );
}
