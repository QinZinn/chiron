import { useState } from 'react';
import { useApp } from '../state/app';
import { href, type Route } from '../lib/route';
import { Logo } from './ui';

interface NavItem {
  view: Route['view'];
  icon: string;
  label: string;
  badge?: { text: string; color: string; title: string };
  soon?: boolean;
}

export function Sidebar({ route }: { route: Route }) {
  const { dueCount, weakCount, todayEventCount, recent, user, token, health } = useApp();
  const [query, setQuery] = useState('');

  const items: NavItem[] = [
    {
      view: 'schedule',
      icon: 'ph-calendar-dots',
      label: 'Lịch học',
      badge: todayEventCount ? { text: String(todayEventCount), color: 'var(--frost2)', title: 'Sự kiện hôm nay' } : undefined,
    },
    {
      view: 'flashcards',
      icon: 'ph-cards-three',
      label: 'Thẻ ghi nhớ',
      badge: dueCount
        ? { text: dueCount >= 100 ? '100+' : String(dueCount), color: 'var(--yel)', title: 'Thẻ đến hạn ôn' }
        : undefined,
    },
    { view: 'quiz', icon: 'ph-check-square-offset', label: 'Quiz' },
    { view: 'knowledge', icon: 'ph-graph', label: 'Kiến thức' },
    {
      view: 'weak',
      icon: 'ph-warning-diamond',
      label: 'Điểm yếu',
      badge: weakCount ? { text: String(weakCount), color: 'var(--red)', title: 'Thẻ đang yếu' } : undefined,
    },
    { view: 'settings', icon: 'ph-gear-six', label: 'Cài đặt' },
  ];

  const q = query.trim().toLowerCase();
  const shown = q ? recent.filter((r) => r.title.toLowerCase().includes(q)) : recent;
  const activeSession = route.view === 'chat' ? route.sessionId : undefined;

  const initials = user ? user.email.replace(/@.*/, '').slice(0, 2) : '?';

  return (
    <aside className="sb">
      <div className="sb-brand">
        <Logo />
        <span>Chiron</span>
      </div>
      <a className="btn btn-primary btn-block sb-new" href={href({ view: 'chat' })} title="Cuộc trò chuyện mới">
        <i className="ph ph-plus" style={{ fontSize: 16 }} />
        <span>Cuộc trò chuyện mới</span>
      </a>
      <label className="sb-search" title="Tìm trong các phiên gần đây">
        <i className="ph ph-magnifying-glass" />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Tìm kiếm" />
      </label>

      <div className="sb-section">Không gian học</div>
      <nav className="sb-nav">
        {items.map((it) => (
          <a
            key={it.view}
            className={`nv${route.view === it.view ? ' nv-on' : ''}`}
            href={href({ view: it.view } as Route)}
            title={it.label}
          >
            <i className={`ph ${it.icon}`} />
            <span className="nvl">{it.label}</span>
            {it.badge && (
              <span className="nv-badge" style={{ color: it.badge.color }} title={it.badge.title}>
                {it.badge.text}
              </span>
            )}
            {it.soon && <span className="nv-soon">Sắp có</span>}
          </a>
        ))}
      </nav>

      <div className="sb-bottom">
        <div className="sb-section" style={{ paddingTop: 16 }}>Gần đây</div>
        <div className="sb-recent">
          {shown.map((r) => (
            <a
              key={`${r.kind}:${r.id}`}
              className={`nv nv-recent${activeSession === r.id ? ' nv-on' : ''}`}
              href={href({ view: 'chat', kind: r.kind, sessionId: r.id })}
              title={`${r.subtitle} · ${r.title} · ${new Date(r.updatedAt).toLocaleString('vi-VN')}`}
            >
              <i className={`ph ${r.kind === 'socratic' ? 'ph-student' : r.subtitle === 'Giải bài' ? 'ph-function' : 'ph-chat-circle-dots'}`} />
              <span className="nvl">{r.title}</span>
              {r.ended && <span className="nv-ended">đã kết thúc</span>}
            </a>
          ))}
          {shown.length === 0 && (
            <div className="sb-recent-empty">{q ? 'Không có phiên nào khớp.' : user ? 'Chưa có phiên Học bài nào.' : ''}</div>
          )}
        </div>
        <hr className="rule" style={{ margin: '0 0 10px' }} />
        <a className="sb-user" href={href({ view: 'settings' })} title="Cài đặt người học">
          <div className="avatar">{initials}</div>
          <div className="sb-user-text">
            <div className="sb-user-name">
              {user ? user.email : health.mnemosyne === 'down' ? 'Không tải được người học' : token ? 'Đang tải…' : 'Chưa đăng nhập'}
            </div>
            <div className="sb-user-sub">
              {user ? user.learning_style || 'Người học · Mnemosyne' : health.mnemosyne === 'down' ? 'Mnemosyne mất kết nối' : 'Dán token trong Cài đặt'}
            </div>
          </div>
          <i className="ph ph-gear" style={{ fontSize: 15, color: 'var(--dim)', marginLeft: 'auto', display: 'var(--lbl)' }} />
        </a>
      </div>
    </aside>
  );
}
