import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, vi } from "vitest";
import { CampusWorkspace } from "./CampusWorkspace";
import type {
  CampusActivities,
  CampusToolApplicationsMine,
  CampusToolNotifications,
  CampusToolsDirectory,
  CampusServices,
  SessionPayload,
} from "../types";

const services: CampusServices = {
  items: [
    {
      name: "本科生综合教务系统",
      url: "https://jw.ustc.edu.cn",
      description: "教务操作",
      category: "教务学习",
      featured: true,
      priority: 1,
    },
    {
      name: "图书馆",
      url: "https://lib.ustc.edu.cn",
      description: "借阅与研讨室",
      category: "生活服务",
      featured: true,
      priority: 4,
    },
    {
      name: "教学质量管理平台（评教）",
      url: "https://tqm.ustc.edu.cn",
      description: "课程评教",
      category: "教务学习",
      featured: false,
      priority: null,
    },
  ],
  categories: ["教务学习", "生活服务"],
  source: { kind: "curated_config", label: "仓库审核官方入口", stale: false },
};

const activities: CampusActivities = {
  items: [
    {
      id: "activity-1",
      title: "公开讲座",
      category: "讲座",
      location: "东区",
      start_time: "2026-09-01T10:00:00+08:00",
      url: "https://young.ustc.edu.cn/activity-1",
    },
  ],
  source: { kind: "young_live", label: "青春科大公开活动", stale: false },
  limitations: [],
};

const tools: CampusToolsDirectory = {
  items: [{
    tool_id: "campus-tool-one",
    application_id: "tool-app-one",
    name: "课程目录速查",
    description: "集中查看课程目录与课程基本信息。",
    display_description: "集中查看课程目录与课程基本信息。",
    category: "study",
    url: "https://catalog.ustc.edu.cn/",
    normalized_url: "https://catalog.ustc.edu.cn/",
    status: "active",
    published_at: 1_788_000_000,
    version: 1,
  }],
  categories: ["study", "life", "information", "community", "other"],
  source: { kind: "demo_fixture", label: "合成演示申请", demo: true, stale: false },
};

const mine: CampusToolApplicationsMine = {
  items: [{
    application_id: "tool-app-rejected",
    applicant_principal_id: "PB25111691",
    applicant_name_snapshot: "测试",
    name: "校园活动聚合",
    description: "",
    display_description: "暂无补充说明",
    category: "community",
    submitted_url: "https://young.ustc.edu.cn/",
    normalized_url: "https://young.ustc.edu.cn/",
    status: "rejected",
    decision_reason: "说明不足，无法确认具体用途。",
    reviewed_at: 1_788_000_100,
    version: 2,
    created_at: 1_788_000_000,
    updated_at: 1_788_000_100,
    tool_id: null,
    tool_status: null,
    unpublish_reason: null,
    unread: true,
  }],
  unread_count: 1,
  namespace: "demo",
};

const notifications: CampusToolNotifications = {
  items: [{
    notification_id: "notification-one",
    notification_type: "tool_rejected",
    title: "校园工具申请未通过",
    body: "“校园活动聚合”未通过审核：说明不足，无法确认具体用途。",
    application_id: "tool-app-rejected",
    tool_id: null,
    read_at: null,
    created_at: 1_788_000_100,
  }],
  namespace: "demo",
};

const anonymousSession: SessionPayload = {
  principal: { id: null, auth_mode: "anonymous", authenticated: false, profile: null, is_admin: false, review_namespace: null },
  capabilities: { public_chat: true, server_history: false, personal_academic: false, knowledge_review: false, production_publish: false },
  csrf_token: "anonymous-csrf",
};

const demoSession: SessionPayload = {
  principal: {
    id: "PB25111691",
    auth_mode: "demo",
    authenticated: true,
    profile: { id: "PB25111691", name: "测试", major: "计算机科学与技术", grade: "2025级" },
    is_admin: false,
    review_namespace: null,
  },
  capabilities: { public_chat: true, server_history: true, personal_academic: true, knowledge_review: false, production_publish: false },
  csrf_token: "demo-csrf",
};

const { apiGetMock, apiMutationMock } = vi.hoisted(() => ({ apiGetMock: vi.fn(), apiMutationMock: vi.fn() }));

vi.mock("../lib/api", () => ({
  apiGet: apiGetMock,
  apiMutation: apiMutationMock,
}));

