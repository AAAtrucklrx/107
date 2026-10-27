import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sheet } from "./Sheet";

test("sheet announces its description, closes with Escape and restores trigger focus", async () => {
  const user = userEvent.setup();
  render(<Sheet title="会话历史" description="选择一段对话继续。" trigger={<button>打开历史</button>}>
    <button>一段历史对话</button>
  </Sheet>);
  const trigger = screen.getByRole("button", { name: "打开历史" });
  await user.click(trigger);
  expect(screen.getByRole("dialog", { name: "会话历史" })).toHaveAccessibleDescription("选择一段对话继续。");
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
