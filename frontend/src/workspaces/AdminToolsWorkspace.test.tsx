import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, vi } from "vitest";
import type {
  CampusToolApplication,
  CampusToolAuditEntry,
  ManagedCampusTool,
  SessionPayload,
} from "../types";
import { AdminToolsWorkspace } from "./AdminToolsWorkspace";

const apiGetMock = vi.hoisted(() => vi.fn());
const apiMutationMock = vi.hoisted(() => vi.fn());

vi.mock("../lib/api", () => ({
  apiGet: apiGetMock,
  apiMutation: apiMutationMock,
}));

const session: SessionPayload = {
  principal: {
    id: "PB25111691",
    auth_mode: "demo",
    authenticated: true,
    profile: { id: "PB25111691", name: "测试管理员", major: "计算机科学与技术", grade: "2025级" },
    is_admin: true,
    review_namespace: "demo",
  },
  capabilities: {
    public_chat: true,
    server_history: true,
    personal_academic: true,
    knowledge_review: true,
    production_publish: false,
  },
  csrf_token: "csrf-demo",
};

const application: CampusToolApplication = {
  application_id: "application-one",
  applicant_principal_id: "PB25110001",
  applicant_name_snapshot: "申请同学",
  name: "校车时刻",
  description: "查询校园班车时刻",
  display_description: "查询校园班车时刻",
  category: "life",
  submitted_url: "https://bus.ustc.edu.cn",
  normalized_url: "https://bus.ustc.edu.cn/",
  status: "pending",
  decision_reason: null,
  reviewed_at: null,
  version: 3,
  created_at: 1788192000,
  updated_at: 1788192000,
  tool_id: null,
  tool_status: null,
  unpublish_reason: null,
  unread: false,
};

const managedTool: ManagedCampusTool = {
  tool_id: "tool-one",
  application_id: application.application_id,
  name: application.name,
  description: application.description,
  display_description: application.display_description,
  category: application.category,
  url: application.normalized_url,
  normalized_url: application.normalized_url,
  status: "active",
  published_at: 1788195600,
  version: 2,
  applicant_principal_id: application.applicant_principal_id,
  applicant_name_snapshot: application.applicant_name_snapshot,
  unpublished_by: null,
  unpublished_at: null,
  unpublish_reason: null,
};

const auditEntry: CampusToolAuditEntry = {
  audit_id: "audit-one",
  actor_key: "demo:PB25111691",
  action: "application_approved",
  object_type: "application",
  object_id: application.application_id,
  before: { status: "pending" },
  after: { status: "approved" },
  reason: null,
  request_id: "request-one",
  created_at: 1788195600,
};

beforeEach(() => {
  apiGetMock.mockReset();
  apiMutationMock.mockReset();
  apiGetMock.mockImplementation((path: string) => {
    if (path.startsWith("/admin/campus-tool-applications")) {
      return Promise.resolve({ items: [application], namespace: "demo" });
    }
    if (path.startsWith("/admin/campus-tools")) {
      return Promise.resolve({ items: [managedTool], namespace: "demo" });
    }
    if (path.startsWith("/admin/campus-tool-audit")) {
      return Promise.resolve({ items: [auditEntry], namespace: "demo" });
    }
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });
  apiMutationMock.mockResolvedValue({});
});

test("filters applications and searches the submitted query", async () => {
  const user = userEvent.setup();
  render(<AdminToolsWorkspace session={session} />);

  expect((await screen.findAllByText("校车时刻")).length).toBeGreaterThanOrEqual(2);
  await user.selectOptions(screen.getByLabelText("申请状态"), "approved");
  await waitFor(() => expect(apiGetMock).toHaveBeenCalledWith(
    "/admin/campus-tool-applications?status=approved",
  ));

  await user.type(screen.getByLabelText("搜索工具管理记录"), "校车");
  await user.click(screen.getByRole("button", { name: "搜索" }));
  await waitFor(() => expect(apiGetMock).toHaveBeenCalledWith(
    "/admin/campus-tool-applications?status=approved&query=%E6%A0%A1%E8%BD%A6",
  ));
});

test("approves a pending application with optimistic concurrency", async () => {
  const user = userEvent.setup();
  render(<AdminToolsWorkspace session={session} />);

  await user.click(await screen.findByRole("button", { name: "通过并上架" }));
  expect(screen.getByRole("dialog", { name: "通过校园工具申请" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "确认通过" }));

  await waitFor(() => expect(apiMutationMock).toHaveBeenCalledWith(
    "/admin/campus-tool-applications/application-one/approve",
    "csrf-demo",
    expect.objectContaining({ method: "POST" }),
  ));
  const call = apiMutationMock.mock.calls.find((item) => item[0].endsWith("/approve"));
  expect(JSON.parse(call?.[2]?.body as string)).toEqual({ expected_version: 3 });
  expect(await screen.findByText("“校车时刻”已通过审核并上架。")).toBeInTheDocument();
});

test("requires a reason before rejecting an application", async () => {
  const user = userEvent.setup();
  render(<AdminToolsWorkspace session={session} />);

  await user.click(await screen.findByRole("button", { name: "驳回" }));
  const confirm = screen.getByRole("button", { name: "确认驳回" });
  expect(confirm).toBeDisabled();
  await user.type(screen.getByLabelText("驳回原因"), "链接并非校内公开服务");
  expect(confirm).toBeEnabled();
  await user.click(confirm);

  await waitFor(() => expect(apiMutationMock).toHaveBeenCalledWith(
    "/admin/campus-tool-applications/application-one/reject",
    "csrf-demo",
    expect.objectContaining({ method: "POST" }),
  ));
  const call = apiMutationMock.mock.calls.find((item) => item[0].endsWith("/reject"));
  expect(JSON.parse(call?.[2]?.body as string)).toEqual({
    expected_version: 3,
    reason: "链接并非校内公开服务",
  });
});

test("unpublishes active tools and exposes the audit trail", async () => {
  const user = userEvent.setup();
  render(<AdminToolsWorkspace session={session} />);

  await user.click(screen.getByRole("tab", { name: "已上架工具" }));
  await user.click(await screen.findByRole("button", { name: "下架" }));
  const confirm = screen.getByRole("button", { name: "确认下架" });
  await user.type(screen.getByLabelText("下架原因"), "服务链接已经停止维护");
  await user.click(confirm);

  await waitFor(() => expect(apiMutationMock).toHaveBeenCalledWith(
    "/admin/campus-tools/tool-one/unpublish",
    "csrf-demo",
    expect.objectContaining({ method: "POST" }),
  ));
  const call = apiMutationMock.mock.calls.find((item) => item[0].endsWith("/unpublish"));
  expect(JSON.parse(call?.[2]?.body as string)).toEqual({
    expected_version: 2,
    reason: "服务链接已经停止维护",
  });

  await user.click(screen.getByRole("tab", { name: "审计记录" }));
  expect(await screen.findByText("审核通过")).toBeInTheDocument();
  expect(screen.getByText("application-one")).toBeInTheDocument();
});

test("returns from mobile detail to the application queue", async () => {
  const user = userEvent.setup();
  render(<AdminToolsWorkspace session={session} />);

  await user.click(await screen.findByRole("button", { name: "返回申请队列" }));
  expect(screen.getByText("申请队列")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "返回申请队列" })).not.toBeInTheDocument();
});
