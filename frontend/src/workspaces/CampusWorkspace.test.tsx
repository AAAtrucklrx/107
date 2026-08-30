import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, vi } from "vitest";
import { CampusWorkspace } from "./CampusWorkspace";
import type { CampusActivities, CampusServices } from "../types";

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

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock("../lib/api", () => ({
  apiGet: apiGetMock,
}));

beforeEach(() => {
  apiGetMock.mockReset();
  apiGetMock.mockImplementation((path: string) => {
    if (path.startsWith("/campus/services")) {
      if (path.includes("query=")) return Promise.resolve({ ...services, items: [services.items[1]] });
      return Promise.resolve(services);
    }
    if (path.startsWith("/campus/activities")) return Promise.resolve(activities);
    return Promise.reject(new Error(`Unexpected path: ${path}`));
  });
});

test("campus renders curated featured launch tiles and the remaining directory", async () => {
  render(<CampusWorkspace />);

  expect(await screen.findByRole("heading", { name: "常用入口" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "打开 本科生综合教务系统" })).toHaveAttribute("href", "https://jw.ustc.edu.cn");
  expect(screen.getByRole("link", { name: "打开 图书馆" })).toHaveAttribute("href", "https://lib.ustc.edu.cn");
  expect(screen.getByRole("link", { name: "打开 教学质量管理平台（评教）" })).toBeInTheDocument();
  expect(screen.getAllByText("配置审核")).toHaveLength(3);
});

test("service and activity searches keep independent requests and state", async () => {
  const user = userEvent.setup();
  render(<CampusWorkspace />);
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