beforeEach(() => {
  apiGetMock.mockReset();
  apiMutationMock.mockReset();
  apiMutationMock.mockResolvedValue({ application_id: "submitted-tool", status: "pending" });
  apiGetMock.mockImplementation((path: string) => {
    if (path.startsWith("/campus/tools/applications/mine")) return Promise.resolve(mine);
    if (path.startsWith("/campus/tools/notifications")) return Promise.resolve(notifications);
    if (path.startsWith("/campus/tools")) return Promise.resolve(tools);
    if (path.startsWith("/campus/services")) {
      if (path.includes("query=")) return Promise.resolve({ ...services, items: [services.items[1]] });
      return Promise.resolve(services);
    }
    if (path.startsWith("/campus/activities")) return Promise.resolve(activities);
    return Promise.reject(new Error(`Unexpected path: ${path}`));
  });
});

test("campus renders curated featured launch tiles and the remaining directory", async () => {
  render(<CampusWorkspace session={anonymousSession} />);

  expect(await screen.findByRole("heading", { name: "常用入口" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "打开 本科生综合教务系统" })).toHaveAttribute("href", "https://jw.ustc.edu.cn");
  expect(screen.getByRole("link", { name: "打开 图书馆" })).toHaveAttribute("href", "https://lib.ustc.edu.cn");
  expect(screen.getByRole("link", { name: "打开 教学质量管理平台（评教）" })).toBeInTheDocument();
  expect(screen.getAllByText("配置审核")).toHaveLength(3);
});

test("service and activity searches keep independent requests and state", async () => {
  const user = userEvent.setup();
  render(<CampusWorkspace session={anonymousSession} />);
  await screen.findByRole("heading", { name: "常用入口" });
  apiGetMock.mockClear();

  const serviceSearch = screen.getByRole("textbox", { name: "搜索校园服务" });
  await user.type(serviceSearch, "图书馆");
  await user.click(screen.getByRole("button", { name: "搜索" }));

  await waitFor(() => expect(apiGetMock).toHaveBeenCalledTimes(1));
  expect(decodeURIComponent(String(apiGetMock.mock.calls[0][0]))).toBe("/campus/services?query=图书馆");
  expect(screen.queryByRole("heading", { name: "常用入口" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "打开 图书馆" })).toHaveLength(1);

  await user.click(screen.getByRole("tab", { name: /活动/ }));
  const activitySearch = screen.getByRole("textbox", { name: "搜索校园活动" });
  expect(activitySearch).toHaveValue("");
  apiGetMock.mockClear();
  await user.type(activitySearch, "讲座");
  await user.click(screen.getByRole("button", { name: "搜索" }));

  await waitFor(() => expect(apiGetMock).toHaveBeenCalledTimes(1));
  expect(decodeURIComponent(String(apiGetMock.mock.calls[0][0]))).toBe("/campus/activities?query=讲座");
});

test("campus tools allow public browsing but require identity for submission", async () => {
  const user = userEvent.setup();
  render(<CampusWorkspace session={anonymousSession} />);
  await user.click(screen.getByRole("tab", { name: /校园工具/ }));

  const tool = await screen.findByRole("link", { name: "打开 课程目录速查" });
  expect(tool).toHaveAttribute("href", "https://catalog.ustc.edu.cn/");
  expect(tool).toHaveTextContent("管理员审核");
  expect(screen.getByRole("tab", { name: "全部工具" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "我的申请" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /提交工具/ }));
  expect(screen.getByText("登录或进入演示身份后，才能提交校园工具申请。")).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("authenticated user submits a tool and sees rejection notification history", async () => {
  const user = userEvent.setup();
  render(<CampusWorkspace session={demoSession} />);
  await user.click(screen.getByRole("tab", { name: /校园工具/ }));
  await screen.findByRole("link", { name: "打开 课程目录速查" });

  await user.click(screen.getByRole("button", { name: /提交工具/ }));
  await user.type(screen.getByLabelText("工具名称"), "校历速查");
  await user.type(screen.getByLabelText("HTTPS 链接"), "https://calendar.ustc.edu.cn/");
  await user.type(screen.getByLabelText("功能说明（可选）"), "快速查看教学周安排。");
  await user.click(screen.getByRole("button", { name: "提交审核" }));

  await waitFor(() => expect(apiMutationMock).toHaveBeenCalledTimes(1));
  expect(apiMutationMock.mock.calls[0][0]).toBe("/campus/tools/applications");
  expect(JSON.parse(String(apiMutationMock.mock.calls[0][2].body))).toMatchObject({
    name: "校历速查",
    url: "https://calendar.ustc.edu.cn/",
    category: "study",
  });
  expect(await screen.findByText("申请已提交，管理员审核通过后会面向全校展示。")).toBeInTheDocument();
  expect(screen.getByText("校园工具申请未通过")).toBeInTheDocument();
  expect(screen.getAllByText(/说明不足，无法确认具体用途/).length).toBeGreaterThan(0);
  expect(screen.getByText("驳回原因：")).toBeInTheDocument();
});
