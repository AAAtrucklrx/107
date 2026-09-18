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


test("反馈页签显示来源清单并能流转状态（修「只能看不能办」）", async () => {
  // 反馈此前只写不读、status 永远停在 open、管理页没有任何按钮。
  const user = userEvent.setup();
  apiGetMock.mockImplementation((path: string) => {
    if (path === "/admin/review-items/stats") return Promise.resolve(stats);
    if (path.startsWith("/admin/feedback")) {
      return Promise.resolve({
        items: [{
          id: 7,
          answer_id: "answer-abcdefghij",
          run_id: "run-abcdefghij",
          category: "outdated",
          status: "open",
          created_at: "2026-09-17T03:00:00Z",
          handled_by: null,
          handled_at: null,
          resolution: "",
          detail: "开放时间好像变了。",
          sources: [
            { title: "图书馆公告", display_url: "https://lib.ustc.edu.cn/a", domain: "lib.ustc.edu.cn" },
            { title: "另一篇", display_url: "https://lib.ustc.edu.cn/b", domain: "lib.ustc.edu.cn" },
          ],
        }],
      });
    }
    if (path === "/admin/generations") return Promise.resolve({
      namespace: "demo", active_generation_id: "gen-current", previous_generation_id: null,
      activated_at: 1787788800, can_rollback: false, publish_busy: false,
    });
    if (path.startsWith("/admin/review-items")) return Promise.resolve({ items: [detail], namespace: "demo" });
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });

  render(<ReviewWorkspace session={session} />);
  await user.click(await screen.findByRole("tab", { name: /回答反馈/ }));

  expect(await screen.findByText("信息已过期")).toBeTruthy();
  expect(screen.getByText("待处理")).toBeTruthy();
  expect(screen.getByText(/该次回答的来源 2 条/)).toBeTruthy();
  expect(screen.getByText("1 条待处理 / 共 1 条")).toBeTruthy();

  await user.click(screen.getByRole("button", { name: "标为已办结" }));
  await waitFor(() => expect(apiMutationMock).toHaveBeenCalled());
  const call = apiMutationMock.mock.calls.find((item) => item[0] === "/admin/feedback/7");
  expect(call).toBeTruthy();
  expect(call?.[2]).toMatchObject({ method: "PATCH" });
  expect(JSON.parse(String(call?.[2]?.body))).toEqual({ status: "handled", resolution: "" });
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


test("未登录用户的反馈会标注来源并能处理（P1-1 回归）", async () => {
  const user = userEvent.setup();
  apiGetMock.mockImplementation((path: string) => {
    if (path === "/admin/generations") return Promise.resolve({
      namespace: "demo", active_generation_id: "gen-current", previous_generation_id: null,
      activated_at: 1787788800, can_rollback: false, publish_busy: false,
    });
    if (path === "/admin/review-items/stats") return Promise.resolve(stats);
    if (path.startsWith("/admin/feedback")) return Promise.resolve({
      items: [{
        id: 9, answer_id: "answer-1", run_id: "run-1", namespace: "anonymous",
        category: "outdated", status: "open", created_at: "2026-09-17T00:00:00Z",
        detail: "开放时间疑似过期", resolution: "", sources: [],
      }],
    });
    if (path.startsWith("/admin/review-items")) return Promise.resolve({ items: [detail], namespace: "demo" });
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });

  render(<ReviewWorkspace session={session} />);
  await user.click(await screen.findByRole("tab", { name: /回答反馈/ }));
  // 匿名反馈必须在列表里，并明确标注来源（原来它落 anonymous 命名空间，后台根本看不到）
  expect(await screen.findByText("未登录用户")).toBeTruthy();
  expect(screen.getByText("开放时间疑似过期")).toBeTruthy();
  expect(screen.getByRole("button", { name: "标为已办结" })).toBeTruthy();
});


test("反馈详情能看到提问与回答原文（2026-09-17）", async () => {
  const user = userEvent.setup();
  apiGetMock.mockImplementation((path: string) => {
    if (path === "/admin/generations") return Promise.resolve({
      namespace: "demo", active_generation_id: "gen-current", previous_generation_id: null,
      activated_at: 1787788800, can_rollback: false, publish_busy: false, pending_proposals: 0,
    });
    if (path === "/admin/review-items/stats") return Promise.resolve(stats);
    if (path.startsWith("/admin/feedback/7/transcript")) return Promise.resolve({
      feedback_id: 7, run_id: "run-7",
      question: "中国科学技术大学食堂开放时间",
      answer: "同学你好，食堂开放时间以现场公告为准。",
      mode: "auto", sources: [], limitations: ["本条回答来自语义缓存"],
      created_at: "2026-09-17T02:00:00Z",
    });
    if (path.startsWith("/admin/feedback")) return Promise.resolve({
      items: [{
        id: 7, answer_id: "answer-7", run_id: "run-7", namespace: "demo",
        category: "outdated", status: "open", created_at: "2026-09-17T02:00:00Z",
        detail: "开放时间疑似过期", resolution: "", sources: [],
      }],
    });
    if (path.startsWith("/admin/review-items")) return Promise.resolve({ items: [detail], namespace: "demo" });
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });

  render(<ReviewWorkspace session={session} />);
  await user.click(await screen.findByRole("tab", { name: /回答反馈/ }));
  // 折叠块默认收起：点开才懒加载原文
  await user.click(await screen.findByText("查看提问与回答"));

  expect(await screen.findByText("中国科学技术大学食堂开放时间")).toBeInTheDocument();
  expect(await screen.findByText(/食堂开放时间以现场公告为准/)).toBeInTheDocument();
  expect(screen.getByText("本条回答来自语义缓存")).toBeInTheDocument();
});

