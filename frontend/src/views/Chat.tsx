/**
 * Main chat, all three modes:
 *
 *   Học bài   Socratic — POST /socratic/start → /reply → /end. Refuses to
 *             hand over answers; that is the method.
 *   Hỏi bài   direct answers — POST /chat/start (mode "ask") → /reply
 *   Giải bài  worked step by step — POST /chat/start (mode "solve") → /reply
 *
 * A session id alone does not say which table it lives in, so the URL does:
 * #/chat/s/<id> for Socratic, #/chat/c/<id> for the other two.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiError } from '../api/http';
import {
  mnemosyne,
  SOCRATIC_TURN_CAP_MESSAGES,
  type ChatMessage,
  type ChatMode,
  type EndResponse,
  type KsSyncStatus,
  type SocraticMessage,
} from '../api/mnemosyne';
import { useApp } from '../state/app';
import { navigate } from '../lib/route';
import { minutesBetween } from '../lib/time';
import { Composer, type Mode } from '../components/Composer';
import { AiAvatar, Constellation, ErrorNotice, HeroMark, Loading, PageHeader } from '../components/ui';

/**
 * Hand-off from the new-chat screen to the session screen, so the opening
 * message renders at once instead of being re-fetched, and so text the
 * learner typed before starting is sent as their first reply.
 */
const handoff = new Map<string, { messages: SocraticMessage[]; firstReply?: string }>();
const chatHandoff = new Map<string, { mode: ChatMode; title: string; messages: ChatMessage[] }>();

/** Mnemosyne's known 4xx messages, in the learner's language. */
function friendly(err: unknown): string {
  if (!(err instanceof ApiError)) return err instanceof Error ? err.message : String(err);
  if (err.kind !== 'http') return err.message;
  const m = err.message;
  if (m.includes('has no cards')) return 'Bộ thẻ này chưa có thẻ nào — cần tạo thẻ trước khi học.';
  if (m.includes('turn limit')) return 'Phiên đã đạt giới hạn 20 lượt. Hãy kết thúc và mở phiên mới.';
  if (m.includes('not found')) return `Mnemosyne không tìm thấy dữ liệu (${m}).`;
  return `Mnemosyne báo lỗi (HTTP ${err.status}): ${m}`;
}

function StatusTag() {
  const { health } = useApp();
  if (health.mnemosyne === 'ok') return <span className="tag tag-green tag-sm">Mnemosyne sẵn sàng</span>;
  if (health.mnemosyne === 'down') return <span className="tag tag-red tag-sm">Mnemosyne mất kết nối</span>;
  return <span className="tag tag-dim tag-sm">Đang kiểm tra kết nối…</span>;
}

export function ChatView({
  kind,
  sessionId,
  newSetId,
}: {
  kind?: 'socratic' | 'chat';
  sessionId?: string;
  newSetId?: string;
}) {
  if (!sessionId) return <NewChat preselect={newSetId} />;
  return kind === 'chat' ? (
    <AskSolveSession key={sessionId} sessionId={sessionId} />
  ) : (
    <SessionChat key={sessionId} sessionId={sessionId} />
  );
}

// ─────────────────────────────────────────────────────────────── 1a — new chat

