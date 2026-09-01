import type { EventClickArg, EventContentArg, EventInput } from "@fullcalendar/core";
import FullCalendar from "@fullcalendar/react";
import timeGridPlugin from "@fullcalendar/timegrid";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import {
  AlertTriangle,
  BookOpenCheck,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock3,
  GraduationCap,
  ListChecks,
  MapPin,
  TrendingUp,
  UserRound,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AskXiaowoButton, Limitations, SourceBadge, WorkspaceEmpty, WorkspaceError, WorkspaceLoading } from "../components/WorkspacePrimitives";
import { apiGet } from "../lib/api";
import type {
  AcademicCourses,
  AcademicCourse,
  AcademicMeeting,
  AcademicOverview,
  AcademicProgram,
  AcademicSchedule,
  GradeRecord,
  ProgramCourse,
  SessionPayload,
} from "../types";

interface AcademicWorkspaceProps {
  session: SessionPayload;
  onAsk: (question: string) => void;
}

type AcademicData = {
  overview?: AcademicOverview;
  program?: AcademicProgram;
  courses?: AcademicCourses;
  schedule?: AcademicSchedule;
};

type AcademicErrors = Partial<Record<keyof AcademicData, string>>;

function valueOrDash(value: unknown): string {
  return value === null || value === undefined || value === "" ? "--" : String(value);
}

// ── 学期/建议学期分组排序:先秋季后春季(夏为小学期,介于秋春之间),年份/年级升序 ──
function semesterSortKey(semester: string): [number, number] | null {
  const match = /^(\d{4})年(秋|春)季学期$/.exec((semester || "").trim());
  if (!match) return null;
  return [parseInt(match[1], 10), match[2] === "秋" ? 0 : 1];
}

function termSortKey(term: string): [number, number] | null {
  const match = /^(\d+)([春秋夏])$/.exec((term || "").trim());
  if (!match) return null;
  const season = match[2] === "秋" ? 0 : match[2] === "夏" ? 1 : 2;
  return [parseInt(match[1], 10), season];
}

const TERM_GRADE_NAMES = ["大一", "大二", "大三", "大四", "大五", "大六"];

function termLabel(term: string): string {
  const key = termSortKey(term);
  if (key) {
    const grade = TERM_GRADE_NAMES[key[0] - 1] ?? `${key[0]}年级`;
    const season = key[1] === 0 ? "秋季" : key[1] === 1 ? "夏季" : "春季";
    return `${grade}·${season}（${term}）`;
  }
  return term || "未标注";
}

function sortGroups<T>(entries: [string, T[]][], keyFn: (s: string) => [number, number] | null): [string, T[]][] {
  return entries.sort((a, b) => {
    const ka = keyFn(a[0]);
    const kb = keyFn(b[0]);
    if (ka && kb) return ka[0] - kb[0] || ka[1] - kb[1];
    if (ka) return -1;
    if (kb) return 1;
    return a[0].localeCompare(b[0], "zh-Hans-CN");
  });
}

