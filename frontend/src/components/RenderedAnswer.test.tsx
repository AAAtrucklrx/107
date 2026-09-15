import { render, screen } from "@testing-library/react";
import { RenderedAnswer } from "./RenderedAnswer";

test("github alerts syntax becomes a semantic callout with data attributes", () => {
  const content = "正文。\n\n> [!WARNING]\n> 教务接口不可用，以下为本地缓存。\n\n结束。";
  const { container } = render(<RenderedAnswer content={content} sources={[]} />);

  const callout = container.querySelector("blockquote.callout");
  expect(callout).not.toBeNull();
  expect(callout?.getAttribute("data-callout")).toBe("WARNING");
  expect(callout?.className).toContain("callout--warning");
  expect(screen.getByText(/教务接口不可用/)).toBeInTheDocument();
  // 标记本身必须被剥离，不能残留给用户看
  expect(container.textContent).not.toContain("[!WARNING]");
});

test("callout keeps inline markdown and does not swallow the block", () => {
  const content = "> [!NOTE]\n> **样本量过少**，仅供参考。\n> 第二行。";
  const { container } = render(<RenderedAnswer content={content} sources={[]} />);

  expect(container.querySelector("blockquote.callout strong")?.textContent).toBe("样本量过少");
  expect(container.textContent).toContain("第二行。");
});

test("plain blockquotes stay plain (review quotes are not turned into callouts)", () => {
  const { container } = render(<RenderedAnswer content={'> “老师讲得很好”——某同学(2025秋)'} sources={[]} />);

  expect(container.querySelector("blockquote.callout")).toBeNull();
  expect(container.querySelector("blockquote")).not.toBeNull();
});

test("unknown alert kind is left untouched", () => {
  const content = "> [!BOGUS]\n> 不应被当成提示块";
  const { container } = render(<RenderedAnswer content={content} sources={[]} />);

  expect(container.querySelector("blockquote.callout")).toBeNull();
  expect(container.textContent).toContain("[!BOGUS]");
});

test("citation pills still render inside a callout body", () => {
  const content = "> [!IMPORTANT]\n> 该操作需在官方系统完成[1]。";
  const { container } = render(
    <RenderedAnswer
      content={content}
      sources={[{
        source_id: "s1", title: "教务处", display_url: "https://www.teach.ustc.edu.cn/",
        institution: "中国科学技术大学教务处", domain: "www.teach.ustc.edu.cn",
        published_at: null, fetched_at: "2026-09-15T00:00:00Z", level: "official_primary",
        validity: "valid", citation: 1,
      }]}
    />,
  );

  expect(container.querySelector("blockquote.callout sup.cite")).not.toBeNull();
});