function NewChat({ preselect }: { preselect?: string }) {
  const { health, user, studySets, studySetsError, reloadSets, refreshSessions } = useApp();
  const [mode, setMode] = useState<Mode>('hoc');
  const [setId, setSetId] = useState('');
  const [text, setText] = useState('');
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!studySets || studySets.length === 0) return;
    // A set named in the URL wins, but only if it is really this learner's.
    if (preselect && studySets.some((s) => s.id === preselect)) {
      setSetId(preselect);
      return;
    }
    if (!setId) setSetId(studySets[0].id);
  }, [studySets, setId, preselect]);

  const down = health.mnemosyne === 'down';
  let blocked: string | undefined;
  if (down) blocked = 'Không kết nối được Mnemosyne — Học bài tạm thời không dùng được.';
  else if (health.mnemosyne === 'checking') blocked = undefined;
  else if (!user) blocked = 'Chưa đăng nhập Mnemosyne — dán token trong Cài đặt.';
  else if (studySetsError) blocked = friendly(studySetsError);
  // Only Socratic needs a study set; the other two work on anything typed.
  else if (mode === 'hoc' && studySets && studySets.length === 0)
    blocked = 'Học bài cần một bộ thẻ. Người học này chưa có bộ thẻ nào trong Mnemosyne.';

  const start = async () => {
    if (!user) return;
    setStarting(true);
    setError(undefined);
    try {
      if (mode === 'hoc') {
        if (!setId) return;
        const res = await mnemosyne.socraticStart(setId);
        const now = new Date().toISOString();
        handoff.set(res.session_id, {
          messages: [{ role: 'assistant', content: res.opening_message, flagged_misconception: null, created_at: now }],
          firstReply: text.trim() || undefined,
        });
        refreshSessions();
        navigate({ view: 'chat', kind: 'socratic', sessionId: res.session_id });
        return;
      }
      // Hỏi bài / Giải bài: the typed message IS the opening move, and the
      // study set is optional context rather than the subject.
      const res = await mnemosyne.chatStart(mode === 'giai' ? 'solve' : 'ask', text, setId || undefined);
      const now = new Date().toISOString();
      chatHandoff.set(res.session_id, {
        mode: res.mode,
        title: res.title,
        messages: [
          { role: 'user', content: text.trim(), created_at: now },
          { role: 'assistant', content: res.reply, created_at: now },
        ],
      });
      refreshSessions();
      navigate({ view: 'chat', kind: 'chat', sessionId: res.session_id });
    } catch (e) {
      setError(friendly(e));
      setStarting(false);
    }
  };

  return (
    <main className="main main-stars">
      <Constellation />
      <PageHeader title={<span style={{ color: 'var(--mut)', fontSize: 13 }}>Cuộc trò chuyện mới</span>} line={false}>
        <StatusTag />
      </PageHeader>
      <div className="chat-empty">
        <HeroMark />
        <h1>Chiron</h1>
        <p>Người đồng hành trong việc học. Giải bài từng bước, ôn lại bằng câu hỏi, và theo dõi những gì bạn chưa vững.</p>
      </div>
      <Composer
        float
        mode={mode}
        onModeChange={(m) => {
          setMode(m);
          setError(undefined);
        }}
        value={text}
        onChange={setText}
        onSubmit={start}
        allowEmpty={mode === 'hoc'}
        sending={starting}
        disabled={Boolean(blocked) || (mode === 'hoc' && !setId) || health.mnemosyne !== 'ok'}
        autoFocus
        placeholder={
          starting
            ? 'Chiron đang trả lời…'
            : mode === 'hoc'
              ? 'Chọn bộ thẻ rồi bấm gửi — Chiron sẽ mở đầu bằng một câu hỏi. Có thể gõ sẵn điều bạn muốn hỏi trước.'
              : mode === 'giai'
                ? 'Dán đề bài. Chiron giải từng bước, rồi bạn hỏi tiếp về bước nào chưa rõ.'
                : 'Hỏi một câu. Chiron trả lời thẳng, không vòng vo.'
        }
        topLeft={
          <div className="set-picker">
            <i className="ph ph-cards-three" style={{ fontSize: 14, color: 'var(--frost2)' }} />
            {mode === 'hoc' ? 'Bộ thẻ' : 'Ngữ cảnh'}
            <select
              className="input"
              value={setId}
              onChange={(e) => setSetId(e.target.value)}
              disabled={!studySets || studySets.length === 0 || starting}
            >
              {mode !== 'hoc' && <option value="">— Không dùng bộ thẻ —</option>}
              {!studySets && <option value="">—</option>}
              {studySets?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {s.topic ? ` · ${s.topic}` : ''}
                </option>
              ))}
            </select>
            {studySetsError != null && (
              <button className="icon-btn" title="Tải lại" onClick={reloadSets}>
                <i className="ph ph-arrow-clockwise" />
              </button>
            )}
          </div>
        }
        status={error ?? blocked}
      />
    </main>
  );
}

// ─────────────────────────────────────────────────────────────── 1b — session

interface Pending {
  text: string;
  failed?: string;
}

