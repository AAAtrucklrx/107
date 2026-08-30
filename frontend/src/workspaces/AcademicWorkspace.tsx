import * as Tabs from "@radix-ui/react-tabs";
import { BookOpenCheck, CalendarDays, GraduationCap, ListChecks, TrendingUp } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AskXiaowoButton, Limitations, SourceBadge, WorkspaceEmpty, WorkspaceError, WorkspaceLoading } from "../components/WorkspacePrimitives";
import { apiGet } from "../lib/api";
import type {
  AcademicCourses,
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

const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

function ScheduleView({ data, onAsk }: { data: AcademicSchedule; onAsk: (question: string) => void }) {
  return (
    <div className="academic-section">
      <div className="schedule-heading"><div><h2>{data.semester || "课表"}</h2><span>当前学期</span></div><SourceBadge source={data.source} /></div>
      <div className="week-grid" aria-label="周课表">
        {weekdays.map((day) => (
          <section className="day-column" key={day}>
            <h3>{day}</h3>
            <div className="day-column__courses">
              {data.courses.filter((course) => course.time?.includes(day)).map((course, index) => (
                <article className="schedule-course" key={`${course.course_code}-${index}`}>
                  <span>{course.time?.replace(day, "").trim()}</span>
                  <strong>{course.course_name}</strong>
                  <small>{course.location || "地点待定"} · {course.teacher || "教师待定"}</small>
                  <AskXiaowoButton compact question={`点评课表中的课程“${course.course_name || course.course_code}”`} onAsk={onAsk} />
                </article>
              ))}
              {!data.courses.some((course) => course.time?.includes(day)) && <div className="day-column__empty">无课</div>}
            </div>
          </section>
        ))}
      </div>
      <Limitations items={data.limitations} />
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
