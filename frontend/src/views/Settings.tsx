/**
 * Cài đặt — local only (this browser's localStorage). Secrets are not
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

const HEALTH_TEXT: Record<Health, string> = { ok: 'Đang chạy', down: 'Không kết nối được', checking: 'Đang kiểm tra…' };

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
      <PageHeader title="Cài đặt" />
      <div className="page">
        <div className="page-head">
          <div>
            <h2>Cài đặt</h2>
            <p>Lưu trong trình duyệt này. Token và khoá bí mật nằm trong <code>frontend/.env</code>, không chỉnh ở đây.</p>
          </div>
        </div>
        <hr className="rule" style={{ margin: '18px 0 22px' }} />

        <section className="set-sec">
          <h4>Người học</h4>
          <p>
            Mnemosyne nhận diện bạn bằng token. Cấp token bằng lệnh
            {' '}<code>cargo run -p backend -- create-user &lt;email&gt;</code> trong <code>mnemosyne/</code>;
            token chỉ hiện đúng một lần. Nó được lưu trong trình duyệt này, không nằm trong <code>.env</code>.
          </p>

          {user ? (
            <>
              <div className="set-row">
                <label>Đang đăng nhập</label>
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
                  <i className="ph ph-sign-out" />Đăng xuất
                </button>
              </div>
            </>
          ) : (
            <>
              {rejected && (
                <div className="notice notice-warn" style={{ marginBottom: 12 }}>
                  <i className="ph ph-key" />
                  <div>Token đang lưu bị Mnemosyne từ chối — có thể đã bị thu hồi. Dán token khác.</div>
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
                <label htmlFor="token">Dán token</label>
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
                  <i className="ph ph-sign-in" />Đăng nhập
                </button>
              </form>
              {draft.trim() !== '' && !draft.trim().startsWith(TOKEN_PREFIX) && (
                <p className="wk-meta" style={{ color: 'var(--yel)' }}>
                  Token của Mnemosyne bắt đầu bằng <code>{TOKEN_PREFIX}</code> — kiểm tra lại chuỗi vừa dán.
                </p>
              )}
            </>
          )}
          {token && !user && health.mnemosyne === 'ok' && !rejected && (
            <button className="btn btn-soft" onClick={reloadUser}>
              <i className="ph ph-arrow-clockwise" />Thử lại
            </button>
          )}
        </section>

        {user && (
          <section className="set-sec">
            <h4>Hồ sơ học tập</h4>
            <p>Một dòng mô tả cách bạn học. Mnemosyne lưu kèm người học; hiện chưa dùng vào việc sinh nội dung.</p>
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
              <label htmlFor="style">Cách học</label>
              <input
                id="style"
                className="input"
                style={{ width: 'auto', minWidth: 300 }}
                lang="vi"
                placeholder="Ví dụ: Lớp 11 · thích ví dụ thực tế"
                value={style ?? user.learning_style ?? ''}
                onChange={(e) => setStyle(e.target.value)}
              />
              <button className="btn btn-soft" type="submit" disabled={styleSaving || style === undefined}>
                {styleSaving ? <span className="spin" /> : <i className="ph ph-check" />}Lưu
              </button>
            </form>
          </section>
        )}

        {user && studySets && studySets.length > 0 && (
          <section className="set-sec">
            <h4>Bộ thẻ</h4>
            <p>
              Đổi tên hoặc xoá. Xoá một bộ thẻ sẽ xoá luôn thẻ, lịch sử ôn, câu quiz và phiên Học bài thuộc bộ đó —
              không khôi phục được.
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
          <h4>Giao diện</h4>
          <p>Ba tuỳ chọn của bản thiết kế: màu nhấn, thu gọn thanh bên, nền bản đồ sao.</p>
          <div className="set-row">
            <label>Màu nhấn</label>
            <div className="swatches">
              {ACCENTS.map((c) => (
                <button
                  key={c}
                  className={`swatch${settings.accent === c ? ' swatch-on' : ''}`}
                  style={{ background: c }}
                  title={c}
                  aria-label={`Màu nhấn ${c}`}
                  onClick={() => updateSettings({ accent: c })}
                />
              ))}
            </div>
          </div>
          <div className="set-row">
            <label>Thu gọn thanh bên</label>
            <Switch on={settings.sidebarCollapsed} onChange={(v) => updateSettings({ sidebarCollapsed: v })} label="Thu gọn thanh bên" />
          </div>
          <div className="set-row">
            <label>Nền bản đồ sao</label>
            <Switch on={settings.starTexture} onChange={(v) => updateSettings({ starTexture: v })} label="Nền bản đồ sao" />
          </div>
        </section>

        <section className="set-sec">
          <h4 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            Kết nối
            <button className="btn btn-secondary btn-soft" onClick={recheck}><i className="ph ph-arrow-clockwise" />Kiểm tra lại</button>
          </h4>
          <p>Mỗi module chạy độc lập. Module nào tắt thì chỉ khu vực dùng module đó báo lỗi.</p>
          <table className="table conn">
            <thead>
              <tr><th>Module</th><th>Trạng thái</th><th>Cách kết nối</th></tr>
            </thead>
            <tbody>
              <tr>
                <td>Mnemosyne<div className="sub">Học bài · Thẻ ghi nhớ · Quiz</div></td>
                <td><Dot h={health.mnemosyne} />{HEALTH_TEXT[health.mnemosyne]}</td>
                <td>Browser gọi thẳng <code>{config.mnemosyneUrl}</code> (CORS)</td>
              </tr>
              <tr>
                <td>Knowledge Store<div className="sub">Kiến thức</div></td>
                <td>
                  <Dot h={health.ks} />{HEALTH_TEXT[health.ks]}
                  {proxy && !proxy.ks.tokenConfigured && <div className="sub" style={{ color: 'var(--yel)' }}>Thiếu CHIRON_KS_TOKEN</div>}
                </td>
                <td>Qua proxy <code>/api/ks</code> → <code>{proxy?.ks.url ?? '?'}</code></td>
              </tr>
            </tbody>
          </table>
        </section>

        <section className="set-sec">
          <h4>Thời gian</h4>
          <p>Từ <code>frontend/.env</code>.</p>
          <table className="table conn">
            <tbody>
              <tr><td>Múi giờ</td><td><code>{config.timezone}</code></td></tr>
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
              <div className="notice-title">Xoá “{name}” và mọi thứ trong đó?</div>
              <div>Thẻ, lịch sử ôn, câu quiz và phiên Học bài của bộ thẻ này sẽ mất hẳn.</div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-soft rate-again" onClick={remove} disabled={busy}>
                  {busy ? <span className="spin" /> : <i className="ph ph-trash" />}Xoá hẳn
                </button>
                <button className="btn btn-soft" onClick={() => setConfirming(false)} disabled={busy}>Giữ lại</button>
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="wk-side">
        {draft === undefined ? (
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="icon-btn" title="Đổi tên" onClick={() => setDraft(name)}>
              <i className="ph ph-pencil-simple" />
            </button>
            <button className="icon-btn" title="Xoá bộ thẻ" onClick={() => setConfirming(true)}>
              <i className="ph ph-trash" />
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn btn-soft" onClick={rename} disabled={busy || !draft.trim()}>Lưu</button>
            <button className="btn btn-soft" onClick={() => setDraft(undefined)} disabled={busy}>Huỷ</button>
          </div>
        )}
      </div>
    </div>
  );
}