function SessionChat({ sessionId }: { sessionId: string }) {
  const { recent, refreshSessions, health } = useApp();
  const meta = recent.find((r) => r.kind === 'socratic' && r.id === sessionId);
  // Read during render (idempotent), consumed in an effect: reopening this
  // session later must load the real transcript, not this snapshot.
  const initial = useMemo(() => handoff.get(sessionId), [sessionId]);
  useEffect(() => {
    handoff.delete(sessionId);
  }, [sessionId]);

  const [messages, setMessages] = useState<SocraticMessage[] | undefined>(initial?.messages);
  const [loadError, setLoadError] = useState<unknown>();
  const [loadTick, setLoadTick] = useState(0);
  const [text, setText] = useState('');
  const [pending, setPending] = useState<Pending>();
  const [menuOpen, setMenuOpen] = useState(false);
  const [ending, setEnding] = useState(false);
  const [endRes, setEndRes] = useState<EndResponse>();
  const [endError, setEndError] = useState<string>();
  const scroller = useRef<HTMLDivElement>(null);
  const firstReplySent = useRef(false);

  // Reopened from the sidebar (or a reload): fetch the transcript.
  useEffect(() => {
    if (initial && loadTick === 0) return;
    let live = true;
    setLoadError(undefined);
    mnemosyne.socraticHistory(sessionId).then(
      (r) => live && setMessages(r.messages),
      (e) => live && setLoadError(e),
    );
    return () => {
      live = false;
    };
  }, [sessionId, initial, loadTick]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' });
  }, [messages, pending]);

  const send = async (raw: string) => {
    const msg = raw.trim();
    if (!msg || pending && !pending.failed) return;
    setPending({ text: msg });
    setText('');
    try {
      const res = await mnemosyne.socraticReply(sessionId, msg);
      const now = new Date().toISOString();
      setMessages((m) => [
        ...(m ?? []),
        { role: 'user', content: msg, flagged_misconception: null, created_at: now },
        { role: 'assistant', content: res.reply, flagged_misconception: res.flagged_misconception, created_at: now },
      ]);
      setPending(undefined);
    } catch (e) {
      // Mnemosyne stores the learner's message before calling DeepSeek, so
      // after a 502 the message is already in the transcript; "Gửi lại"
      // stores it a second time. Harmless for the dialogue, and still better
      // than silently dropping what the learner wrote.
      setPending({ text: msg, failed: friendly(e) });
    }
  };

  // Text typed on the new-chat screen goes out as the first reply.
  useEffect(() => {
    if (initial?.firstReply && !firstReplySent.current) {
      firstReplySent.current = true;
      void send(initial.firstReply);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const end = async () => {
    setMenuOpen(false);
    setEnding(true);
    setEndError(undefined);
    try {
      const res = await mnemosyne.socraticEnd(sessionId);
      setEndRes(res);
      refreshSessions();
    } catch (e) {
      setEndError(friendly(e));
    } finally {
      setEnding(false);
    }
  };

  const ended = Boolean(endRes) || Boolean(meta?.ended);
  const count = messages?.length ?? 0;
  const atCap = count >= SOCRATIC_TURN_CAP_MESSAGES;
  const turns = Math.ceil(count / 2);
  const elapsed = messages && messages.length > 0 ? minutesBetween(messages[0].created_at, messages[messages.length - 1].created_at) : 0;
  const busy = Boolean(pending && !pending.failed);

  let composerBlock: string | undefined;
  if (health.mnemosyne === 'down') composerBlock = 'Không kết nối được Mnemosyne — không gửi được câu trả lời lúc này.';
  else if (atCap) composerBlock = 'Phiên đã đạt giới hạn 20 lượt. Kết thúc phiên để lưu lại, rồi mở phiên mới.';

  return (
    <main className="main">
      <PageHeader title={meta?.title ?? 'Phiên Học bài'} after={<span className="tag tag-frost tag-sm">Học bài</span>}>
        {messages && (
          <span className="hdr-meta">
            Phiên {elapsed} phút · {turns}/{SOCRATIC_TURN_CAP_MESSAGES / 2} lượt
          </span>
        )}
        <div className="menu-wrap">
          <button className="icon-btn" title="Tuỳ chọn phiên" onClick={() => setMenuOpen((o) => !o)}>
            <i className="ph ph-dots-three" />
          </button>
          {menuOpen && (
            <div className="menu" onMouseLeave={() => setMenuOpen(false)}>
              <button onClick={end} disabled={ending || ended || health.mnemosyne !== 'ok'}>
                <i className="ph ph-flag-checkered" />
                {ended ? 'Phiên đã kết thúc' : 'Kết thúc phiên & lưu vào KS'}
              </button>
              <button onClick={() => { setMenuOpen(false); setLoadTick((t) => t + 1); }}>
                <i className="ph ph-arrow-clockwise" />Tải lại lịch sử
              </button>
              <button onClick={() => navigate({ view: 'chat' })}>
                <i className="ph ph-plus" />Phiên mới
              </button>
              <button
                onClick={async () => {
                  setMenuOpen(false);
                  // Deleting removes the transcript from Mnemosyne. Anything
                  // already shipped to the Knowledge Store stays there — KS
                  // owns its own records.
                  if (!window.confirm('Xoá hẳn phiên này khỏi Mnemosyne? Transcript đã gửi sang Knowledge Store vẫn được giữ ở đó.')) return;
                  try {
                    await mnemosyne.deleteSocratic(sessionId);
                    refreshSessions();
                    navigate({ view: 'chat' });
                  } catch (e) {
                    setEndError(friendly(e));
                  }
                }}
              >
                <i className="ph ph-trash" />Xoá phiên này
              </button>
            </div>
          )}
        </div>
      </PageHeader>

      <div className="chat-scroll" ref={scroller}>
        <div className="chat-col">
          <div className="chip-center">
            <span>Học bài từ bộ thẻ “{meta?.title ?? '…'}” · Chiron hỏi gợi mở, không đưa đáp án</span>
          </div>

          {!messages && !loadError && <Loading label="Đang tải lịch sử phiên…" />}
          {loadError != null && <ErrorNotice error={loadError} onRetry={() => setLoadTick((t) => t + 1)} />}

          {messages?.map((m, i) =>
            m.role === 'assistant' ? (
              <div className="msg-ai" key={i}>
                <AiAvatar />
                <div className="msg-ai-body">
                  <div className="msg-ai-text">{m.content}</div>
                  {m.flagged_misconception && (
                    <div className="callout-yel">
                      <i className="ph ph-lightbulb" />
                      <div>
                        <b>Chiron nhận thấy một hiểu lầm —</b> {m.flagged_misconception}
                      </div>
                    </div>
                  )}
                  {i === messages.length - 1 && !pending && !ended && !atCap && health.mnemosyne === 'ok' && (
                    <div className="quick">
                      {['Mình chưa rõ chỗ này', 'Cho mình xem ví dụ'].map((q) => (
                        <button key={q} className="btn btn-secondary btn-soft" onClick={() => send(q)}>
                          {q}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="msg-user" key={i}>
                <div>{m.content}</div>
              </div>
            ),
          )}

          {pending && (
            <>
              <div className={`msg-user${pending.failed ? ' failed' : ''}`}>
                <div>{pending.text}</div>
              </div>
              {pending.failed ? (
                <div className="notice notice-err">
                  <i className="ph ph-warning-circle" />
                  <div>
                    <div className="notice-title">Chưa gửi được câu trả lời</div>
                    <div>{pending.failed}</div>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button className="btn btn-soft" onClick={() => send(pending.text)}>
                        <i className="ph ph-arrow-clockwise" />Gửi lại
                      </button>
                      <button className="btn btn-soft" onClick={() => { setText(pending.text); setPending(undefined); }}>
                        Sửa lại
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="msg-ai">
                  <AiAvatar />
                  <div className="thinking">
                    <span className="pulse" /><span className="pulse" /><span className="pulse" />
                    Chiron đang suy nghĩ…
                  </div>
                </div>
              )}
            </>
          )}

          {ending && <Loading label="Đang kết thúc phiên và gửi transcript sang Knowledge Store…" />}
          {endError && <ErrorNotice error={new Error(endError)} onRetry={end} compact />}
          {endRes && <EndSummary res={endRes} onRetry={end} />}
          {!endRes && meta?.ended && (
            <div className="chip-center"><span>Phiên này đã kết thúc.</span></div>
          )}
        </div>
      </div>

      {ended ? (
        <div className="composer-wrap">
          <button className="btn btn-primary btn-main" onClick={() => navigate({ view: 'chat' })}>
            <i className="ph ph-plus" />Bắt đầu phiên mới
          </button>
        </div>
      ) : (
        <Composer
          value={text}
          onChange={setText}
          onSubmit={() => send(text)}
          sending={busy}
          disabled={Boolean(composerBlock) || !messages}
          autoFocus
          placeholder="Trả lời câu hỏi của Chiron…"
          topLeft={<span className="composer-hint">Chiron sẽ hỏi lại thay vì đưa đáp án</span>}
          status={composerBlock}
        />
      )}
    </main>
  );
}

function EndSummary({ res, onRetry }: { res: EndResponse; onRetry: () => void }) {
  const ks: KsSyncStatus = res.knowledge_store;
  let tone = 'notice-info';
  let icon = 'ph-check-circle';
  let title = 'Đã kết thúc phiên';
  let body: ReactNode;
  let retry = false;
  switch (ks.state) {
    case 'saved':
      tone = 'notice-info';
      body = <>Transcript ({res.message_count} tin nhắn) đã được lưu vào Knowledge Store. Job extract của KS sẽ rút khái niệm từ đó sau.</>;
      break;
    case 'disabled':
      icon = 'ph-info';
      body = <>Đồng bộ sang Knowledge Store đang tắt phía Mnemosyne (thiếu <code>KS_HTTP_TOKEN</code> trong <code>Mnemosyne/.env</code>), nên transcript chưa được gửi.</>;
      break;
    case 'nothing_to_send':
      icon = 'ph-info';
      body = 'Phiên chưa có tin nhắn nào nên không có gì để lưu.';
      break;
    case 'ks_db_unavailable':
      tone = 'notice-warn';
      icon = 'ph-warning';
      body = <>Knowledge Store đang chạy nhưng database của nó lỗi: {ks.error}. Thử lại sau — gửi lại an toàn, KS không lưu trùng.</>;
      retry = true;
      break;
    case 'failed':
      tone = 'notice-warn';
      icon = 'ph-warning';
      body = <>Không gửi được transcript sang Knowledge Store: {ks.error}. Phiên học vẫn được giữ trong Mnemosyne; có thể thử gửi lại.</>;
      retry = true;
      break;
  }
  return (
    <div className={`notice ${tone}`}>
      <i className={`ph ${icon}`} />
      <div>
        <div className="notice-title">{title}</div>
        <div>{body}</div>
        {retry && (
          <button className="btn btn-soft" onClick={onRetry}>
            <i className="ph ph-arrow-clockwise" />Gửi lại transcript
          </button>
        )}
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────── Hỏi bài / Giải bài session

function AskSolveSession({ sessionId }: { sessionId: string }) {
  const { health, refreshSessions } = useApp();
  const initial = useMemo(() => chatHandoff.get(sessionId), [sessionId]);
  useEffect(() => {
    chatHandoff.delete(sessionId);
  }, [sessionId]);

  const [messages, setMessages] = useState<ChatMessage[] | undefined>(initial?.messages);
  const [mode, setMode] = useState<ChatMode | undefined>(initial?.mode);
  const [title, setTitle] = useState(initial?.title ?? '');
  const [loadError, setLoadError] = useState<unknown>();
  const [loadTick, setLoadTick] = useState(0);
  const [text, setText] = useState('');
  const [pending, setPending] = useState<Pending>();
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (initial && loadTick === 0) return;
    let live = true;
    setLoadError(undefined);
    mnemosyne.chatSession(sessionId).then(
      (r) => {
        if (!live) return;
        setMessages(r.messages);
        setMode(r.mode);
        setTitle(r.title);
      },
      (e) => live && setLoadError(e),
    );
    return () => {
      live = false;
    };
  }, [sessionId, initial, loadTick]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' });
  }, [messages, pending]);

  const send = async (raw: string) => {
    const msg = raw.trim();
    if (!msg || (pending && !pending.failed)) return;
    setPending({ text: msg });
    setText('');
    try {
      const res = await mnemosyne.chatReply(sessionId, msg);
      const now = new Date().toISOString();
      setMessages((m) => [
        ...(m ?? []),
        { role: 'user', content: msg, created_at: now },
        { role: 'assistant', content: res.reply, created_at: now },
      ]);
      setPending(undefined);
      refreshSessions();
    } catch (e) {
      setPending({ text: msg, failed: friendly(e) });
    }
  };

  const busy = Boolean(pending && !pending.failed);
  const label = mode === 'solve' ? 'Giải bài' : 'Hỏi bài';
  const composerBlock =
    health.mnemosyne === 'down' ? 'Không kết nối được Mnemosyne — không gửi được câu hỏi lúc này.' : undefined;

  return (
    <main className="main">
      <PageHeader title={title || 'Cuộc trò chuyện'} after={<span className="tag tag-frost tag-sm">{label}</span>}>
        <span className="hdr-meta">{messages ? `${Math.ceil(messages.length / 2)} lượt` : ''}</span>
        <button className="icon-btn" title="Tải lại" onClick={() => setLoadTick((t) => t + 1)}>
          <i className="ph ph-arrow-clockwise" />
        </button>
        <button className="icon-btn" title="Cuộc trò chuyện mới" onClick={() => navigate({ view: 'chat' })}>
          <i className="ph ph-plus" />
        </button>
        <button
          className="icon-btn"
          title="Xoá cuộc trò chuyện này"
          onClick={async () => {
            if (!window.confirm('Xoá hẳn cuộc trò chuyện này?')) return;
            try {
              await mnemosyne.deleteChat(sessionId);
              refreshSessions();
              navigate({ view: 'chat' });
            } catch (e) {
              setLoadError(e);
            }
          }}
        >
          <i className="ph ph-trash" />
        </button>
      </PageHeader>

      <div className="chat-scroll" ref={scroller}>
        <div className="chat-col">
          <div className="chip-center">
            <span>
              {mode === 'solve'
                ? 'Giải từng bước · hỏi tiếp về bước nào chưa rõ'
                : 'Hỏi đáp nhanh · Chiron trả lời thẳng'}
            </span>
          </div>

          {!messages && !loadError && <Loading label="Đang tải cuộc trò chuyện…" />}
          {loadError != null && <ErrorNotice error={loadError} onRetry={() => setLoadTick((t) => t + 1)} />}

          {messages?.map((m, i) =>
            m.role === 'assistant' ? (
              <div className="msg-ai" key={i}>
                <AiAvatar />
                <div className="msg-ai-body">
                  <div className="msg-ai-text">{m.content}</div>
                </div>
              </div>
            ) : (
              <div className="msg-user" key={i}>
                <div>{m.content}</div>
              </div>
            ),
          )}

          {pending && (
            <>
              <div className={`msg-user${pending.failed ? ' failed' : ''}`}>
                <div>{pending.text}</div>
              </div>
              {pending.failed ? (
                <div className="notice notice-err">
                  <i className="ph ph-warning-circle" />
                  <div>
                    <div className="notice-title">Chưa gửi được</div>
                    <div>{pending.failed}</div>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button className="btn btn-soft" onClick={() => send(pending.text)}>
                        <i className="ph ph-arrow-clockwise" />Gửi lại
                      </button>
                      <button
                        className="btn btn-soft"
                        onClick={() => {
                          setText(pending.text);
                          setPending(undefined);
                        }}
                      >
                        Sửa lại
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="msg-ai">
                  <AiAvatar />
                  <div className="thinking">
                    <span className="pulse" /><span className="pulse" /><span className="pulse" />
                    {mode === 'solve' ? 'Chiron đang giải…' : 'Chiron đang trả lời…'}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <Composer
        value={text}
        onChange={setText}
        onSubmit={() => send(text)}
        sending={busy}
        disabled={Boolean(composerBlock) || !messages}
        autoFocus
        placeholder={mode === 'solve' ? 'Hỏi về một bước, hoặc đưa bài tiếp theo…' : 'Hỏi tiếp…'}
        topLeft={
          <span className="composer-hint">
            {mode === 'solve' ? 'Chiron giải từng bước và nói rõ vì sao' : 'Chiron trả lời thẳng, nói rõ khi không chắc'}
          </span>
        }
        status={composerBlock}
      />
    </main>
  );
}
