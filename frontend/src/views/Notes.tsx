/**
 * Scan ghi chép — ảnh/PDF → OCR (PaddleOCR + VietOCR) → sửa văn bản → rút khái niệm → duyệt.
 *
 * Two checkpoints on purpose, and the UI keeps them visible as separate steps:
 * the learner corrects the OCR text before any LLM reads it, then decides on
 * every extracted concept before it becomes a node. Nothing here writes to the
 * Knowledge Store graph directly — `accept` is the only way in, and it goes
 * through KS's duplicate detection.
 */
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { ApiError } from '../api/http';
import { ks, type AcceptResult, type Note, type OcrPage, type ReviewConcept } from '../api/ks';
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
      return { title: 'Chưa bật service OCR', detail: 'Knowledge Store chưa có KS_OCR_URL — chạy bằng docker compose để có service `ocr`.' };
    case 'ocr_unreachable':
      return { title: 'Không kết nối được service OCR', detail: 'Container `ocr` đang tắt hoặc đang khởi động. Thử lại sau ít giây.' };
    case 'not_ready':
      return { title: 'Mô hình OCR đang tải', detail: 'Service OCR vừa khởi động và đang nạp mô hình vào bộ nhớ, thường dưới một phút. Thử lại sau một lúc.' };
    case 'invalid_document':
      return { title: 'Tệp không đọc được', detail: err.message };
    case 'too_many_pages':
    case 'too_large':
      return { title: 'Tệp quá lớn', detail: err.message };
    case 'llm_not_configured':
      return { title: 'Knowledge Store chưa cấu hình LLM', detail: `${err.message}. Điền khoá LLM trong knowledge-store/.env để rút khái niệm.` };
    case 'extraction_failed':
      return err.message.includes('max_tokens')
        ? { title: 'Ghi chép quá dài cho một lần rút', detail: 'Mô hình dùng hết ngân sách token trước khi trả kết quả. Bấm rút lại (mỗi lần tốn một lượng khác nhau), hoặc tách ghi chép thành nhiều lần scan.' }
        : { title: 'Không rút được khái niệm', detail: err.message };
    case 'empty_note':
      return { title: 'Ghi chép không còn chữ nào', detail: 'OCR không đọc được gì, hoặc văn bản đã bị xoá hết.' };
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
            <i className="ph ph-arrow-clockwise" />Thử lại
          </button>
        )}
      </div>
    </div>
  );
}

