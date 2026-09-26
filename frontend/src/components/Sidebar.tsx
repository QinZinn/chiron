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
  const { dueCount, weakCount, todoOpenCount, recent, user, token, health } = useApp();
  const [query, setQuery] = useState('');

  const items: NavItem[] = [
    {
      view: 'todos',
      icon: 'ph-list-checks',
      label: 'To-do',
      badge: todoOpenCount ? { text: String(todoOpenCount), color: 'var(--frost2)', title: 'Open items' } : undefined,
    },
    {
      view: 'flashcards',
      icon: 'ph-cards-three',
      label: 'Flashcards',
      badge: dueCount
        ? { text: dueCount >= 100 ? '100+' : String(dueCount), color: 'var(--yel)', title: 'Cards due for review' }
        : undefined,
    },
    { view: 'quiz', icon: 'ph-check-square-offset', label: 'Quiz' },
    { view: 'feynman', icon: 'ph-chalkboard-teacher', label: 'Teach back' },
    { view: 'knowledge', icon: 'ph-graph', label: 'Knowledge' },
    { view: 'notes', icon: 'ph-scan', label: 'Note scan' },
    {
      view: 'weak',
      icon: 'ph-warning-diamond',
      label: 'Weak spots',
      badge: weakCount ? { text: String(weakCount), color: 'var(--red)', title: 'Cards still weak' } : undefined,
    },
    { view: 'settings', icon: 'ph-gear-six', label: 'Settings' },
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
      <a className="btn btn-primary btn-block sb-new" href={href({ view: 'chat' })} title="New conversation">
        <i className="ph ph-plus" style={{ fontSize: 16 }} />
        <span>New conversation</span>
      </a>
      <label className="sb-search" title="Search recent sessions">
        <i className="ph ph-magnifying-glass" />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" />
      </label>

      <div className="sb-section">Study space</div>
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
            {it.soon && <span className="nv-soon">Soon</span>}
          </a>
        ))}
      </nav>

      <div className="sb-bottom">
        <div className="sb-section" style={{ paddingTop: 16 }}>Recent</div>
        <div className="sb-recent">
          {shown.map((r) => (
            <a
              key={`${r.kind}:${r.id}`}
              className={`nv nv-recent${activeSession === r.id ? ' nv-on' : ''}`}
              href={href({ view: 'chat', kind: r.kind, sessionId: r.id })}
              title={`${r.subtitle} · ${r.title} · ${new Date(r.updatedAt).toLocaleString('en-GB')}`}
            >
              <i className={`ph ${r.kind === 'socratic' ? 'ph-student' : r.subtitle === 'Solve' ? 'ph-function' : 'ph-chat-circle-dots'}`} />
              <span className="nvl">{r.title}</span>
              {r.ended && <span className="nv-ended">ended</span>}
            </a>
          ))}
          {shown.length === 0 && (
            <div className="sb-recent-empty">{q ? 'No matching sessions.' : user ? 'No study sessions yet.' : ''}</div>
          )}
        </div>
        <hr className="rule" style={{ margin: '0 0 10px' }} />
        <a className="sb-user" href={href({ view: 'settings' })} title="Learner settings">
          <div className="avatar">{initials}</div>
          <div className="sb-user-text">
            <div className="sb-user-name">
              {user ? user.email : health.mnemosyne === 'down' ? 'Could not load learner' : token ? 'Loading…' : 'Not signed in'}
            </div>
            <div className="sb-user-sub">
              {user ? user.learning_style || 'Learner · Mnemosyne' : health.mnemosyne === 'down' ? 'Mnemosyne unreachable' : 'Paste a token in Settings'}
            </div>
          </div>
          <i className="ph ph-gear" style={{ fontSize: 15, color: 'var(--dim)', marginLeft: 'auto', display: 'var(--lbl)' }} />
        </a>
      </div>
    </aside>
  );
}
