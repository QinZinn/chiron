import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react';

export type Mode = 'giai' | 'hoc' | 'hoi';

/** The three modes, in the design's order. All three now have a backend. */
const MODES: { id: Mode; icon: string; label: string; hint: string }[] = [
  { id: 'giai', icon: 'ph-function', label: 'Giải bài', hint: 'Giải từng bước, rồi hỏi thêm về bước bất kỳ' },
  { id: 'hoc', icon: 'ph-student', label: 'Học bài', hint: 'Socratic: Chiron hỏi gợi mở, không đưa đáp án' },
  { id: 'hoi', icon: 'ph-chat-circle-dots', label: 'Hỏi bài', hint: 'Hỏi đáp nhanh, trả lời thẳng' },
];

interface Props {
  /** Omitted on the session screens, where the mode is already decided. */
  mode?: Mode;
  onModeChange?: (mode: Mode) => void;
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  /** Composer is unusable (backend down, no user, session over…). */
  disabled?: boolean;
  /** Submit allowed even with empty text (starting a session needs no text). */
  allowEmpty?: boolean;
  sending?: boolean;
  placeholder: string;
  float?: boolean;
  topLeft?: ReactNode;
  status?: ReactNode;
  autoFocus?: boolean;
}

export function Composer(p: Props) {
  const ta = useRef<HTMLTextAreaElement>(null);

  // Grow with content up to the CSS max-height.
  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [p.value]);

  useEffect(() => {
    if (p.autoFocus && !p.disabled) ta.current?.focus();
  }, [p.autoFocus, p.disabled]);

  const canSend = !p.disabled && !p.sending && (p.allowEmpty || p.value.trim().length > 0);

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // isComposing: Vietnamese IMEs (Telex/VNI via ibus/fcitx) use Enter to
    // commit a composition — that Enter must not send the message.
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (canSend) p.onSubmit();
    }
  };

  return (
    <div className="composer-wrap">
      <div className={`composer${p.float ? ' composer-float' : ''}`}>
        <div className="composer-top">
          <div>{p.topLeft}</div>
          {/* The design's model picker. Mnemosyne exposes no model choice, so
              this names the engine that actually answers instead of offering
              a dropdown that would do nothing. */}
          <div className="engine-chip" title="Cả ba chế độ đều chạy trên Mnemosyne">
            <i className="ph ph-sparkle" />
            {p.mode === 'hoc' ? 'Socratic' : p.mode === 'giai' ? 'Giải từng bước' : 'Hỏi đáp'} · Mnemosyne
          </div>
        </div>
        <textarea
          ref={ta}
          rows={2}
          value={p.value}
          onChange={(e) => p.onChange(e.target.value)}
          onKeyDown={onKey}
          placeholder={p.placeholder}
          disabled={p.disabled}
          lang="vi"
        />
        {p.status && <div className="composer-status">{p.status}</div>}
        <div className="composer-bottom">
          <div className="modes" role="radiogroup" aria-label="Chế độ">
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                role="radio"
                aria-checked={m.id === p.mode}
                className={`md${m.id === p.mode ? ' md-on' : ''}`}
                disabled={!p.onModeChange}
                onClick={() => p.onModeChange?.(m.id)}
                title={m.hint}
              >
                <i className={`ph ${m.icon}`} />
                {m.label}
              </button>
            ))}
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              className="btn send-btn"
              onClick={p.onSubmit}
              disabled={!canSend}
              title={p.sending ? 'Đang gửi…' : 'Gửi (Enter)'}
            >
              {p.sending ? <span className="spin" /> : <i className="ph ph-paper-plane-right" />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