export function NotesView({ noteId }: { noteId?: string }) {
  return (
    <main className="main">
      <PageHeader title={noteId ? 'Ghi chép' : 'Scan ghi chép'}>
        {noteId && (
          <a className="btn btn-secondary btn-soft" href={href({ view: 'notes' })}>
            <i className="ph ph-arrow-left" />Tất cả ghi chép
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
          <h2>Scan ghi chép</h2>
          <p>
            Chụp vở hoặc tải PDF. Chiron nhận dạng chữ (PaddleOCR + VietOCR), bạn sửa lại văn bản, rồi Chiron rút khái niệm để bạn
            duyệt từng cái trước khi vào Knowledge Store.
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
            <div style={{ fontSize: 14, color: 'var(--tx2)' }}>Kéo ảnh hoặc PDF vào đây</div>
            <div className="wk-meta">JPG, PNG, WebP, PDF · tối đa 30 trang, 40 MB mỗi lần</div>
          </div>
          <button className="btn btn-secondary btn-soft" onClick={() => input.current?.click()} disabled={scanning}>
            <i className="ph ph-folder-open" />Chọn tệp
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
                    title="Bỏ tệp này"
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
                <label>Tiêu đề (tuỳ chọn)</label>
                <input
                  className="input input-sm"
                  lang="vi"
                  placeholder="Ví dụ: Vật lý 11 · Chương 5 · Cảm ứng điện từ"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={scanning}
                />
              </div>
              <button className="btn btn-primary btn-main" onClick={scan} disabled={scanning || totalMb > 40}>
                {scanning ? (
                  <>
                    <span className="spin" />Đang nhận dạng… {elapsed}s
                  </>
                ) : (
                  <>
                    <i className="ph ph-text-aa" />Nhận dạng chữ
                  </>
                )}
              </button>
            </div>
            {scanning && (
              <div className="wk-meta">OCR chạy trên CPU: thường vài giây mỗi trang, PDF dài có thể mất vài phút.</div>
            )}
            {totalMb > 40 && <div className="wk-meta" style={{ color: 'var(--yel)' }}>Tổng {totalMb.toFixed(1)} MB — vượt giới hạn 40 MB.</div>}
          </>
        )}
        {error != null && <Problem error={error} onRetry={files.length ? scan : undefined} />}
      </div>

      <div className="section-label">Ghi chép đã scan</div>
      {listQ.loading && !listQ.data && <Loading />}
      {listQ.error != null && <Problem error={listQ.error} onRetry={listQ.reload} />}
      {listQ.data && listQ.data.notes.length === 0 && <div className="wk-desc">Chưa có ghi chép nào.</div>}
      <div className="list" style={{ maxWidth: 760 }}>
        {listQ.data?.notes.map((n) => (
          <a key={n.id} className="wk wk-click" href={href({ view: 'notes', noteId: n.id })}>
            <div style={{ minWidth: 0 }}>
              <div className="wk-head">
                <span className="wk-title" style={{ fontSize: 15 }}>{n.title}</span>
                {n.status === 'extracted' ? (
                  <span className="tag tag-green tag-sm">Đã rút khái niệm</span>
                ) : (
                  <span className="tag tag-yel tag-sm">Bản nháp</span>
                )}
              </div>
              <p className="wk-desc clamp2">{n.text || '(không nhận dạng được chữ nào)'}</p>
            </div>
            <div className="wk-side">
              <span className="wk-meta">{n.page_count} trang</span>
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

  if (q.loading && !q.data) return <Loading label="Đang tải ghi chép…" />;
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
            lang="vi"
            value={title ?? ''}
            onChange={(e) => setTitle(e.target.value)}
            aria-label="Tiêu đề ghi chép"
          />
          <p>
            {note.page_count} trang · {note.filenames.join(', ')} · scan {dateTime(note.created_at)}
          </p>
        </div>
      </div>

      <div className="steps">
        <Step n={1} title="Sửa văn bản nhận dạng" done={!dirty && note.text.trim() !== ''} />
        <Step n={2} title="Rút khái niệm" done={(concepts?.length ?? 0) > 0} />
        <Step n={3} title="Duyệt từng khái niệm" done={(concepts?.length ?? 0) > 0 && pending === 0} />
      </div>
      <hr className="rule" style={{ margin: '14px 0 18px' }} />

      <div className="note-grid">
        <div>
          <div className="section-label" style={{ marginTop: 0 }}>Văn bản · sửa lỗi nhận dạng trước khi rút khái niệm</div>
          <textarea
            className="input note-text"
            lang="vi"
            spellCheck={false}
            value={text ?? ''}
            onChange={(e) => setText(e.target.value)}
            placeholder="OCR không nhận dạng được chữ nào — có thể gõ tay nội dung vào đây."
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <button className="btn btn-secondary btn-soft" onClick={save} disabled={!dirty || saving}>
              {saving ? <span className="spin" /> : <i className="ph ph-floppy-disk" />}Lưu văn bản
            </button>
            <button className="btn btn-primary btn-main" onClick={extract} disabled={extracting || saving || !(text ?? '').trim()}>
              {extracting ? (
                <>
                  <span className="spin" />Đang rút khái niệm…
                </>
              ) : (
                <>
                  <i className="ph ph-sparkle" />
                  {concepts && concepts.length > 0 ? 'Rút lại khái niệm' : 'Rút khái niệm'}
                </>
              )}
            </button>
            {dirty && <span className="wk-meta" style={{ color: 'var(--yel)' }}>Có thay đổi chưa lưu</span>}
          </div>
          {saveError != null && <div style={{ marginTop: 10 }}><Problem error={saveError} /></div>}
          {extractError != null && <div style={{ marginTop: 10 }}><Problem error={extractError} onRetry={extract} /></div>}
        </div>

        <OcrQuality pages={note.ocr_pages ?? []} />
      </div>

      {concepts && concepts.length > 0 && (
        <>
          <div className="section-label">
            Khái niệm · {pending > 0 ? `${pending} chờ duyệt` : 'đã duyệt xong'}
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
          <div>LLM không tìm thấy khái niệm rõ ràng nào trong ghi chép này. Có thể sửa văn bản rồi rút lại.</div>
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
        p.lines.filter((l) => l.confidence < 0.8).map((l) => ({ page: `${p.source} · tr.${p.number}`, ...l })),
      ),
    [pages],
  );
  return (
    <aside className="kn-detail" style={{ padding: '16px 18px' }}>
      <div className="section-label" style={{ marginTop: 0 }}>Độ tin cậy OCR</div>
      <div className="list" style={{ gap: 6 }}>
        {pages.map((p) => (
          <div key={`${p.source}-${p.number}`} className="ocr-page">
            <span className="wk-meta" style={{ flex: 1 }}>{p.source} · trang {p.number}</span>
            {p.mean_confidence === null ? (
              <span className="tag tag-dim tag-sm">không có chữ</span>
            ) : (
              <span className={`tag tag-sm ${p.mean_confidence >= 0.9 ? 'tag-green' : p.mean_confidence >= 0.8 ? 'tag-yel' : 'tag-red'}`}>
                {Math.round(p.mean_confidence * 100)}%
              </span>
            )}
          </div>
        ))}
      </div>
      <div className="section-label">Dòng nên kiểm tra · {flagged.length}</div>
      {flagged.length === 0 ? (
        <div className="wk-desc">Không có dòng nào độ tin cậy thấp.</div>
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
        Chữ viết tay, dấu tiếng Việt và công thức là chỗ OCR dễ sai nhất. Dòng độ tin cậy thấp vẫn được giữ trong văn bản
        — chỉ đánh dấu để bạn soát.
      </p>
    </aside>
  );
}

function ConceptCard({ concept, onChange }: { concept: ReviewConcept; onChange: (c: ReviewConcept) => void }) {
  const [edit, setEdit] = useState<{ title: string; subject: string; summary: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [result, setResult] = useState<AcceptResult>();

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
      const r = await ks.acceptConcept(concept.id);
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
            <input className="input input-sm" lang="vi" value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} aria-label="Tên khái niệm" />
            <input className="input input-sm" lang="vi" value={edit.subject} onChange={(e) => setEdit({ ...edit, subject: e.target.value })} aria-label="Môn học" />
            <textarea className="input" lang="vi" style={{ minHeight: 70 }} value={edit.summary} onChange={(e) => setEdit({ ...edit, summary: e.target.value })} aria-label="Tóm tắt" />
          </div>
        ) : (
          <>
            <div className="wk-head">
              <span className="wk-title" style={{ fontSize: 15 }}>{concept.title}</span>
              <span className="wk-meta">{concept.subject}</span>
              {concept.status === 'accepted' && (
                <span className="tag tag-green tag-sm">
                  {result && !result.created ? 'Đã gộp vào khái niệm có sẵn' : 'Đã thêm vào KS'}
                </span>
              )}
              {concept.status === 'discarded' && <span className="tag tag-dim tag-sm">Đã bỏ</span>}
            </div>
            <p className="wk-desc">{concept.summary}</p>
          </>
        )}
        {error != null && <div style={{ marginTop: 8 }}><Problem error={error} /></div>}
      </div>
      <div className="wk-side">
        {!decided && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            {edit ? (
              <>
                <button className="btn btn-soft" onClick={saveEdit} disabled={busy}>Lưu</button>
                <button className="btn btn-soft" onClick={() => setEdit(undefined)} disabled={busy}>Huỷ</button>
              </>
            ) : (
              <button className="icon-btn" title="Sửa trước khi chấp nhận" onClick={() => setEdit({ title: concept.title, subject: concept.subject, summary: concept.summary })}>
                <i className="ph ph-pencil-simple" />
              </button>
            )}
            <button className="btn btn-frost" onClick={accept} disabled={busy}>
              {busy ? <span className="spin" /> : <i className="ph ph-check" />}Chấp nhận
            </button>
            <button className="btn btn-soft" onClick={discard} disabled={busy}>
              <i className="ph ph-x" />Bỏ
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
