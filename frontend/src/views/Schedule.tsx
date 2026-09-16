/**
 * Lịch học — Google Calendar read straight through withone.ai (NOT via Horae,
 * which has no HTTP API). Read-only: study blocks are created by Horae alone.
 *
 * An empty Auto-Study calendar (or none at all) is a normal state — Horae may
 * simply not have planned that week — so it renders as information, never as
 * an error.
 */
import { useEffect, useMemo, useState } from 'react';
import { loadRange, type CalEvent } from '../api/calendar';
import { config } from '../config';
import { useApp } from '../state/app';
import { useAsync } from '../lib/useAsync';
import { addDays, clock, dayLabel, keyBoundsPadded, mondayOf, shortDate, todayKey } from '../lib/time';
import { ErrorNotice, Loading, PageHeader } from '../components/ui';

export function ScheduleView() {
  const { health, setTodayEventCount } = useApp();
  const [week, setWeek] = useState(() => mondayOf(todayKey()));
  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(week, i)), [week]);
  const today = todayKey();

  const configured = health.proxy?.gcal.configured;
  const q = useAsync(
    () => {
      const { timeMin, timeMax } = keyBoundsPadded(days[0], addDays(days[6], 1));
      return loadRange(timeMin, timeMax);
    },
    [week],
    configured !== false,
  );

  const byDay = useMemo(() => {
    const m = new Map<string, CalEvent[]>(days.map((d) => [d, []]));
    for (const e of q.data?.events ?? []) m.get(e.dayKey)?.push(e);
    return m;
  }, [q.data, days]);

  useEffect(() => {
    if (q.data && days.includes(today)) setTodayEventCount(byDay.get(today)?.length ?? 0);
  }, [q.data, byDay, days, today, setTodayEventCount]);

  const weekEvents = days.flatMap((d) => byDay.get(d) ?? []);
  const autoCount = weekEvents.filter((e) => e.isAuto).length;

  return (
    <main className="main">
      <PageHeader title="Lịch học">
        <span className="tag tag-dim tag-sm" title="Frontend không tạo, sửa hay xoá sự kiện — việc đó thuộc Horae">
          <i className="ph ph-lock-simple" style={{ marginRight: 5 }} />Chỉ đọc
        </span>
        <button className="icon-btn" title="Tuần trước" onClick={() => setWeek((w) => addDays(w, -7))}><i className="ph ph-caret-left" /></button>
        <button className="btn btn-secondary btn-soft" onClick={() => setWeek(mondayOf(todayKey()))}>Tuần này</button>
        <button className="icon-btn" title="Tuần sau" onClick={() => setWeek((w) => addDays(w, 7))}><i className="ph ph-caret-right" /></button>
      </PageHeader>
      <div className="page">
        <div className="page-head">
          <div>
            <h2>Tuần {shortDate(days[0])} – {shortDate(days[6])}</h2>
            <p>
              Đọc thẳng từ Google Calendar qua withone.ai. Block tự học do Horae xếp nằm trong calendar
              “{config.autoStudyCalendar}”, tiêu đề bắt đầu bằng <code>[Auto]</code>.
            </p>
          </div>
          <button className="btn btn-secondary btn-soft" onClick={q.reload} disabled={configured === false}>
            <i className="ph ph-arrow-clockwise" />Tải lại
          </button>
        </div>

        {configured === false ? (
          <div className="notice notice-warn" style={{ marginTop: 18 }}>
            <i className="ph ph-gear-six" />
            <div>
              <div className="notice-title">Chưa cấu hình kết nối Google Calendar</div>
              <div>
                Lịch học đọc Calendar qua withone.ai. Điền {health.proxy?.gcal.missing.map((m, i) => (
                  <span key={m}>{i > 0 && ', '}<code>{m}</code></span>
                ))} trong <code>frontend/.env</code> rồi khởi động lại <code>npm run dev</code>.
              </div>
            </div>
          </div>
        ) : (
          <>
            <div className="stats">
              <div><div className="stat-k">Block tự học</div><div className="stat-v" style={{ color: 'var(--frost)' }}>{q.data ? autoCount : '—'}</div></div>
              <div><div className="stat-k">Sự kiện khác</div><div className="stat-v">{q.data ? weekEvents.length - autoCount : '—'}</div></div>
              <div><div className="stat-k">Calendar</div><div className="stat-v" style={{ color: 'var(--mut)' }}>{q.data ? q.data.calendars.length : '—'}</div></div>
            </div>
            <hr className="rule" style={{ marginBottom: 18 }} />

            {q.loading && <Loading label="Đang đọc Google Calendar qua withone.ai…" />}
            {q.error != null && <ErrorNotice error={q.error} onRetry={q.reload} />}

            {q.data && !q.loading && (
              <div className="days">
                {q.data.autoStudy === null ? (
                  <div className="notice notice-info">
                    <i className="ph ph-info" />
                    <div>
                      Tài khoản Google đã kết nối chưa có calendar tên “{config.autoStudyCalendar}” — nơi Horae ghi block
                      tự học. Khi Horae tạo calendar và xếp block, chúng sẽ hiện ở đây.
                    </div>
                  </div>
                ) : autoCount === 0 ? (
                  <div className="notice notice-info">
                    <i className="ph ph-info" />
                    <div>
                      Tuần này chưa có block tự học nào trong “{config.autoStudyCalendar}” — Horae chưa xếp block cho khoảng
                      thời gian này. Khi Horae ghi lên Calendar, block sẽ hiện ở đây.
                    </div>
                  </div>
                ) : null}

                {q.data.failures.length > 0 && (
                  <div className="notice notice-warn">
                    <i className="ph ph-warning" />
                    <div>
                      <div className="notice-title">Không đọc được {q.data.failures.length} calendar</div>
                      {q.data.failures.map((f) => <div key={f.calendar.id}>{f.calendar.name}: {f.message}</div>)}
                    </div>
                  </div>
                )}

                {days.map((d) => {
                  const list = byDay.get(d) ?? [];
                  return (
                    <div key={d}>
                      <div className="day-head">
                        <span className={`day-name${d === today ? ' day-today' : ''}`}>{dayLabel(d)}</span>
                        {d === today && <span className="tag tag-frost tag-sm">Hôm nay</span>}
                      </div>
                      {list.length === 0 ? (
                        <div className="day-empty">Không có sự kiện.</div>
                      ) : (
                        list.map((e) => (
                          <div key={e.id} className={`ev${e.isAuto ? ' ev-auto' : ''}`}>
                            <span className="ev-time">{e.allDay ? 'Cả ngày' : `${clock(e.start)} – ${clock(e.end)}`}</span>
                            <span className="ev-title">
                              {e.isAuto && <span className="tag tag-frost tag-sm" style={{ marginRight: 8 }}>Horae</span>}
                              {e.title}
                            </span>
                            <span className="ev-cal">
                              <span className="dot" style={{ background: e.calendar.color ?? 'var(--dim)', marginRight: 6 }} />
                              {e.calendar.name}
                            </span>
                          </div>
                        ))
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}
