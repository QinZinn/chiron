/**
 * Note scan — image/PDF → OCR (PaddleOCR) → correct the text → extract concepts → review.
 *
 * Two checkpoints on purpose, and the UI keeps them visible as separate steps:
 * the learner corrects the OCR text before any LLM reads it, then decides on
 * every extracted concept before it becomes a node. Nothing here writes to the
 * Knowledge Store graph directly — `accept` is the only way in, and it goes
 * through KS's duplicate detection.
 */
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { ApiError } from '../api/http';
import { ks, type AcceptDecision, type AcceptResult, type Note, type OcrPage, type ReviewConcept } from '../api/ks';
import { useAsync } from '../lib/useAsync';
import { href, navigate } from '../lib/route';
import { dateTime } from '../lib/time';
import { ErrorNotice, Loading, PageHeader } from '../components/ui';

const ACCEPTED_TYPES = 'image/*,application/pdf';

/** KS/OCR error codes with a message a learner can act on. */
function friendly(err: unknown): { title: string; detail: string } | null {
  if (!(err instanceof ApiError)) return null;
  switch (err.code) {
    case 'ocr_not_configured':
      return { title: 'OCR service not enabled', detail: 'The Knowledge Store has no KS_OCR_URL — run with docker compose to get the `ocr` service.' };
    case 'ocr_unreachable':
      return { title: 'Cannot reach the OCR service', detail: 'The `ocr` container is down or still starting. Try again in a few seconds.' };
    case 'not_ready':
      return { title: 'OCR models are loading', detail: 'The OCR service has just started and is loading its models, usually in under a minute. Try again shortly.' };
    case 'invalid_document':
      return { title: 'Unreadable file', detail: err.message };
    case 'too_many_pages':
    case 'too_large':
      return { title: 'File too large', detail: err.message };
    case 'llm_not_configured':
      return { title: 'The Knowledge Store has no LLM configured', detail: `${err.message}. Set an LLM key in knowledge-store/.env to extract concepts.` };
    case 'extraction_failed':
      return err.message.includes('max_tokens')
        ? { title: 'Note too long for one extraction', detail: 'The model ran out of its token budget before answering. Try extracting again (usage varies between runs), or split the note into several scans.' }
        : { title: 'Could not extract concepts', detail: err.message };
    case 'empty_note':
      return { title: 'The note has no text', detail: 'OCR read nothing, or all the text was deleted.' };
    default:
      return null;
  }
}

