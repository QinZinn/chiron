import { Component, type ErrorInfo, type ReactNode } from 'react';
import { ApiError } from '../api/http';
import { config } from '../config';
import { useApp } from '../state/app';
import { navigate } from '../lib/route';

// ─────────────────────────────────────────────────────────────── marks

export function Logo({ size = 26, bg = '#2A303B' }: { size?: number; bg?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 26 26" fill="none" aria-hidden="true">
      <circle cx="13" cy="13" r="11" stroke="var(--frost)" strokeWidth="1.2" opacity=".7" />
      <path d="M13 4.5 L16 13 L13 21.5 L10 13 Z" fill="var(--frost)" opacity=".85" />
      <circle cx="13" cy="13" r="1.7" fill={bg} />
    </svg>
  );
}

export function HeroMark() {
  return (
    <svg width="58" height="58" viewBox="0 0 26 26" fill="none" aria-hidden="true" style={{ marginBottom: 18 }}>
      <circle cx="13" cy="13" r="11" stroke="var(--frost)" strokeWidth="1" opacity=".55" />
      <circle cx="13" cy="13" r="7.5" stroke="var(--frost2)" strokeWidth=".5" opacity=".4" />
      <path d="M13 3.5 L16.4 13 L13 22.5 L9.6 13 Z" fill="var(--frost)" opacity=".8" />
      <circle cx="13" cy="13" r="1.6" fill="#2E3440" />
    </svg>
  );
}

export function AiAvatar() {
  return (
    <div className="ai-avatar">
      <svg width="15" height="15" viewBox="0 0 26 26" fill="none" aria-hidden="true">
        <path d="M13 4.5 L16 13 L13 21.5 L10 13 Z" fill="var(--frost)" />
      </svg>
    </div>
  );
}

export function Constellation() {
  return (
    <svg className="constellation" viewBox="0 0 1000 700" preserveAspectRatio="none" aria-hidden="true">
      <g stroke="var(--frost)" strokeWidth="0.7" fill="none" opacity=".5">
        <path d="M120 154 L262 210 L338 112 L470 168 L556 96" />
        <path d="M262 210 L300 330 L418 392 L560 344" />
        <path d="M690 210 L790 148 L876 200 L830 316 L690 210" />
        <path d="M560 344 L690 210" />
      </g>
      <g fill="#D8DEE9" opacity=".55">
        <circle cx="120" cy="154" r="2" /><circle cx="262" cy="210" r="2.6" /><circle cx="338" cy="112" r="1.8" />
        <circle cx="470" cy="168" r="2.2" /><circle cx="556" cy="96" r="1.8" /><circle cx="300" cy="330" r="2" />
        <circle cx="418" cy="392" r="2.4" /><circle cx="560" cy="344" r="2" /><circle cx="690" cy="210" r="2.6" />
        <circle cx="790" cy="148" r="1.9" /><circle cx="876" cy="200" r="2.1" /><circle cx="830" cy="316" r="1.8" />
      </g>
    </svg>
  );
}

// ─────────────────────────────────────────────────────────────── header

export function PageHeader({ title, after, children, line = true }: { title: ReactNode; after?: ReactNode; children?: ReactNode; line?: boolean }) {
  const { settings, updateSettings } = useApp();
  return (
    <header className={`hdr${line ? ' hdr-line' : ''}`}>
      <button
        className="icon-btn"
        title={settings.sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        onClick={() => updateSettings({ sidebarCollapsed: !settings.sidebarCollapsed })}
      >
        <i className="ph ph-sidebar-simple" />
      </button>
      <span className="hdr-title">{title}</span>
      {after}
      {children && <div className="hdr-right">{children}</div>}
    </header>
  );
}

// ─────────────────────────────────────────────────────────────── state panels

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="loading">
      <span className="spin" />
      {label}
    </div>
  );
}

function hintFor(err: ApiError): ReactNode {
  if (err.kind === 'not_configured') return <>Fill in the value in <code>frontend/.env</code> (see <code>.env.example</code>) and restart <code>npm run dev</code>.</>;
  if (err.kind === 'unreachable' || err.kind === 'timeout') {
    switch (err.service) {
      case 'Mnemosyne':
        return <>Check that Mnemosyne is running at <code>{config.mnemosyneUrl}</code>. If it is and this still fails, CORS may be blocking this origin — the frontend must be served on port 5173 or 4173.</>;
      case 'Knowledge Store':
        return <>Check that the Knowledge Store is running (<code>ks serve</code>) and that <code>CHIRON_KS_URL</code> points at its <code>KS_HTTP_PORT</code>.</>;
    }
  }
  if (err.kind === 'http' && err.status === 403 && err.service === 'Knowledge Store') {
    return <><code>CHIRON_KS_TOKEN</code> does not match the Knowledge Store's <code>KS_HTTP_TOKEN</code>.</>;
  }
  return null;
}

