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
  auto_approved: false,
  pre_review: {
    stability: "stable",
    sensitivity: "clean",
    duplication: "unique",
    relevance: "on_topic",
    reason: "校级公告属长期稳定的校园信息，无个人敏感信息。",
    model: "test-model",
    fallback_reason: null,
    level: "official_primary",
    category: "announcement",
    auto_approve_eligible: false,
  },
};

const stats = {
  namespace: "demo" as const,
  window_seconds: 86400,
  items: { draft: 3, active: 29 },
  draft_backlog: 42,
  active_items: 29,
  ingested: 19,
  dead: { SENSITIVE_CONTENT: 1, OFF_TOPIC: 7 },
  off_topic: 7,
  pre_reviewed: 12,
  auto_eligible: 4,
  auto_approved: 0,
  active_documents: 31,
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
    // ⚠️ 必须排在通用 `/admin/review-items` 分支**之前**（它用 startsWith 兜底）
    if (path === "/admin/review-items/stats") return Promise.resolve(stats);
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

test("队列超过一页时能继续加载（修「审核过多看不到」）", async () => {
  // 回归：后端每页只给 50 条并返回 next_cursor，前端原来完全不用分页 →
  // 条目超过 50 后再也看不到、审不了。
  const user = userEvent.setup();
  const second = { ...detail, item_id: "item-demo-2", title: "第二条公开资料", updated_at: 1787788700 };
  apiGetMock.mockImplementation((path: string) => {
    if (path === "/admin/review-items/stats") return Promise.resolve(stats);
    if (path.startsWith("/admin/review-items?") && path.includes("cursor=")) {
      return Promise.resolve({ items: [second], namespace: "demo", next_cursor: null });
    }
    if (path.startsWith("/admin/review-items")) {
      return Promise.resolve({ items: [detail], namespace: "demo", next_cursor: "cur-1" });
    }
    if (path === "/admin/generations") return Promise.resolve({
      namespace: "demo", active_generation_id: "gen-current", previous_generation_id: null,
      activated_at: 1787788800, can_rollback: false, publish_busy: false,
    });
    if (path.startsWith("/admin/feedback")) return Promise.resolve({ items: [] });
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });

  render(<ReviewWorkspace session={session} />);
  expect(await screen.findByRole("button", { name: /科大新栏目公开资料/ })).toBeTruthy();
  expect(screen.getByText(/还有更多/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /第二条公开资料/ })).toBeNull();

  await user.click(screen.getByRole("button", { name: "加载更多" }));
  expect(await screen.findByRole("button", { name: /第二条公开资料/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "加载更多" })).toBeNull();
});


test("审核动作后详情不被清空，条目被钉在列表里（修「审核完就跳出」）", async () => {
  // 回归：任何审核动作都会改状态（开始审核/批准或排除分块 → in_review、批准 →
  // pending_publish、拒绝 → rejected），条目随即离开当前筛选。旧实现会让队列刷新
  // 顺手把详情面板清空 → 审核流程被打断（用户反馈"审核过多直接跳出"）。
  const user = userEvent.setup();
  render(<ReviewWorkspace session={session} />);
  await user.click(await screen.findByRole("button", { name: /科大新栏目公开资料/ }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "科大新栏目公开资料" })).toBeTruthy());

  // 模拟：审核后该条目离开了当前筛选（队列不再返回它），详情接口返回新状态
  apiGetMock.mockImplementation((path: string) => {
    if (path === "/admin/review-items/stats") return Promise.resolve(stats);
    if (path.startsWith("/admin/review-items/item-demo")) {
      return Promise.resolve({ ...detail, status: "rejected" });
    }
    if (path.startsWith("/admin/review-items")) return Promise.resolve({ items: [], namespace: "demo" });
    if (path === "/admin/generations") return Promise.resolve({
      namespace: "demo", active_generation_id: "gen-current", previous_generation_id: null,
      activated_at: 1787788800, can_rollback: false, publish_busy: false,
    });
    if (path.startsWith("/admin/feedback")) return Promise.resolve({ items: [] });
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });

  await user.click(screen.getByRole("button", { name: "拒绝" }));

  await waitFor(() => expect(screen.queryByText("选择一条内容开始核验")).toBeNull());
  expect(screen.getByRole("heading", { name: "科大新栏目公开资料" })).toBeTruthy();
  // 并且该条目被钉在队列列表里（以最新状态显示）
  expect(screen.getAllByText(/已拒绝/).length).toBeGreaterThanOrEqual(1);
});


test("daily card and pre-review verdict render with real numbers", async () => {
  const user = userEvent.setup();
  render(<ReviewWorkspace session={session} />);

  // 进料日报：数字来自 /admin/review-items/stats
  expect(await screen.findByRole("heading", { name: "进料日报" })).toBeTruthy();
  expect(screen.getByText("跑题拦下").parentElement?.textContent).toContain("7");
  expect(screen.getByText("线上文档").parentElement?.textContent).toContain("31");
  expect(screen.getByText("待审积压").parentElement?.textContent).toContain("42");

  // 预审四项判定 + 理由要展示给审批人
  await user.click(await screen.findByRole("button", { name: /科大新栏目公开资料/ }));
  await waitFor(() => expect(screen.getByText(/校级公告属长期稳定的校园信息/)).toBeTruthy());
  expect(screen.getByText("相关性").parentElement?.textContent).toContain("范围内");
  expect(screen.getByText("敏感性").parentElement?.textContent).toContain("无敏感信息");
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
  render(<ReviewWorkspace session={session} />);
  await user.click(await screen.findByRole("tab", { name: "发布治理" }));

  expect(await screen.findByText("gen-current")).toBeInTheDocument();
  expect(screen.getByText("gen-previous")).toBeInTheDocument();
  expect(screen.getByText("演示索引")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "回滚上一版本" }));
  expect(screen.getByRole("dialog", { name: "回滚知识索引" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "确认回滚" }));

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
  expect(screen.getByRole("radio", { name: "待定" })).toBeChecked();

  await user.click(screen.getByRole("radio", { name: "批准" }));
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