function Problem({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const f = friendly(error);
  if (!f) return <ErrorNotice error={error} onRetry={onRetry} />;
  return (
    <div className="notice notice-warn" role="alert">
      <i className="ph ph-warning" />
      <div>
        <div className="notice-title">{f.title}</div>
        <div>{f.detail}</div>
        {onRetry && (
          <button className="btn btn-soft" onClick={onRetry}>
            <i className="ph ph-arrow-clockwise" />Retry
          </button>
        )}
      </div>
    </div>
  );
}

export function NotesView({ noteId }: { noteId?: string }) {
  return (
    <main className="main">
      <PageHeader title={noteId ? 'Note' : 'Note scan'}>
        {noteId && (
          <a className="btn btn-secondary btn-soft" href={href({ view: 'notes' })}>
            <i className="ph ph-arrow-left" />All notes
          </a>
        )}
      </PageHeader>
      <div className="page">
        <div className="page-inner">{noteId ? <NoteDetail noteId={noteId} /> : <NoteList />}</div>
      </div>
    </main>
  );
}

// ───────────────────────────────────────────────────────────── list + upload

function NoteList() {
  const listQ = useAsync(() => ks.listNotes(), []);
  const [files, setFiles] = useState<File[]>([]);
  const [title, setTitle] = useState('');
  const [scanning, setScanning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<unknown>();
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  // OCR on CPU takes seconds per page; a visible clock says "still working".
  useEffect(() => {
    if (!scanning) return;
    const started = Date.now();
    const id = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, [scanning]);

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    // Copy now: the FileList is live, and the input's onChange clears it
    // (value = '') before a lazy state updater would get to read it.
    const added = Array.from(list);
    setFiles((prev) => [...prev, ...added]);
    setError(undefined);
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    addFiles(e.dataTransfer.files);
  };

  const scan = async () => {
    setScanning(true);
    setElapsed(0);
    setError(undefined);
    try {
      const note = await ks.scanNote(files, title);
      navigate({ view: 'notes', noteId: note.id });
    } catch (e) {
      setError(e);
      setScanning(false);
    }
  };

  const totalMb = files.reduce((n, f) => n + f.size, 0) / 1024 / 1024;

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Note scan</h2>
          <p>
            Photograph your notebook or upload a PDF. Chiron reads the text (PaddleOCR), you correct it, then Chiron extracts
            concepts for you to review one by one before they go into the Knowledge Store.
          </p>
        </div>
      </div>

      <div
        className={`gen drop${dragging ? ' drop-on' : ''}`}
        style={{ marginTop: 16 }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <i className="ph ph-scan" style={{ fontSize: 26, color: 'var(--frost)' }} />
          <div style={{ flex: 1, minWidth: 220 }}>
            <div style={{ fontSize: 14, color: 'var(--tx2)' }}>Drop images or PDFs here</div>
            <div className="wk-meta">JPG, PNG, WebP, PDF · up to 30 pages, 40 MB per scan</div>
          </div>
          <button className="btn btn-secondary btn-soft" onClick={() => input.current?.click()} disabled={scanning}>
            <i className="ph ph-folder-open" />Choose files
          </button>
          <input
            ref={input}
            type="file"
            accept={ACCEPTED_TYPES}
            multiple
            hidden
            onChange={(e) => {
              addFiles(e.target.files);
              e.target.value = '';
            }}
          />
        </div>

        {files.length > 0 && (
          <>
            <div className="list" style={{ gap: 6 }}>
              {files.map((f, i) => (
                <div key={`${f.name}-${i}`} className="file-row">
                  <i className={`ph ${f.type === 'application/pdf' ? 'ph-file-pdf' : 'ph-image'}`} />
                  <span className="file-name">{f.name}</span>
                  <span className="wk-meta">{(f.size / 1024 / 1024).toFixed(1)} MB</span>
                  <button
                    className="icon-btn"
                    title="Remove this file"
                    disabled={scanning}
                    onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                  >
                    <i className="ph ph-x" />
                  </button>
                </div>
              ))}
            </div>
            <div className="gen-row">
              <div className="field">
                <label>Title (optional)</label>
                <input
                  className="input input-sm"
                  lang="en"
                  placeholder="e.g. Physics · Chapter 5 · Electromagnetic induction"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={scanning}
                />
              </div>
              <button className="btn btn-primary btn-main" onClick={scan} disabled={scanning || totalMb > 40}>
                {scanning ? (
                  <>
                    <span className="spin" />Reading text… {elapsed}s
                  </>
                ) : (
                  <>
                    <i className="ph ph-text-aa" />Read text
                  </>
                )}
              </button>
            </div>
            {scanning && (
              <div className="wk-meta">OCR runs on the CPU: a few seconds per page; a long PDF can take a few minutes.</div>
            )}
            {totalMb > 40 && <div className="wk-meta" style={{ color: 'var(--yel)' }}>{totalMb.toFixed(1)} MB in total — over the 40 MB limit.</div>}
          </>
        )}
        {error != null && <Problem error={error} onRetry={files.length ? scan : undefined} />}
      </div>

      <div className="section-label">Scanned notes</div>
      {listQ.loading && !listQ.data && <Loading />}
      {listQ.error != null && <Problem error={listQ.error} onRetry={listQ.reload} />}
      {listQ.data && listQ.data.notes.length === 0 && <div className="wk-desc">No notes yet.</div>}
      <div className="list" style={{ maxWidth: 760 }}>
        {listQ.data?.notes.map((n) => (
          <a key={n.id} className="wk wk-click" href={href({ view: 'notes', noteId: n.id })}>
            <div style={{ minWidth: 0 }}>
              <div className="wk-head">
                <span className="wk-title" style={{ fontSize: 15 }}>{n.title}</span>
                {n.status === 'extracted' ? (
                  <span className="tag tag-green tag-sm">Concepts extracted</span>
                ) : (
                  <span className="tag tag-yel tag-sm">Draft</span>
                )}
              </div>
              <p className="wk-desc clamp2">{n.text || '(no text was recognised)'}</p>
            </div>
            <div className="wk-side">
              <span className="wk-meta">{n.page_count} page{n.page_count === 1 ? '' : 's'}</span>
              <span className="wk-meta">{dateTime(n.created_at)}</span>
            </div>
          </a>
        ))}
      </div>
    </>
  );
}

// ───────────────────────────────────────────────────────────── detail: edit + review

function NoteDetail({ noteId }: { noteId: string }) {
  const q = useAsync(() => ks.getNote(noteId), [noteId]);
  const [title, setTitle] = useState<string>();
  const [text, setText] = useState<string>();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<unknown>();
  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState<unknown>();
  const [concepts, setConcepts] = useState<ReviewConcept[]>();

  useEffect(() => {
    if (q.data) {
      setTitle(q.data.title);
      setText(q.data.text);
      setConcepts(q.data.concepts);
    }
  }, [q.data]);

  if (q.loading && !q.data) return <Loading label="Loading note…" />;
  if (q.error) return <Problem error={q.error} onRetry={q.reload} />;
  const note = q.data!;
  const dirty = title !== note.title || text !== note.text;

  const save = async (): Promise<Note | undefined> => {
    setSaving(true);
    setSaveError(undefined);
    try {
      const saved = await ks.updateNote(note.id, { title, text });
      q.reload();
      return saved;
    } catch (e) {
      setSaveError(e);
      return undefined;
    } finally {
      setSaving(false);
    }
  };

  const extract = async () => {
    // Extraction reads what is stored, so unsaved edits are saved first —
    // otherwise the LLM would read the text the learner just corrected away.
    if (dirty && !(await save())) return;
    setExtracting(true);
    setExtractError(undefined);
    try {
      const res = await ks.extractNote(note.id);
      setConcepts(res.concepts);
    } catch (e) {
      setExtractError(e);
    } finally {
      setExtracting(false);
    }
  };

  const pending = concepts?.filter((c) => c.status === 'pending_review').length ?? 0;

  return (
    <>
      <div className="page-head">
        <div style={{ flex: 1, minWidth: 260 }}>
          <input
            className="title-input"
            lang="en"
            value={title ?? ''}
            onChange={(e) => setTitle(e.target.value)}
            aria-label="Note title"
          />
          <p>
            {note.page_count} page{note.page_count === 1 ? '' : 's'} · {note.filenames.join(', ')} · scan {dateTime(note.created_at)}
          </p>
        </div>
      </div>

      <div className="steps">
        <Step n={1} title="Correct the recognised text" done={!dirty && note.text.trim() !== ''} />
        <Step n={2} title="Extract concepts" done={(concepts?.length ?? 0) > 0} />
        <Step n={3} title="Review each concept" done={(concepts?.length ?? 0) > 0 && pending === 0} />
      </div>
      <hr className="rule" style={{ margin: '14px 0 18px' }} />

      <div className="note-grid">
        <div>
          <div className="section-label" style={{ marginTop: 0 }}>Text · fix recognition errors before extracting concepts</div>
          <textarea
            className="input note-text"
            lang="en"
            spellCheck={false}
            value={text ?? ''}
            onChange={(e) => setText(e.target.value)}
            placeholder="OCR recognised no text — you can type the content here."
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <button className="btn btn-secondary btn-soft" onClick={save} disabled={!dirty || saving}>
              {saving ? <span className="spin" /> : <i className="ph ph-floppy-disk" />}Save text
            </button>
            <button className="btn btn-primary btn-main" onClick={extract} disabled={extracting || saving || !(text ?? '').trim()}>
              {extracting ? (
                <>
                  <span className="spin" />Extracting concepts…
                </>
              ) : (
                <>
                  <i className="ph ph-sparkle" />
                  {concepts && concepts.length > 0 ? 'Extract again' : 'Extract concepts'}
                </>
              )}
            </button>
            {dirty && <span className="wk-meta" style={{ color: 'var(--yel)' }}>Unsaved changes</span>}
          </div>
          {saveError != null && <div style={{ marginTop: 10 }}><Problem error={saveError} /></div>}
          {extractError != null && <div style={{ marginTop: 10 }}><Problem error={extractError} onRetry={extract} /></div>}
        </div>

        <OcrQuality pages={note.ocr_pages ?? []} />
      </div>

      {concepts && concepts.length > 0 && (
        <>
          <div className="section-label">
            Concepts · {pending > 0 ? `${pending} to review` : 'all reviewed'}
          </div>
          <div className="list" style={{ maxWidth: 760 }}>
            {concepts.map((c) => (
              <ConceptCard key={c.id} concept={c} onChange={(next) => setConcepts((cs) => cs?.map((x) => (x.id === next.id ? next : x)))} />
            ))}
          </div>
        </>
      )}
      {concepts && concepts.length === 0 && note.status === 'extracted' && (
        <div className="notice notice-info" style={{ maxWidth: 760, marginTop: 18 }}>
          <i className="ph ph-info" />
          <div>The LLM found no clear concept in this note. You can correct the text and extract again.</div>
        </div>
      )}
    </>
  );
}

function Step({ n, title, done }: { n: number; title: string; done: boolean }) {
  return (
    <div className={`step${done ? ' step-done' : ''}`}>
      <span className="step-n">{done ? <i className="ph ph-check" /> : n}</span>
      {title}
    </div>
  );
}

/** Where to look: pages and lines the recogniser was least sure about. */
function OcrQuality({ pages }: { pages: OcrPage[] }) {
  const flagged = useMemo(
    () =>
      pages.flatMap((p) =>
        p.lines.filter((l) => l.confidence < 0.8).map((l) => ({ page: `${p.source} · p.${p.number}`, ...l })),
      ),
    [pages],
  );
  return (
    <aside className="kn-detail" style={{ padding: '16px 18px' }}>
      <div className="section-label" style={{ marginTop: 0 }}>OCR confidence</div>
      <div className="list" style={{ gap: 6 }}>
        {pages.map((p) => (
          <div key={`${p.source}-${p.number}`} className="ocr-page">
            <span className="wk-meta" style={{ flex: 1 }}>{p.source} · page {p.number}</span>
            {p.mean_confidence === null ? (
              <span className="tag tag-dim tag-sm">no text</span>
            ) : (
              <span className={`tag tag-sm ${p.mean_confidence >= 0.9 ? 'tag-green' : p.mean_confidence >= 0.8 ? 'tag-yel' : 'tag-red'}`}>
                {Math.round(p.mean_confidence * 100)}%
              </span>
            )}
          </div>
        ))}
      </div>
      <div className="section-label">Lines to check · {flagged.length}</div>
      {flagged.length === 0 ? (
        <div className="wk-desc">No low-confidence lines.</div>
      ) : (
        <div className="list" style={{ gap: 6, maxHeight: 320, overflowY: 'auto' }}>
          {flagged.map((l, i) => (
            <div key={i} className="ocr-line">
              <div className="ocr-line-text">{l.text}</div>
              <div className="wk-meta">
                {l.page} · {Math.round(l.confidence * 100)}%
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="wk-meta" style={{ marginTop: 12, lineHeight: 1.6 }}>
        Handwriting, two-column layouts and formulas are where OCR goes wrong most. Low-confidence lines stay in the text —
        they are only flagged for you to check, and unflagged lines can still be wrong.
      </p>
    </aside>
  );
}

function ConceptCard({ concept, onChange }: { concept: ReviewConcept; onChange: (c: ReviewConcept) => void }) {
  const [edit, setEdit] = useState<{ title: string; subject: string; summary: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [result, setResult] = useState<AcceptResult>();
  // 'create', or the node id to merge into. Undefined = not chosen yet.
  const [choice, setChoice] = useState<string>();
  const pending = concept.status === 'pending_review';
  // Re-asked when the title changes: similarity is computed on the title.
  const candQ = useAsync(() => ks.conceptCandidates(concept.id), [concept.id, concept.title], pending);
  const cands = candQ.data?.candidates ?? [];
  const suggested = candQ.data?.suggested_node_id ?? null;
  // Above the threshold the learner must choose; below it "create" is the
  // default, since that is also what the rule would do.
  const effectiveChoice = choice ?? (candQ.data && !suggested ? 'create' : undefined);
  const mergedInto = result && !result.created ? result.candidates.find((c) => c.node_id === result.node_id)?.title : undefined;

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const saveEdit = () =>
    run(async () => {
      const next = await ks.editConcept(concept.id, edit!);
      onChange({ ...concept, ...next });
      setEdit(undefined);
    });

  const accept = () =>
    run(async () => {
      if (edit) {
        const next = await ks.editConcept(concept.id, edit);
        onChange({ ...concept, ...next });
        setEdit(undefined);
      }
      const decision: AcceptDecision =
        effectiveChoice === 'create' || !effectiveChoice ? { decision: 'create' } : { decision: 'merge', node_id: effectiveChoice };
      const r = await ks.acceptConcept(concept.id, decision);
      setResult(r);
      onChange({ ...concept, ...(edit ?? {}), status: 'accepted', node_id: r.node_id });
    });

  const discard = () =>
    run(async () => {
      await ks.discardConcept(concept.id);
      onChange({ ...concept, status: 'discarded' });
    });

  const decided = concept.status !== 'pending_review';

  return (
    <div className={`wk${concept.status === 'discarded' ? ' wk-dim' : ''}`}>
      <div style={{ minWidth: 0 }}>
        {edit ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <input className="input input-sm" lang="en" value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} aria-label="Concept name" />
            <input className="input input-sm" lang="en" value={edit.subject} onChange={(e) => setEdit({ ...edit, subject: e.target.value })} aria-label="Subject" />
            <textarea className="input" lang="en" style={{ minHeight: 70 }} value={edit.summary} onChange={(e) => setEdit({ ...edit, summary: e.target.value })} aria-label="Summary" />
          </div>
        ) : (
          <>
            <div className="wk-head">
              <span className="wk-title" style={{ fontSize: 15 }}>{concept.title}</span>
              <span className="wk-meta">{concept.subject}</span>
              {concept.status === 'accepted' && (
                <span className="tag tag-green tag-sm">
                  {result && !result.created ? `Merged into “${mergedInto ?? 'an existing concept'}”` : 'Added to the Knowledge Store'}
                </span>
              )}
              {concept.status === 'discarded' && <span className="tag tag-dim tag-sm">Discarded</span>}
            </div>
            <p className="wk-desc">{concept.summary}</p>
          </>
        )}
        {pending && candQ.error != null && <div style={{ marginTop: 8 }}><ErrorNotice error={candQ.error} onRetry={candQ.reload} compact /></div>}
        {pending && cands.length > 0 && (
          <fieldset className="dup" aria-label="Similar existing concepts">
            <legend className="wk-meta">
              {suggested
                ? 'A very similar concept already exists. Merge into it, or add this as a new concept:'
                : 'Somewhat similar concepts exist (below the merge threshold). Adding as new by default:'}
            </legend>
            <label className={`dup-opt${effectiveChoice === 'create' ? ' dup-on' : ''}`}>
              <input type="radio" name={`dup-${concept.id}`} checked={effectiveChoice === 'create'} onChange={() => setChoice('create')} disabled={busy} />
              <span><b>Add as a new concept</b></span>
            </label>
            {cands.map((c) => (
              <label key={c.node_id} className={`dup-opt${effectiveChoice === c.node_id ? ' dup-on' : ''}`}>
                <input type="radio" name={`dup-${concept.id}`} checked={effectiveChoice === c.node_id} onChange={() => setChoice(c.node_id)} disabled={busy} />
                <span style={{ minWidth: 0 }}>
                  Merge into <b>{c.title}</b> <span className="wk-meta">· {c.subject} · {Math.round(c.score * 100)}% similar{c.node_id === suggested ? ' · the automatic rule would pick this' : ''}</span>
                  <span className="wk-desc clamp2" style={{ display: 'block' }}>{c.summary}</span>
                </span>
              </label>
            ))}
          </fieldset>
        )}
        {error != null && <div style={{ marginTop: 8 }}><Problem error={error} /></div>}
      </div>
      <div className="wk-side">
        {!decided && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            {edit ? (
              <>
                <button className="btn btn-soft" onClick={saveEdit} disabled={busy}>Save</button>
                <button className="btn btn-soft" onClick={() => setEdit(undefined)} disabled={busy}>Cancel</button>
              </>
            ) : (
              <button className="icon-btn" title="Edit before accepting" onClick={() => setEdit({ title: concept.title, subject: concept.subject, summary: concept.summary })}>
                <i className="ph ph-pencil-simple" />
              </button>
            )}
            <button
              className="btn btn-frost"
              onClick={accept}
              disabled={busy || candQ.loading || !effectiveChoice}
              title={!effectiveChoice ? 'Choose merge or new first' : undefined}
            >
              {busy ? <span className="spin" /> : <i className="ph ph-check" />}Accept
            </button>
            <button className="btn btn-soft" onClick={discard} disabled={busy}>
              <i className="ph ph-x" />Discard
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
