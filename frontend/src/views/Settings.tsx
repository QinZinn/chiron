/**
 * Settings — local only (this browser's localStorage). Secrets are not
 * editable here: they live in frontend/.env and never reach the browser.
 */
import { useState } from 'react';
import { mnemosyne } from '../api/mnemosyne';
import { config } from '../config';
import { TOKEN_PREFIX } from '../api/session';
import { ACCENTS, useApp, type Health } from '../state/app';
import { ApiError } from '../api/http';
import { ErrorNotice, PageHeader } from '../components/ui';

function Dot({ h }: { h: Health | 'off' }) {
  const cls = h === 'ok' ? 'dot-ok' : h === 'down' ? 'dot-down' : h === 'checking' ? 'dot-wait' : 'dot-off';
  return <span className={`dot ${cls}`} style={{ marginRight: 7 }} />;
}

function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return <button className={`switch${on ? ' switch-on' : ''}`} role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)} />;
}

const HEALTH_TEXT: Record<Health, string> = { ok: 'Running', down: 'Unreachable', checking: 'Checking…' };

export function SettingsView() {
  const { settings, updateSettings, token, saveToken, user, userError, reloadUser, health, recheck, studySets, reloadSets } =
    useApp();
  const [style, setStyle] = useState<string>();
  const [styleSaving, setStyleSaving] = useState(false);
  const [setsError, setSetsError] = useState<unknown>();
  const proxy = health.proxy;
  const [draft, setDraft] = useState('');
  const rejected = userError instanceof ApiError && userError.kind === 'unauthenticated';

  return (
    <main className="main">
      <PageHeader title="Settings" />
      <div className="page">
        <div className="page-head">
          <div>
            <h2>Settings</h2>
            <p>Saved in this browser. Tokens and secret keys live in <code>frontend/.env</code> and are not edited here.</p>
          </div>
        </div>
        <hr className="rule" style={{ margin: '18px 0 22px' }} />

        <section className="set-sec">
          <h4>Learner</h4>
          <p>
            Mnemosyne knows you by a token. Create one with
            {' '}<code>cargo run -p backend -- create-user &lt;email&gt;</code> in <code>mnemosyne/</code>;
            it is shown exactly once. It is stored in this browser, not in <code>.env</code>.
          </p>

          {user ? (
            <>
              <div className="set-row">
                <label>Signed in as</label>
                <span style={{ fontSize: 14 }}>
                  {user.email}
                  {user.learning_style ? <span className="sub"> · {user.learning_style}</span> : null}
                </span>
              </div>
              <div className="set-row">
                <label>Token</label>
                <span className="wk-meta">
                  {/* Only the prefix and last 4 characters: enough to tell two
                      tokens apart, not enough to reuse one from a screenshot. */}
                  <code>{TOKEN_PREFIX}…{token.slice(-4)}</code>
                </span>
                <button
                  className="btn btn-soft"
                  onClick={() => {
                    saveToken('');
                    setDraft('');
                  }}
                >
                  <i className="ph ph-sign-out" />Sign out
                </button>
              </div>
            </>
          ) : (
            <>
              {rejected && (
                <div className="notice notice-warn" style={{ marginBottom: 12 }}>
                  <i className="ph ph-key" />
                  <div>Mnemosyne rejected the saved token — it may have been revoked. Paste another one.</div>
                </div>
              )}
              {!rejected && userError != null && <ErrorNotice error={userError} compact />}
              <form
                className="set-row"
                onSubmit={(e) => {
                  e.preventDefault();
                  saveToken(draft);
                  setDraft('');
                }}
              >
                <label htmlFor="token">Paste a token</label>
                <input
                  id="token"
                  className="input"
                  style={{ width: 'auto', minWidth: 320, fontFamily: 'ui-monospace, Menlo, monospace' }}
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={`${TOKEN_PREFIX}…`}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                />
                <button className="btn btn-primary btn-main" type="submit" disabled={!draft.trim()}>
                  <i className="ph ph-sign-in" />Sign in
                </button>
              </form>
              {draft.trim() !== '' && !draft.trim().startsWith(TOKEN_PREFIX) && (
                <p className="wk-meta" style={{ color: 'var(--yel)' }}>
                  Mnemosyne tokens start with <code>{TOKEN_PREFIX}</code> — check what you pasted.
                </p>
              )}
            </>
          )}
          {token && !user && health.mnemosyne === 'ok' && !rejected && (
            <button className="btn btn-soft" onClick={reloadUser}>
              <i className="ph ph-arrow-clockwise" />Retry
            </button>
          )}
        </section>

        {user && (
          <section className="set-sec">
            <h4>Learning profile</h4>
            <p>One line about how you learn. Mnemosyne stores it with the learner; it is not used for generation yet.</p>
            <form
              className="set-row"
              onSubmit={async (e) => {
                e.preventDefault();
                setStyleSaving(true);
                try {
                  await mnemosyne.patchMe((style ?? user.learning_style ?? '').trim() || null);
                  reloadUser();
                  setStyle(undefined);
                } finally {
                  setStyleSaving(false);
                }
              }}
            >
              <label htmlFor="style">Learning style</label>
              <input
                id="style"
                className="input"
                style={{ width: 'auto', minWidth: 300 }}
                lang="vi"
                placeholder="e.g. Grade 11 · likes real-world examples"
                value={style ?? user.learning_style ?? ''}
                onChange={(e) => setStyle(e.target.value)}
              />
              <button className="btn btn-soft" type="submit" disabled={styleSaving || style === undefined}>
                {styleSaving ? <span className="spin" /> : <i className="ph ph-check" />}Save
              </button>
            </form>
          </section>
        )}

        {user && studySets && studySets.length > 0 && (
          <section className="set-sec">
            <h4>Study sets</h4>
            <p>
              Rename or delete. Deleting a study set also deletes its cards, review history, quiz questions and Study
              sessions — this cannot be undone.
            </p>
            {setsError != null && <ErrorNotice error={setsError} compact />}
            <div className="list" style={{ maxWidth: 680 }}>
              {studySets.map((s) => (
                <StudySetRow
                  key={s.id}
                  id={s.id}
                  name={s.name}
                  topic={s.topic}
                  onDone={reloadSets}
                  onError={setSetsError}
                />
              ))}
            </div>
          </section>
        )}

        <section className="set-sec">
          <h4>Appearance</h4>
          <p>The design’s three options: accent colour, collapsed sidebar, star-map background.</p>
          <div className="set-row">
            <label>Accent colour</label>
            <div className="swatches">
              {ACCENTS.map((c) => (
                <button
                  key={c}
                  className={`swatch${settings.accent === c ? ' swatch-on' : ''}`}
                  style={{ background: c }}
                  title={c}
                  aria-label={`Accent colour ${c}`}
                  onClick={() => updateSettings({ accent: c })}
                />
              ))}
            </div>
          </div>
          <div className="set-row">
            <label>Collapse sidebar</label>
            <Switch on={settings.sidebarCollapsed} onChange={(v) => updateSettings({ sidebarCollapsed: v })} label="Collapse sidebar" />
          </div>
          <div className="set-row">
            <label>Star-map background</label>
            <Switch on={settings.starTexture} onChange={(v) => updateSettings({ starTexture: v })} label="Star-map background" />
          </div>
        </section>

        <section className="set-sec">
          <h4 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            Connections
            <button className="btn btn-secondary btn-soft" onClick={recheck}><i className="ph ph-arrow-clockwise" />Check again</button>
          </h4>
          <p>Each module runs on its own. When one is down, only the areas that use it show an error.</p>
          <table className="table conn">
            <thead>
              <tr><th>Module</th><th>Status</th><th>How it connects</th></tr>
            </thead>
            <tbody>
              <tr>
                <td>Mnemosyne<div className="sub">Study · Flashcards · Quiz</div></td>
                <td><Dot h={health.mnemosyne} />{HEALTH_TEXT[health.mnemosyne]}</td>
                <td>Browser calls <code>{config.mnemosyneUrl}</code> directly (CORS)</td>
              </tr>
              <tr>
                <td>Knowledge Store<div className="sub">Knowledge · Note scan</div></td>
                <td>
                  <Dot h={health.ks} />{HEALTH_TEXT[health.ks]}
                  {proxy && !proxy.ks.tokenConfigured && <div className="sub" style={{ color: 'var(--yel)' }}>CHIRON_KS_TOKEN missing</div>}
                </td>
                <td>Qua proxy <code>/api/ks</code> → <code>{proxy?.ks.url ?? '?'}</code></td>
              </tr>
            </tbody>
          </table>
        </section>

        <section className="set-sec">
          <h4>Time</h4>
          <p>From <code>frontend/.env</code>.</p>
          <table className="table conn">
            <tbody>
              <tr><td>Time zone</td><td><code>{config.timezone}</code></td></tr>
            </tbody>
          </table>
        </section>
      </div>
    </main>
  );
}

