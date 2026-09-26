/**
 * Main chat, all three modes:
 *
 *   Study   Socratic — POST /socratic/start → /reply → /end. Refuses to
 *             hand over answers; that is the method.
 *   Ask     direct answers — POST /chat/start (mode "ask") → /reply
 *   Solve   worked step by step — POST /chat/start (mode "solve") → /reply
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
  if (m.includes('has no cards')) return 'This study set has no cards yet — add cards before studying.';
  if (m.includes('turn limit')) return 'This session has reached the 20-turn limit. End it and start a new one.';
  if (m.includes('not found')) return `Mnemosyne could not find it (${m}).`;
  return `Mnemosyne returned an error (HTTP ${err.status}): ${m}`;
}

function StatusTag() {
  const { health } = useApp();
  if (health.mnemosyne === 'ok') return <span className="tag tag-green tag-sm">Mnemosyne ready</span>;
  if (health.mnemosyne === 'down') return <span className="tag tag-red tag-sm">Mnemosyne unreachable</span>;
  return <span className="tag tag-dim tag-sm">Checking connection…</span>;
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
  if (down) blocked = 'Cannot reach Mnemosyne — Study is unavailable for now.';
  else if (health.mnemosyne === 'checking') blocked = undefined;
  else if (!user) blocked = 'Not signed in to Mnemosyne — paste a token in Settings.';
  else if (studySetsError) blocked = friendly(studySetsError);
  // Only Socratic needs a study set; the other two work on anything typed.
  else if (mode === 'hoc' && studySets && studySets.length === 0)
    blocked = 'Study needs a study set. This learner has no study sets in Mnemosyne yet.';

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
      // Ask / Solve: the typed message IS the opening move, and the
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
      <PageHeader title={<span style={{ color: 'var(--mut)', fontSize: 13 }}>New conversation</span>} line={false}>
        <StatusTag />
      </PageHeader>
      <div className="chat-empty">
        <HeroMark />
        <h1>Chiron</h1>
        <p>A companion for learning. Solve problems step by step, review through questions, and keep track of what you haven't mastered yet.</p>
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
            ? 'Chiron is replying…'
            : mode === 'hoc'
              ? 'Pick a study set and press send — Chiron opens with a question. You can type what you want to ask first.'
              : mode === 'giai'
                ? 'Paste a problem. Chiron solves it step by step; then ask about any step that is unclear.'
                : 'Ask a question. Chiron answers directly, no beating around the bush.'
        }
        topLeft={
          <div className="set-picker">
            <i className="ph ph-cards-three" style={{ fontSize: 14, color: 'var(--frost2)' }} />
            {mode === 'hoc' ? 'Study set' : 'Context'}
            <select
              className="input"
              value={setId}
              onChange={(e) => setSetId(e.target.value)}
              disabled={!studySets || studySets.length === 0 || starting}
            >
              {mode !== 'hoc' && <option value="">— No study set —</option>}
              {!studySets && <option value="">—</option>}
              {studySets?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {s.topic ? ` · ${s.topic}` : ''}
                </option>
              ))}
            </select>
            {studySetsError != null && (
              <button className="icon-btn" title="Reload" onClick={reloadSets}>
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
      // after a 502 the message is already in the transcript; "Resend"
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
  if (health.mnemosyne === 'down') composerBlock = 'Cannot reach Mnemosyne — your answer cannot be sent right now.';
  else if (atCap) composerBlock = 'This session has reached the 20-turn limit. End it to save it, then start a new one.';

  return (
    <main className="main">
      <PageHeader title={meta?.title ?? 'Study session'} after={<span className="tag tag-frost tag-sm">Study</span>}>
        {messages && (
          <span className="hdr-meta">
            {elapsed} min · {turns}/{SOCRATIC_TURN_CAP_MESSAGES / 2} turns
          </span>
        )}
        <div className="menu-wrap">
          <button className="icon-btn" title="Session options" onClick={() => setMenuOpen((o) => !o)}>
            <i className="ph ph-dots-three" />
          </button>
          {menuOpen && (
            <div className="menu" onMouseLeave={() => setMenuOpen(false)}>
              <button onClick={end} disabled={ending || ended || health.mnemosyne !== 'ok'}>
                <i className="ph ph-flag-checkered" />
                {ended ? 'Session ended' : 'End session & save to KS'}
              </button>
              <button onClick={() => { setMenuOpen(false); setLoadTick((t) => t + 1); }}>
                <i className="ph ph-arrow-clockwise" />Reload history
              </button>
              <button onClick={() => navigate({ view: 'chat' })}>
                <i className="ph ph-plus" />New session
              </button>
              <button
                onClick={async () => {
                  setMenuOpen(false);
                  // Deleting removes the transcript from Mnemosyne. Anything
                  // already shipped to the Knowledge Store stays there — KS
                  // owns its own records.
                  if (!window.confirm('Permanently delete this session from Mnemosyne? A transcript already sent to the Knowledge Store stays there.')) return;
                  try {
                    await mnemosyne.deleteSocratic(sessionId);
                    refreshSessions();
                    navigate({ view: 'chat' });
                  } catch (e) {
                    setEndError(friendly(e));
                  }
                }}
              >
                <i className="ph ph-trash" />Delete this session
              </button>
            </div>
          )}
        </div>
      </PageHeader>

      <div className="chat-scroll" ref={scroller}>
        <div className="chat-col">
          <div className="chip-center">
            <span>Studying “{meta?.title ?? '…'}” · Chiron asks guiding questions, never hands over the answer</span>
          </div>

          {!messages && !loadError && <Loading label="Loading session history…" />}
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
                        <b>Chiron spotted a misconception —</b> {m.flagged_misconception}
                      </div>
                    </div>
                  )}
                  {i === messages.length - 1 && !pending && !ended && !atCap && health.mnemosyne === 'ok' && (
                    <div className="quick">
                      {["I don't get this part", 'Show me an example'].map((q) => (
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
                    <div className="notice-title">Your answer was not sent</div>
                    <div>{pending.failed}</div>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button className="btn btn-soft" onClick={() => send(pending.text)}>
                        <i className="ph ph-arrow-clockwise" />Resend
                      </button>
                      <button className="btn btn-soft" onClick={() => { setText(pending.text); setPending(undefined); }}>
                        Edit
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="msg-ai">
                  <AiAvatar />
                  <div className="thinking">
                    <span className="pulse" /><span className="pulse" /><span className="pulse" />
                    Chiron is thinking…
                  </div>
                </div>
              )}
            </>
          )}

          {ending && <Loading label="Ending the session and sending the transcript to the Knowledge Store…" />}
          {endError && <ErrorNotice error={new Error(endError)} onRetry={end} compact />}
          {endRes && <EndSummary res={endRes} onRetry={end} />}
          {!endRes && meta?.ended && (
            <div className="chip-center"><span>This session has ended.</span></div>
          )}
        </div>
      </div>

      {ended ? (
        <div className="composer-wrap">
          <button className="btn btn-primary btn-main" onClick={() => navigate({ view: 'chat' })}>
            <i className="ph ph-plus" />Start a new session
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
          placeholder="Answer Chiron's question…"
          topLeft={<span className="composer-hint">Chiron asks back instead of giving the answer</span>}
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
  let title = 'Session ended';
  let body: ReactNode;
  let retry = false;
  switch (ks.state) {
    case 'saved':
      tone = 'notice-info';
      body = <>The transcript ({res.message_count} messages) was saved to the Knowledge Store. The KS extract job will pull concepts from it later.</>;
      break;
    case 'disabled':
      icon = 'ph-info';
      body = <>Syncing to the Knowledge Store is turned off on the Mnemosyne side (<code>KS_HTTP_TOKEN</code> is missing from <code>Mnemosyne/.env</code>), so the transcript was not sent.</>;
      break;
    case 'nothing_to_send':
      icon = 'ph-info';
      body = 'The session has no messages, so there is nothing to save.';
      break;
    case 'ks_db_unavailable':
      tone = 'notice-warn';
      icon = 'ph-warning';
      body = <>The Knowledge Store is running but its database failed: {ks.error}. Try again later — resending is safe, KS does not store duplicates.</>;
      retry = true;
      break;
    case 'failed':
      tone = 'notice-warn';
      icon = 'ph-warning';
      body = <>Could not send the transcript to the Knowledge Store: {ks.error}. The session is still kept in Mnemosyne; you can try again.</>;
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
            <i className="ph ph-arrow-clockwise" />Resend transcript
          </button>
        )}
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────── Ask / Solve session

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
  const label = mode === 'solve' ? 'Solve' : 'Ask';
  const composerBlock =
    health.mnemosyne === 'down' ? 'Cannot reach Mnemosyne — your question cannot be sent right now.' : undefined;

  return (
    <main className="main">
      <PageHeader title={title || 'Conversation'} after={<span className="tag tag-frost tag-sm">{label}</span>}>
        <span className="hdr-meta">{messages ? `${Math.ceil(messages.length / 2)} turns` : ''}</span>
        <button className="icon-btn" title="Reload" onClick={() => setLoadTick((t) => t + 1)}>
          <i className="ph ph-arrow-clockwise" />
        </button>
        <button className="icon-btn" title="New conversation" onClick={() => navigate({ view: 'chat' })}>
          <i className="ph ph-plus" />
        </button>
        <button
          className="icon-btn"
          title="Delete this conversation"
          onClick={async () => {
            if (!window.confirm('Permanently delete this conversation?')) return;
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
                ? 'Step-by-step solution · ask about any step that is unclear'
                : 'Quick Q&A · Chiron answers directly'}
            </span>
          </div>

          {!messages && !loadError && <Loading label="Loading conversation…" />}
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
                    <div className="notice-title">Not sent</div>
                    <div>{pending.failed}</div>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button className="btn btn-soft" onClick={() => send(pending.text)}>
                        <i className="ph ph-arrow-clockwise" />Resend
                      </button>
                      <button
                        className="btn btn-soft"
                        onClick={() => {
                          setText(pending.text);
                          setPending(undefined);
                        }}
                      >
                        Edit
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="msg-ai">
                  <AiAvatar />
                  <div className="thinking">
                    <span className="pulse" /><span className="pulse" /><span className="pulse" />
                    {mode === 'solve' ? 'Chiron is solving…' : 'Chiron is replying…'}
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
        placeholder={mode === 'solve' ? 'Ask about a step, or give the next problem…' : 'Ask a follow-up…'}
        topLeft={
          <span className="composer-hint">
            {mode === 'solve' ? 'Chiron solves step by step and explains why' : 'Chiron answers directly and says when it is unsure'}
          </span>
        }
        status={composerBlock}
      />
    </main>
  );
}
