import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { LoginPage } from "./LoginPage";

const config = (auth_mode: "demo" | "cas" | "anonymous") =>
  ({ auth_mode, version: "test", features: {} as never } as never);

describe("正式登录页（2026-09-29）", () => {
  test("演示模式：CAS 正式入口常驻（跳转链接），演示身份作为备选", async () => {
    const onDemoLogin = vi.fn();
    render(<LoginPage config={config("demo")} busy={false} onDemoLogin={onDemoLogin} onClose={() => {}} />);
    // 正式入口：演示模式下是按钮（点它给说明，不跳 404）；文案与分区仍在
    expect(screen.getByText(/中国科学技术大学 · 统一身份认证/)).toBeInTheDocument();
    expect(screen.getByText(/统一认证入口已预留/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /统一认证登录/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /统一认证登录/ }));
    expect(screen.getByRole("status")).toHaveTextContent(/XIAOWO_AUTH_MODE/);
    // 备选：演示身份
    await userEvent.click(screen.getByRole("button", { name: /进入演示身份/ }));
    expect(onDemoLogin).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/不读取任何真实个人信息/)).toBeInTheDocument();
  });

  test("统一认证模式：跳转链接指向 CAS，且不出现演示入口与演示模式说明", () => {
    render(<LoginPage config={config("cas")} busy={false} onDemoLogin={() => {}} onClose={() => {}} />);
    const link = screen.getByRole("link", { name: /统一认证登录/ });
    expect(link).toHaveAttribute("href", "/api/v1/auth/cas/login");
    expect(screen.queryByRole("button", { name: /进入演示身份/ })).toBeNull();
    expect(screen.queryByText(/统一认证入口已预留/)).toBeNull();  // cas 模式不再提示"演示模式"
  });

  test("匿名模式：说明会话说清楚；关掉登录页可继续匿名", async () => {
    const onClose = vi.fn();
    render(<LoginPage config={config("anonymous")} busy={false} onDemoLogin={() => {}} onClose={onClose} />);
    expect(screen.getByText(/匿名模式/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "先匿名体验" }));
    expect(onClose).toHaveBeenCalled();
  });

  test("登录失败时回显错误", () => {
    render(<LoginPage config={config("demo")} busy={false} error="演示登录失败。" onDemoLogin={() => {}} onClose={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("演示登录失败。");
  });
});
