export type StarterPromptIcon =
  | "calendar"
  | "rules"
  | "reviews"
  | "services"
  | "activities"
  | "library"
  | "schedule"
  | "grades"
  | "program"
  | "recommend"
  | "conflict"
  | "agenda";

export interface StarterPromptDefinition {
  id: string;
  title: string;
  description: string;
  question: string;
  icon: StarterPromptIcon;
}

export const publicStarterPrompts: StarterPromptDefinition[] = [
  {
    id: "academic-calendar",
    title: "本学期校历",
    description: "查看开学、考试周与重要教学节点",
    question: "请查询本学期校历安排，并列出开学、考试周和重要教学节点。",
    icon: "calendar",
  },
  {
    id: "course-selection-rules",
    title: "退补选规则",
    description: "了解退选、补选和关键时间要求",
    question: "请说明中国科大本学期退补选规则和关键时间要求。",
    icon: "rules",
  },
  {
    id: "course-reviews",
    title: "课程与教师评价",
    description: "查询公开课程评价与教师信息",
    question: "如何查询中国科大的课程评价和教师评价？",
    icon: "reviews",
  },
  {
    id: "campus-services",
    title: "常用办事入口",
    description: "快速找到教务、缴费和认证平台",
    question: "请列出中国科大常用办事入口，并说明各自用途。",
    icon: "services",
  },
  {
    id: "recent-activities",
    title: "近期校园活动",
    description: "查找近期可以报名的公开活动",
    question: "近期有哪些可以报名的中国科大校园活动？",
    icon: "activities",
  },
  {
    id: "library-services",
    title: "图书馆服务",
    description: "了解借阅、研讨室和开放信息",
    question: "请介绍中国科大图书馆的借阅、研讨室预约和开放信息。",
    icon: "library",
  },
];

export const personalStarterPrompts: StarterPromptDefinition[] = [
  {
    id: "today-schedule",
    title: "今日课表",
    description: "查看今天的课程、时间和地点",
    question: "请查询我今天的课表，并按时间列出课程和地点。",
    icon: "schedule",
  },
  {
    id: "grades-gpa",
    title: "成绩与 GPA",
    description: "汇总已验证成绩与绩点",
    question: "请汇总我的成绩与 GPA，并标明数据来源和仍然缺失的信息。",
    icon: "grades",
  },
  {
    id: "program-progress",
    title: "培养方案进度",
    description: "核对已修课程与模块完成情况",
    question: "请根据我的培养方案核对当前完成进度，并列出尚未完成的模块。",
    icon: "program",
  },
  {
    id: "next-term-recommendation",
    title: "下学期推荐",
    description: "基于方案与已修课程生成建议",
    question: "请根据我的培养方案和已修课程推荐下学期课程。",
    icon: "recommend",
  },
  {
    id: "course-conflict",
    title: "独立冲突检查",
    description: "核对计划课程与现有课表",
    question: "请帮我检查一门计划课程与现有课表是否冲突。",
    icon: "conflict",
  },
  {
    id: "weekly-agenda",
    title: "本周日程",
    description: "汇总课程与个人安排",
    question: "请汇总我本周的课程与个人日程，并按日期排序。",
    icon: "agenda",
  },
];

export function starterPromptsFor(personalAcademic: boolean): StarterPromptDefinition[] {
  return personalAcademic ? personalStarterPrompts : publicStarterPrompts;
}