function GradeTable({ rows }: { rows: AcademicCourses["grades"] }) {
  if (!rows.length) {
    return <WorkspaceEmpty title="暂无成绩记录" detail="当前数据源没有返回可展示的成绩。" />;
  }
  const groups = new Map<string, GradeRecord[]>();
  for (const row of rows) {
    const key = row.semester || "未标注学期";
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }
  const ordered = sortGroups([...groups.entries()], semesterSortKey);
  return (
    <div className="grade-by-semester">
      {ordered.map(([semester, list]) => (
        <section className="semester-group" key={semester}>
          <div className="semester-heading">
            <h3>{semester}</h3>
            <em>{list.length} 门</em>
          </div>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead><tr><th>课程</th><th>学分</th><th>成绩</th><th>绩点</th><th aria-label="操作" /></tr></thead>
              <tbody>{list.map((row, index) => (
                <tr key={`${row.course_name}-${row.semester}-${index}`}>
                  <td><strong>{valueOrDash(row.course_name)}</strong></td>
                  <td>{valueOrDash(row.credits)}</td>
                  <td>{valueOrDash(row.score)}</td>
                  <td>{valueOrDash(row.grade_point)}</td>
                  <td />
                </tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}

function OverviewView({ data, onAsk }: { data: AcademicOverview; onAsk: (question: string) => void }) {
  const metrics = [
    { label: "累计绩点", value: valueOrDash(data.metrics.gpa), detail: `${data.metrics.grade_count} 门已出分课程` },
    { label: "已修学分", value: valueOrDash(data.metrics.completed_credits), detail: "按当前成绩记录统计" },
    { label: "本学期学分", value: valueOrDash(data.metrics.current_credits), detail: "按当前课表统计" },
  ];
  return (
    <div className="academic-section">
      <div className="data-band" aria-label="学业摘要">
        {metrics.map((metric) => <div className="data-band__item" key={metric.label}><span>{metric.label}</span><strong>{metric.value}</strong><small>{metric.detail}</small></div>)}
      </div>
      <section className="content-section">
        <div className="section-heading"><h2>全部成绩</h2><AskXiaowoButton question="分析我的近期成绩和学习趋势" onAsk={onAsk} /></div>
        <GradeTable rows={data.grades ?? data.recent_grades} />
      </section>
      <Limitations items={data.limitations} />
    </div>
  );
}

function ProgramView({ data, onAsk }: { data: AcademicProgram; onAsk: (question: string) => void }) {
  const total = data.program.totalCredits ?? data.program.total_credits;
  const modules = data.program.modules ?? [];
  const courses = data.program.courses ?? [];
  return (
    <div className="academic-section">
      {data.banner && <div className={`program-banner ${data.source.demo ? "program-banner--demo" : "program-banner--fallback"}`}><GraduationCap size={19} /><strong>{data.banner}</strong></div>}
      <div className="program-title-row">
        <div><h2>{data.program.name || "当前培养方案"}</h2><p>{[data.program.college, data.program.grade, total ? `总学分 ${total}` : ""].filter(Boolean).join("，")}</p></div>
        <SourceBadge source={data.source} />
      </div>
      {modules.length ? (
        <div className="module-ledger" role="table" aria-label="培养方案模块统计">
          <div className="module-ledger__header" role="row">
            <span role="columnheader">课程模块</span>
            <span role="columnheader">要求学分</span>
            <span role="columnheader">课程数量</span>
          </div>
          {modules.map((module, index) => (
            <div className="module-ledger__row" role="row" key={`${module.category}-${index}`}>
              <strong role="cell">{module.category || "未分类"}</strong>
              <span role="cell"><b>{valueOrDash(module.required_credits)}</b> 学分</span>
              <span role="cell"><b>{valueOrDash(module.course_count)}</b> 门课程</span>
            </div>
          ))}
        </div>
      ) : <WorkspaceEmpty title="暂无模块统计" detail="当前培养方案没有返回模块学分信息。" />}
      <section className="content-section">
        <div className="section-heading"><h2>方案课程（按学期）</h2></div>
        {(() => {
          const groups = new Map<string, ProgramCourse[]>();
          for (const course of courses) {
            const key = course.term || "未标注";
            groups.set(key, [...(groups.get(key) ?? []), course]);
          }
          const ordered = sortGroups([...groups.entries()], termSortKey);
          if (!ordered.length) return <WorkspaceEmpty title="暂无方案课程" detail="当前方案没有可展示的课程记录。" />;
          return ordered.map(([term, list]) => (
            <div className="term-group" key={term}>
              <div className="term-heading"><strong>{termLabel(term)}</strong><em>{list.length} 门</em></div>
              <div className="data-table-wrap">
                <table className="data-table">
                  <thead><tr><th>课程</th><th>类别</th><th>性质</th><th>学分</th><th aria-label="操作" /></tr></thead>
                  <tbody>{list.map((course, index) => (
                    <tr key={`${course.code}-${index}`}>
                      <td><strong>{course.name || "未命名课程"}</strong><small className="cell-subtitle">{course.code || ""}</small></td>
                      <td>{valueOrDash(course.category)}</td><td>{valueOrDash(course.required)}</td><td>{valueOrDash(course.credit)}</td>
                      <td><AskXiaowoButton compact question={`点评培养方案中的课程“${course.name || course.code}”`} onAsk={onAsk} /></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          ));
        })()}
      </section>
      <Limitations items={data.limitations} />
    </div>
  );
}

function CoursesView({ data, onAsk }: { data: AcademicCourses; onAsk: (question: string) => void }) {
  return (
    <div className="academic-section">
      <section className="content-section content-section--first">
        <div className="section-heading"><h2>在修课程</h2><SourceBadge source={data.source} /></div>
        {data.courses.length ? <div className="course-list">
          {data.courses.map((course, index) => (
            <article className="course-row" key={`${course.course_code}-${index}`}>
              <div className="course-row__code">{course.course_code || "--"}</div>
              <div className="course-row__main"><strong>{course.course_name || "未命名课程"}</strong><span>{[course.teacher, course.time, course.location].filter(Boolean).join("，")}</span></div>
              <div className="course-row__credit">{valueOrDash(course.credits)} 学分</div>
              <AskXiaowoButton compact question={`点评我正在修的课程“${course.course_name || course.course_code}”`} onAsk={onAsk} />
            </article>
          ))}
        </div> : <WorkspaceEmpty title="暂无在修课程" detail="当前课业数据源没有返回本学期课程。" />}
      </section>
      <section className="content-section">
        <div className="section-heading"><h2>全部成绩</h2></div>
        <GradeTable rows={data.grades} />
      </section>
      <Limitations items={data.limitations} />
    </div>
  );
}

const weekdayNames = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const periodBands = [
  ["第一节", "07:50-09:25"],
  ["第二节", "09:45-12:10"],
  ["第三节", "14:00-15:35"],
  ["第四节", "15:55-18:20"],
  ["晚间", "19:30-21:55"],
] as const;

type SelectedMeeting = {
  course: AcademicCourse;
  meeting: AcademicMeeting;
  conflict: boolean;
};

function parseLocalDate(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const result = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isNaN(result.getTime()) ? null : result;
}

function addDays(value: Date, days: number): Date {
  const result = new Date(value);
  result.setDate(result.getDate() + days);
  return result;
}

function localDateKey(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function shortDate(value: Date): string {
  return `${String(value.getMonth() + 1).padStart(2, "0")}月${String(value.getDate()).padStart(2, "0")}日`;
}

function minutes(value: string): number {
  const [hours, mins] = value.split(":").map(Number);
  return hours * 60 + mins;
}

function renderCalendarEvent(info: EventContentArg) {
  const details = info.event.extendedProps as SelectedMeeting;
  const { course, meeting, conflict } = details;
  return (
    <div className="schedule-event-card" data-conflict={conflict || undefined}>
      <div className="schedule-event-card__time">
        <span>{meeting.start_time}</span>
        {conflict && <AlertTriangle size={13} aria-label="与同时间课程重叠" />}
      </div>
      <strong>{course.course_name || "未命名课程"}</strong>
      <span>{course.teacher || "教师待确认"}</span>
      <span>{meeting.period_label || `${meeting.start_time}-${meeting.end_time}`}</span>
      <span>{meeting.location || "教室待确认"}</span>
      <time>{meeting.end_time}</time>
    </div>
  );
}

function ScheduleView({ data, onAsk }: { data: AcademicSchedule; onAsk: (question: string) => void }) {
  const semesterStart = useMemo(() => parseLocalDate(data.semester_start) ?? new Date(), [data.semester_start]);
  const totalWeeks = Math.max(1, data.total_weeks || 1);
  const initialWeek = Math.min(totalWeeks, Math.max(1, data.current_week ?? 1));
  const [selectedWeek, setSelectedWeek] = useState(initialWeek);
  const [selectedMeeting, setSelectedMeeting] = useState<SelectedMeeting | null>(null);

  useEffect(() => {
    setSelectedWeek(Math.min(totalWeeks, Math.max(1, data.current_week ?? 1)));
  }, [data.current_week, data.semester_code, totalWeeks]);

  const weekStart = useMemo(
    () => addDays(semesterStart, (selectedWeek - 1) * 7),
    [selectedWeek, semesterStart],
  );
  const weekEnd = useMemo(() => addDays(weekStart, 6), [weekStart]);
  const events = useMemo<EventInput[]>(() => {
    const candidates: SelectedMeeting[] = [];
    for (const course of data.courses) {
      for (const meeting of course.meetings ?? []) {
        if (meeting.week_numbers.includes(selectedWeek)) {
          candidates.push({ course, meeting, conflict: false });
        }
      }
    }
    for (let left = 0; left < candidates.length; left += 1) {
      for (let right = left + 1; right < candidates.length; right += 1) {
        const a = candidates[left].meeting;
        const b = candidates[right].meeting;
        if (
          a.weekday === b.weekday
          && minutes(a.start_time) < minutes(b.end_time)
          && minutes(b.start_time) < minutes(a.end_time)
        ) {
          candidates[left].conflict = true;
          candidates[right].conflict = true;
        }
      }
    }
    return candidates.map((details) => {
      const meetingDate = addDays(weekStart, details.meeting.weekday - 1);
      const dateKey = localDateKey(meetingDate);
      return {
        id: `${details.meeting.meeting_id}-${selectedWeek}`,
        title: details.course.course_name || "未命名课程",
        start: `${dateKey}T${details.meeting.start_time}:00`,
        end: `${dateKey}T${details.meeting.end_time}:00`,
        classNames: [
          "schedule-calendar-event",
          ...(details.conflict ? ["schedule-calendar-event--conflict"] : []),
        ],
        extendedProps: details,
      };
    });
  }, [data.courses, selectedWeek, weekStart]);

  const onEventClick = useCallback((info: EventClickArg) => {
    setSelectedMeeting(info.event.extendedProps as SelectedMeeting);
  }, []);

  return (
    <div className="academic-section schedule-section">
      <div className="schedule-toolbar">
        <div className="schedule-toolbar__title">
          <span>{data.semester_code || data.semester || "当前学期"}</span>
          <h2>第 {selectedWeek} 周</h2>
          <p>{shortDate(weekStart)} - {shortDate(weekEnd)}</p>
        </div>
        <div className="schedule-toolbar__actions">
          <SourceBadge source={data.source} />
          <div className="schedule-week-controls" aria-label="教学周切换">
            <button
              type="button"
              className="icon-button"
              aria-label="前一周"
              title="前一周"
              disabled={selectedWeek <= 1}
              onClick={() => setSelectedWeek((value) => Math.max(1, value - 1))}
            >
              <ChevronLeft size={19} />
            </button>
            <button
              type="button"
              className="secondary-button"
              disabled={data.current_week === null}
              onClick={() => data.current_week && setSelectedWeek(data.current_week)}
            >
              本周
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label="后一周"
              title="后一周"
              disabled={selectedWeek >= totalWeeks}
              onClick={() => setSelectedWeek((value) => Math.min(totalWeeks, value + 1))}
            >
              <ChevronRight size={19} />
            </button>
          </div>
        </div>
      </div>

      <div className="schedule-period-key" aria-label="课时划分">
        {periodBands.map(([label, range]) => (
          <div key={label}><strong>{label}</strong><span>{range}</span></div>
        ))}
      </div>

      {!events.length && (
        <div className="schedule-week-empty" role="status">第 {selectedWeek} 周暂无可确认课程</div>
      )}
      <div className="schedule-calendar-scroll" aria-label={`第${selectedWeek}周课程表`}>
        <div className="schedule-calendar">
          <FullCalendar
            key={localDateKey(weekStart)}
            plugins={[timeGridPlugin]}
            initialView="timeGridWeek"
            initialDate={localDateKey(weekStart)}
            firstDay={1}
            weekends
            allDaySlot={false}
            headerToolbar={false}
            dayHeaderContent={(info) => (
              <span className="schedule-day-heading" data-today={info.isToday || undefined}>
                <strong>{weekdayNames[info.date.getDay()]}</strong>
                <small>{String(info.date.getMonth() + 1).padStart(2, "0")}/{String(info.date.getDate()).padStart(2, "0")}</small>
              </span>
            )}
            slotMinTime="07:30:00"
            slotMaxTime="22:15:00"
            slotDuration="00:15:00"
            slotLabelInterval="01:00:00"
            slotLabelFormat={{ hour: "2-digit", minute: "2-digit", hour12: false }}
            displayEventTime={false}
            nowIndicator={selectedWeek === data.current_week}
            slotEventOverlap={false}
            eventMinHeight={54}
            eventShortHeight={54}
            expandRows
            height="auto"
            events={events}
            eventContent={renderCalendarEvent}
            eventClick={onEventClick}
          />
        </div>
      </div>

      {data.unparsed_courses.length > 0 && (
        <section className="schedule-unparsed" aria-labelledby="schedule-unparsed-title">
          <div className="schedule-unparsed__heading">
            <AlertTriangle size={18} />
            <div><h3 id="schedule-unparsed-title">待确认的排课</h3><p>以下课程缺少可验证的星期、周次或起止时间，未放入课表。</p></div>
          </div>
          <div className="schedule-unparsed__list">
            {data.unparsed_courses.map((course, index) => (
              <article key={`${course.course_code}-${index}`}>
                <strong>{course.course_name || course.course_code || "未命名课程"}</strong>
                <span>{course.raw_schedule || "暂无原始排课文本"}</span>
                <small>{course.reason}</small>
              </article>
            ))}
          </div>
        </section>
      )}
      <Limitations items={data.limitations} />

      <Dialog.Root open={selectedMeeting !== null} onOpenChange={(open) => { if (!open) setSelectedMeeting(null); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          {selectedMeeting && (
            <Dialog.Content className="dialog-content schedule-detail-dialog">
              <div className="dialog-heading">
                <div>
                  <Dialog.Title>{selectedMeeting.course.course_name || "未命名课程"}</Dialog.Title>
                  <Dialog.Description>{selectedMeeting.course.course_code || "课程编号待确认"}</Dialog.Description>
                </div>
                <Dialog.Close className="icon-button" aria-label="关闭"><X size={18} /></Dialog.Close>
              </div>
              {selectedMeeting.conflict && (
                <div className="schedule-conflict-notice" role="alert"><AlertTriangle size={17} />该课程与同一时间的另一门课程重叠，请核对教务课表。</div>
              )}
              <dl className="schedule-detail-list">
                <div><dt><Clock3 size={16} />上课时间</dt><dd>{selectedMeeting.meeting.day} {selectedMeeting.meeting.start_time}-{selectedMeeting.meeting.end_time}<small>{selectedMeeting.meeting.period_label} · {selectedMeeting.meeting.weeks}</small></dd></div>
                <div><dt><MapPin size={16} />上课地点</dt><dd>{selectedMeeting.meeting.location || "待确认"}</dd></div>
                <div><dt><UserRound size={16} />授课教师</dt><dd>{selectedMeeting.course.teacher || "待确认"}</dd></div>
              </dl>
              <div className="schedule-detail-dialog__footer">
                <SourceBadge source={data.source} />
                <AskXiaowoButton
                  question={`点评课表中的课程“${selectedMeeting.course.course_name || selectedMeeting.course.course_code}”`}
                  onAsk={(question) => {
                    setSelectedMeeting(null);
                    onAsk(question);
                  }}
                />
              </div>
            </Dialog.Content>
          )}
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

export function AcademicWorkspace({ session, onAsk }: AcademicWorkspaceProps) {
  const [data, setData] = useState<AcademicData>({});
  const [errors, setErrors] = useState<AcademicErrors>({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const [overview, program, courses, schedule] = await Promise.allSettled([
      apiGet<AcademicOverview>("/academic/overview"),
      apiGet<AcademicProgram>("/academic/program"),
      apiGet<AcademicCourses>("/academic/courses"),
      apiGet<AcademicSchedule>("/academic/schedule"),
    ]);
    const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;
    setData({
      ...(overview.status === "fulfilled" ? { overview: overview.value } : {}),
      ...(program.status === "fulfilled" ? { program: program.value } : {}),
      ...(courses.status === "fulfilled" ? { courses: courses.value } : {}),
      ...(schedule.status === "fulfilled" ? { schedule: schedule.value } : {}),
    });
    setErrors({
      ...(overview.status === "rejected" ? { overview: message(overview.reason, "无法加载学业总览。") } : {}),
      ...(program.status === "rejected" ? { program: message(program.reason, "无法加载培养方案。") } : {}),
      ...(courses.status === "rejected" ? { courses: message(courses.reason, "无法加载课程记录。") } : {}),
      ...(schedule.status === "rejected" ? { schedule: message(schedule.reason, "无法加载课表。") } : {}),
    });
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load, session.principal.id]);

  const hasData = Object.values(data).some(Boolean);
  if (loading && !hasData) return <WorkspaceLoading label="正在读取你的学业档案" />;
  if (!hasData) return <WorkspaceError message={Object.values(errors)[0] ?? "无法加载学业数据。"} onRetry={() => void load()} />;
  const identity = data.overview?.identity ?? session.principal.profile;
  const unavailable = (key: keyof AcademicData) => (
    <WorkspaceError message={errors[key] ?? "当前数据暂不可用。"} onRetry={() => void load()} />
  );
  return (
    <Tabs.Root className="workspace-tabs academic-workspace" defaultValue="overview">
      <header className="workspace-header">
        <div><h1>我的学业</h1><p>{[identity?.major, identity?.grade].filter(Boolean).join(" · ") || "身份档案待确认"}</p></div>
        {data.overview && <SourceBadge source={data.overview.source} />}
      </header>
      <Tabs.List className="tabs-list" aria-label="学业视图">
        <Tabs.Trigger value="overview"><TrendingUp size={15} />总览</Tabs.Trigger>
        <Tabs.Trigger value="program"><BookOpenCheck size={15} />培养方案</Tabs.Trigger>
        <Tabs.Trigger value="courses"><ListChecks size={15} />课程</Tabs.Trigger>
        <Tabs.Trigger value="schedule"><CalendarDays size={15} />课表</Tabs.Trigger>
      </Tabs.List>
      <div className="tabs-scroll">
        <Tabs.Content value="overview">{data.overview ? <OverviewView data={data.overview} onAsk={onAsk} /> : unavailable("overview")}</Tabs.Content>
        <Tabs.Content value="program">{data.program ? <ProgramView data={data.program} onAsk={onAsk} /> : unavailable("program")}</Tabs.Content>
        <Tabs.Content value="courses">{data.courses ? <CoursesView data={data.courses} onAsk={onAsk} /> : unavailable("courses")}</Tabs.Content>
        <Tabs.Content value="schedule">{data.schedule ? <ScheduleView data={data.schedule} onAsk={onAsk} /> : unavailable("schedule")}</Tabs.Content>
      </div>
    </Tabs.Root>
  );
}
