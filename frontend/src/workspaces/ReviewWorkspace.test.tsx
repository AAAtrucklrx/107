import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, vi } from "vitest";
import { ReviewWorkspace } from "./ReviewWorkspace";
import type { ReviewItemDetail, SessionPayload } from "../types";


const apiGetMock = vi.hoisted(() => vi.fn());
const apiMutationMock = vi.hoisted(() => vi.fn());

vi.mock("../lib/api", () => ({
  apiGet: apiGetMock,
  apiMutation: apiMutationMock,
}));

const detail: ReviewItemDetail = {
  item_id: "item-demo",
  title: "科大新栏目公开资料",
  status: "draft",
  scope: "campus",
  category: "announcement",
  ttl_days: 7,
  normalized_url: "https://new.ustc.edu.cn/column/notice",
  final_url: "https://new.ustc.edu.cn/column/notice",
  fetched_at: "2026-08-27T00:00:00Z",
  current_version: 1,
  updated_at: 1787788800,
  content_type: "text/html",
  snapshot_hash: "a".repeat(64),
  raw_snapshot: "公开资料原文。",
  versions: [{
    version_id: "version-model",
    version_number: 1,
    kind: "model",
    content_text: "模型清洗后的公开资料。",
    content_hash: "b".repeat(64),
    actor_key: "worker",
    created_at: 1787788800,
  }],
  chunks: [{
    chunk_id: "chunk-one",
    version_id: "version-model",
    position: 0,
    content_text: "模型清洗后的公开资料。",
    approval_status: "pending",
    approved: false,
    expires_at: null,
  }],
  diff: "-公开资料原文。\n+模型清洗后的公开资料。",
};

const session: SessionPayload = {
  principal: {
    id: "PB25111691",
    auth_mode: "demo",
    authenticated: true,
    profile: { id: "PB25111691", name: "测试", major: "人工智能", grade: "2025级" },
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

beforeEach(() => {
  apiGetMock.mockReset();
  apiMutationMock.mockReset();
  apiGetMock.mockImplementation((path: string) => {
    if (path === "/admin/generations") return Promise.resolve({
      namespace: "demo",
      active_generation_id: "gen-current",
      previous_generation_id: "gen-previous",
      activated_at: 1787788800,
      can_rollback: true,
      publish_busy: false,
    });
    if (path.startsWith("/admin/review-items/item-demo")) return Promise.resolve(detail);
    if (path.startsWith("/admin/review-items")) return Promise.resolve({ items: [detail], namespace: "demo" });
    if (path.startsWith("/admin/feedback")) return Promise.resolve({ items: [] });
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });
  apiMutationMock.mockImplementation((path: string) => {
    if (path.endsWith("/refetch")) return Promise.resolve({ job_id: "refetch-one", status: "queued", created: true });
    if (path.endsWith("/source-trust-proposals")) return Promise.resolve({ proposal_id: "proposal-one" });
    if (path === "/admin/generations/rollback") return Promise.resolve({ generation_id: "gen-previous" });
    return Promise.resolve({});
  });
});

test("review item can queue a refetch and submit a source rule proposal", async () => {
  const user = userEvent.setup();
  render(<ReviewWorkspace session={session} />);
  await user.click(await screen.findByRole("button", { name: /科大新栏目公开资料/ }));

  await user.click(await screen.findByRole("button", { name: "重新抓取" }));
  expect(await screen.findByText("已加入异步复抓队列。")).toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: "来源治理" }));
  expect(screen.getByLabelText("精确域名")).toHaveValue("new.ustc.edu.cn");
  expect(screen.getByLabelText("栏目路径")).toHaveValue("/column");
  await user.type(screen.getByLabelText("机构名称"), "中国科学技术大学测试栏目");
  await user.type(screen.getByLabelText("核验依据"), "已核验该栏目归属和长期公开发布职责。");
  await user.click(screen.getByRole("button", { name: "加入变更建议" }));

  await waitFor(() => expect(apiMutationMock).toHaveBeenCalledWith(
    "/admin/review-items/item-demo/source-trust-proposals",
    "csrf-demo",
    expect.objectContaining({ method: "POST" }),
  ));
  const proposalCall = apiMutationMock.mock.calls.find(
    (call) => call[0] === "/admin/review-items/item-demo/source-trust-proposals",
  );
  const body = JSON.parse(proposalCall?.[2]?.body as string);
  expect(body).toMatchObject({
    host: "new.ustc.edu.cn",
    path_prefix: "/column",
    level: "reliable_independent",
    institution: "中国科学技术大学测试栏目",
  });
  expect(await screen.findByText("来源规则建议已加入 Git diff 导出队列。")).toBeInTheDocument();
});

test("generation governance shows isolated pointers and confirms rollback", async () => {
  const user = userEvent.setup();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<ReviewWorkspace session={session} />);
  await user.click(await screen.findByRole("tab", { name: "发布治理" }));

  expect(await screen.findByText("gen-current")).toBeInTheDocument();
  expect(screen.getByText("gen-previous")).toBeInTheDocument();
  expect(screen.getByText("演示索引")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "回滚上一版本" }));

  await waitFor(() => expect(apiMutationMock).toHaveBeenCalledWith(
    "/admin/generations/rollback",
    "csrf-demo",
    expect.objectContaining({ method: "POST" }),
  ));
  expect(await screen.findByText("已切换到 gen-previous。")).toBeInTheDocument();
});

test("chunk decisions send an explicit three-state approval value", async () => {
  const user = userEvent.setup();
  render(<ReviewWorkspace session={session} />);
  await user.click(await screen.findByRole("button", { name: /科大新栏目公开资料/ }));
  await user.click(screen.getByRole("tab", { name: /分块 1/ }));
  expect(screen.getByRole("button", { name: "待定" })).toHaveAttribute("data-active", "true");

  await user.click(screen.getByRole("button", { name: "批准" }));
  await waitFor(() => expect(apiMutationMock).toHaveBeenCalledWith(
    "/admin/review-items/item-demo/chunks/chunk-one",
    "csrf-demo",
    expect.objectContaining({ method: "POST" }),
  ));
  const approvalCall = apiMutationMock.mock.calls.find(
    (call) => call[0] === "/admin/review-items/item-demo/chunks/chunk-one",
  );
  expect(JSON.parse(approvalCall?.[2]?.body as string)).toEqual({
    approval_status: "approved",
  });
});
