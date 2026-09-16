/**
 * Cài đặt — local only (this browser's localStorage). Secrets are not
 * editable here: they live in frontend/.env and never reach the browser.
 */
import { config } from '../config';
import { ACCENTS, useApp, type Health } from '../state/app';
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
  const { settings, updateSettings, users, usersError, userId, health, recheck } = useApp();
  const proxy = health.proxy;
  const chosenMissing = Boolean(settings.userId) && users !== undefined && !users.some((u) => u.id === settings.userId);

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
          <p>Mnemosyne chưa có đăng nhập — mọi yêu cầu gửi kèm <code>user_id</code> của người được chọn ở đây.</p>
          {usersError != null ? (
            <ErrorNotice error={usersError} compact />
          ) : (
            <div className="set-row">
              <label htmlFor="user">Người học hiện tại</label>
              <select
                id="user"
                className="input"
                style={{ width: 'auto', minWidth: 280 }}
                value={userId}
                onChange={(e) => updateSettings({ userId: e.target.value })}
                disabled={!users}
              >
                <option value="">{users ? (users.length ? '— Chọn người học —' : 'Mnemosyne chưa có user nào') : 'Đang tải…'}</option>
                {users?.map((u) => (
                  <option key={u.id} value={u.id}>{u.email}{u.learning_style ? ` · ${u.learning_style}` : ''}</option>
                ))}
              </select>
            </div>
          )}
          {chosenMissing && (
            <div className="notice notice-warn">
              <i className="ph ph-warning" />
              <div>Người học đã chọn trước đó không còn trong Mnemosyne (có thể database đã được dựng lại). Hãy chọn lại.</div>
            </div>
          )}
          {!settings.userId && config.defaultUserId && (
            <p className="wk-meta">Mặc định lấy từ <code>CHIRON_MNEMOSYNE_USER_ID</code>.</p>
          )}
        </section>

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
              <tr>
                <td>Google Calendar<div className="sub">Lịch học · chỉ đọc</div></td>
                <td>
                  {proxy ? (
                    proxy.gcal.configured
                      ? <><Dot h="ok" />Đã cấu hình</>
                      : <><Dot h="off" />Chưa cấu hình<div className="sub" style={{ color: 'var(--yel)' }}>Thiếu {proxy.gcal.missing.join(', ')}</div></>
                  ) : <><Dot h="checking" />Đang kiểm tra…</>}
                </td>
                <td>Qua proxy <code>/api/gcal</code> → withone.ai</td>
              </tr>
              <tr>
                <td>Horae<div className="sub">Xếp lịch tự học</div></td>
                <td><Dot h="off" />Không có HTTP API</td>
                <td>Không gọi trực tiếp. Lịch học đọc block <code>[Auto]</code> Horae ghi trên Google Calendar.</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section className="set-sec">
          <h4>Lịch</h4>
          <p>Từ <code>frontend/.env</code>.</p>
          <table className="table conn">
            <tbody>
              <tr><td>Múi giờ</td><td><code>{config.timezone}</code></td></tr>
              <tr><td>Calendar tự học</td><td><code>{config.autoStudyCalendar}</code></td></tr>
              <tr><td>Calendar bỏ qua</td><td>{config.ignoredCalendars.length ? config.ignoredCalendars.map((c) => <code key={c} style={{ marginRight: 6 }}>{c}</code>) : <span className="sub">không có</span>}</td></tr>
            </tbody>
          </table>
        </section>
      </div>
    </main>
  );
}