/** Render any failure as a panel scoped to the area that failed. */
export function ErrorNotice({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  let title: string;
  let detail: ReactNode = null;
  let hint: ReactNode = null;
  let tone = 'notice-err';
  let icon = 'ph-plugs';

  if (error instanceof ApiError) {
    hint = hintFor(error);
    switch (error.kind) {
      case 'unreachable':
        title = `Cannot reach ${error.service}`;
        break;
      case 'timeout':
        title = `${error.service} did not respond in time`;
        detail = error.message;
        icon = 'ph-hourglass-medium';
        break;
      case 'not_configured':
        title = `${error.service} is not configured`;
        detail = error.message;
        tone = 'notice-warn';
        icon = 'ph-gear-six';
        break;
      default:
        title = `${error.service} returned an error${error.status ? ` (HTTP ${error.status})` : ''}`;
        detail = error.message;
        icon = 'ph-warning-circle';
    }
  } else {
    title = 'Something unexpected went wrong';
    detail = error instanceof Error ? error.message : String(error);
    icon = 'ph-warning-circle';
  }

  return (
    <div className={`notice ${tone}`} role="alert">
      <i className={`ph ${icon}`} />
      <div>
        <div className="notice-title">{title}</div>
        {detail && <div>{detail}</div>}
        {hint && !compact && <div>{hint}</div>}
        {onRetry && (
          <button className="btn btn-soft" onClick={onRetry}>
            <i className="ph ph-arrow-clockwise" />Retry
          </button>
        )}
      </div>
    </div>
  );
}

export function ComingSoon({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="soon-hero">
      <span className="tag tag-pur">Coming soon</span>
      <h3>{title}</h3>
      {children}
    </div>
  );
}

/**
 * Shown wherever a learner's own data would go when this browser holds no
 * usable token. Separate from ErrorNotice on purpose: "not signed in" is a
 * thing the learner can fix in ten seconds, not a failure report.
 */
export function NeedToken() {
  const { token, userError, userLoading, health } = useApp();
  if (health.mnemosyne === 'down') {
    return <ErrorNotice error={new ApiError({ kind: 'unreachable', service: 'Mnemosyne', message: '' })} />;
  }
  // A token in hand but /me still in flight is neither "logged in" nor
  // "logged out" — saying "not signed in" here would blame the learner for a
  // request that has not finished.
  if (token && userLoading) return <Loading label="Checking token…" />;
  const rejected = userError instanceof ApiError && userError.kind === 'unauthenticated';
  return (
    <div className={`notice ${rejected ? 'notice-warn' : 'notice-info'}`}>
      <i className={`ph ${rejected ? 'ph-key' : 'ph-user-circle'}`} />
      <div>
        <div className="notice-title">{rejected ? 'Token not accepted' : 'Not signed in to Mnemosyne'}</div>
        <div>
          {rejected
            ? <>Mnemosyne rejected the saved token — it may have been revoked. Paste another one in Settings.</>
            : <>Paste your token in Settings. No token yet? Create one with <code>cargo run -p backend -- create-user &lt;email&gt;</code> in <code>mnemosyne/</code>.</>}
        </div>
        {userError != null && !rejected && <div style={{ marginTop: 6 }}><ErrorNotice error={userError} compact /></div>}
        <button className="btn btn-soft" onClick={() => navigate({ view: 'settings' })}>
          <i className="ph ph-gear-six" />{token ? 'Open Settings' : 'Paste token'}
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────── error boundary

/**
 * Wraps each area. A render bug in one view shows this panel in that view's
 * slot; the sidebar and every other view keep working.
 */
export class AreaBoundary extends Component<{ children: ReactNode; area: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[chiron] ${this.props.area} crashed`, error, info.componentStack);
  }

  componentDidUpdate(prev: { area: string }) {
    if (prev.area !== this.props.area && this.state.error) this.setState({ error: null });
  }

  render() {
    if (this.state.error) {
      return (
        <div className="page">
          <div className="notice notice-err" role="alert">
            <i className="ph ph-bug" />
            <div>
              <div className="notice-title">The “{this.props.area}” area failed to render</div>
              <div>{this.state.error.message}</div>
              <button className="btn btn-soft" onClick={() => this.setState({ error: null })}>
                <i className="ph ph-arrow-clockwise" />Reload this area
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
