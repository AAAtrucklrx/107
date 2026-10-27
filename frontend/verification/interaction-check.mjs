import { chromium, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { apiFixture, fixtureDate } from "./fixtures.mjs";

const origin = "http://127.0.0.1:5175";
const report = { syntheticDataOnly: true, checks: [], errors: [] };
const browser = await chromium.launch();
const wideMarkdown = `| ${Array.from({ length: 12 }, (_, i) => `列${i + 1}`).join(" | ")} |\n| ${Array(12).fill("---").join(" | ")} |\n| ${Array(12).fill("合成课程内容较长").join(" | ")} |`;
const longAnswer = `${wideMarkdown}\n\n${Array.from({ length: 35 }, (_, i) => `第${i + 1}段：这是用于检查长回答滚动的合成说明，不涉及真实学生或后台。`).join("\n\n")}`;
const sse = [{ type: "answer.segment", data: { markdown: longAnswer } }, { type: "answer.completed", data: { claims: [], sources: [], terminal_reason: "completed" } }]
  .map((event, index) => `data: ${JSON.stringify({ id: index + 1, run_id: "synthetic-run", at: fixtureDate, ...event })}\n\n`).join("");

try {
  for (const width of [375, 768, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: width === 375 ? 812 : 1000 }, locale: "zh-CN", timezoneId: "Asia/Shanghai", reducedMotion: "reduce", serviceWorkers: "block" });
    await context.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) { report.errors.push(`Unexpected external origin: ${url.origin}`); return route.abort(); }
      if (!url.pathname.startsWith("/api/")) return route.continue();
      const path = url.pathname.replace(/^\/api\/v1/, "");
      if (path.endsWith("/events")) return route.fulfill({ contentType: "text/event-stream", body: sse });
      const data = apiFixture(path, route.request().method());
      if (data === undefined) { report.errors.push(`Missing fixture: ${path}`); return route.abort(); }
      return route.fulfill({ json: data });
    });
    const page = await context.newPage();
    page.on("pageerror", error => report.errors.push(error.message));
    await page.clock.install({ time: new Date(fixtureDate) });
    await page.addInitScript(() => localStorage.setItem("xiaowo.mascot.greeting-day", "2026-10-26"));
    await page.goto(origin, { waitUntil: "networkidle" });
    if (await page.getByRole("button", { name: "知道了", exact: true }).isVisible()) await page.getByRole("button", { name: "知道了", exact: true }).click();
    const pet = page.getByRole("button", { name: "奶蛙", exact: true });
    await expect(pet).toHaveAttribute("data-state", "idle");
    await pet.click(); await expect(pet).toHaveAttribute("data-state", "laugh");
    await page.clock.fastForward(1600); await expect(pet).toHaveAttribute("data-state", "idle");
    await page.clock.fastForward(60_100); await expect(pet).toHaveAttribute("data-state", "sleep");
    await pet.click(); await expect(pet).toHaveAttribute("data-state", "laugh");
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("xiaowo:mascot", { detail: { type: "think" } })));
    await page.clock.fastForward(60_100); await expect(pet).toHaveAttribute("data-state", "think");
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("xiaowo:mascot", { detail: { type: "idle" } })));
    expect(await pet.locator("img").evaluate(element => getComputedStyle(element).animationName)).toBe("none");
    report.checks.push({ name: "mascot-click-sleep-busy-reduced-motion", width, ok: true });
    if (width > 1024) {
      const box = await pet.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x - 80, box.y - 60, { steps: 5 });
      expect((await pet.boundingBox()).x).toBeLessThan(box.x - 50);
      await page.mouse.up();
      await expect.poll(async () => Math.abs((await pet.boundingBox()).x - box.x)).toBeLessThan(1);
      await expect(pet).toHaveAttribute("data-state", "idle");
      report.checks.push({ name: "desktop-drag-return-without-laugh", width, ok: true });
    }
    const input = page.getByRole("textbox", { name: "向小蛙助手提问" });
    await input.fill(Array(6).fill("多行合成问题").join("\n"));
    const overlap = await page.evaluate(() => {
      const a = document.querySelector(".mascot-button").getBoundingClientRect();
      const b = document.querySelector(".composer").getBoundingClientRect();
      const t = document.querySelector(".scaffold-tab-bar")?.getBoundingClientRect();
      const intersects = (x, y) => x.left < y.right && x.right > y.left && x.top < y.bottom && x.bottom > y.top;
      return intersects(a, b) || Boolean(t && intersects(a, t));
    });
    expect(overlap).toBe(false);
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await page.locator(".answer-table-scroll").waitFor();
    const sizes = await page.locator(".answer-table-scroll").evaluate(element => ({ width: element.clientWidth, scrollWidth: element.scrollWidth, overflow: getComputedStyle(element).overflowX }));
    expect(sizes.overflow).toBe("auto");
    expect(await page.locator(".message-scroll").evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    await page.locator(".message-scroll").evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event("scroll")); });
    await page.getByRole("button", { name: "回到最新" }).click();
    await expect.poll(() => page.locator(".message-scroll").evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(120);
    report.checks.push({ name: "multiline-no-mascot-overlap-markdown-table-and-long-scroll", width, ok: true, table: sizes });
    await context.close();
  }
} catch (error) { report.errors.push(error.stack ?? String(error)); }
finally {
  await browser.close();
  await writeFile(fileURLToPath(new URL("../../docs/frontend-rebuild/interaction-verification.json", import.meta.url)), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ checks: report.checks.length, errors: report.errors.length }, null, 2));
  if (report.errors.length) process.exitCode = 1;
}