/** One editable study set: rename in place, delete with a confirmation. */
function StudySetRow({
  id,
  name,
  topic,
  onDone,
  onError,
}: {
  id: string;
  name: string;
  topic: string | null;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const [draft, setDraft] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const rename = async () => {
    if (draft === undefined || !draft.trim()) return;
    setBusy(true);
    try {
      await mnemosyne.patchStudySet(id, { name: draft.trim() });
      setDraft(undefined);
      onDone();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await mnemosyne.deleteStudySet(id);
      setConfirming(false);
      onDone();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wk">
      <div style={{ minWidth: 0 }}>
        {draft === undefined ? (
          <>
            <div className="wk-title" style={{ fontSize: 15 }}>{name}</div>
            {topic && <span className="wk-meta">{topic}</span>}
          </>
        ) : (
          <input
            className="input"
            lang="vi"
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) rename();
              if (e.key === 'Escape') setDraft(undefined);
            }}
          />
        )}
        {confirming && (
          <div className="notice notice-warn" style={{ marginTop: 10 }}>
            <i className="ph ph-warning" />
            <div>
              <div className="notice-title">Delete “{name}” and everything in it?</div>
              <div>Its cards, review history, quiz questions and Study sessions will be gone for good.</div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-soft rate-again" onClick={remove} disabled={busy}>
                  {busy ? <span className="spin" /> : <i className="ph ph-trash" />}Delete
                </button>
                <button className="btn btn-soft" onClick={() => setConfirming(false)} disabled={busy}>Keep</button>
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="wk-side">
        {draft === undefined ? (
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="icon-btn" title="Rename" onClick={() => setDraft(name)}>
              <i className="ph ph-pencil-simple" />
            </button>
            <button className="icon-btn" title="Delete study set" onClick={() => setConfirming(true)}>
              <i className="ph ph-trash" />
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn btn-soft" onClick={rename} disabled={busy || !draft.trim()}>Save</button>
            <button className="btn btn-soft" onClick={() => setDraft(undefined)} disabled={busy}>Cancel</button>
          </div>
        )}
      </div>
    </div>
  );
}
