import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SourceList } from "./SourceList";

test("source details expose trust and timestamps only after expansion", async () => {
  const user = userEvent.setup();
  render(<SourceList sources={[{
    source_id: "s1",
    title: "教务处公开通知",
    display_url: "https://www.teach.ustc.edu.cn/notice/1",
    institution: "中国科学技术大学教务处",
    domain: "www.teach.ustc.edu.cn",
    published_at: "2026-08-27T00:00:00Z",
    fetched_at: "2026-08-27T01:00:00Z",
    level: "official_primary",
    validity: "valid",
    citation: 1,
  }]} />);

  expect(screen.getByRole("button", { name: /教务处公开通知/ })).toBeInTheDocument();
  expect(screen.queryByText("官方一手")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /来源 1/ }));
  expect(screen.getByRole("link", { name: /教务处公开通知/ })).toHaveAttribute(
    "href",
    "https://www.teach.ustc.edu.cn/notice/1",
  );
  expect(screen.getByText("官方一手")).toBeInTheDocument();
  expect(screen.getByText("中国科学技术大学教务处")).toBeInTheDocument();
});
