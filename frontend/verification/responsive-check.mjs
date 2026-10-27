import { chromium, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { apiFixture, events, fixtureDate } from "./fixtures.mjs";

const args = Object.fromEntries(process.argv.slice(2).map((argument) => argument.replace(/^--/, "").split("=")));
const phase = args.phase ?? "before";
const visualStyle = args.style === "ustc" ? "ustc" : "frog";
if (!["before", "after"].includes(phase)) throw new Error("Use --phase=before or --phase=after");
const origin = new URL(args.url ?? (phase === "before" ? "http://127.0.0.1:5174" : "http://127.0.0.1:5175")).origin;
if (!["127.0.0.1", "localhost"].includes(new URL(origin).hostname)) throw new Error("UI verification only permits a local Vite server.");
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const output = resolve(root, args.output ?? "docs/frontend-rebuild");
await mkdir(output, { recursive: true });
const report = { phase, visualStyle, origin, fixtureDate, syntheticDataOnly: true, pages: [], checks: [], errors: [], blockedExternalRequests: [], unknownApiRequests: [] };
const browser = await chromium.launch({ headless: true });

async function pageMetrics(page, name, width, theme) {
  const measurements = await page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const touchTargets = [...document.querySelectorAll('button, a[href], input, select, textarea, [role="button"], [role="tab"], [role="switch"], .fc-event, tr[tabindex="0"]')]
      .filter((element) => {
        const box = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return !element.matches(':disabled, [aria-disabled="true"]') && box.width > 0 && box.height > 0 && style.visibility !== "hidden" && box.bottom > 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth;
      }).map((element) => {
        const box = element.getBoundingClientRect();
        return { label: (element.getAttribute("aria-label") || element.textContent || element.tagName).trim().slice(0, 70), width: Math.round(box.width * 10) / 10, height: Math.round(box.height * 10) / 10, inlineProseLink: element.matches(".markdown-body a, .message__body p a"), tag: element.tagName };
      });
    return { viewport, documentWidth: document.documentElement.scrollWidth, bodyWidth: document.body.scrollWidth,
      horizontalOverflow: document.documentElement.scrollWidth > viewport + 1 || document.body.scrollWidth > viewport + 1,
      smallTouchTargets: touchTargets.filter((target) => !target.inlineProseLink && (target.width < 43.5 || target.height < 43.5)),
      inlineProseLinks: touchTargets.filter((target) => target.inlineProseLink && (target.width < 43.5 || target.height < 43.5)),
      touchTargetCount: touchTargets.length,
    };
  });
  const filename = `${phase}-${name}-${width}-${theme}.png`;
  await page.screenshot({ path: resolve(output, filename), fullPage: false, animations: "disabled" });
  const contrast = phase === "after" ? await contrastMetrics(page) : undefined;
  report.pages.push({ name, width, theme, screenshot: filename, ...measurements, contrast });
}

async function contrastMetrics(page) {
  return page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const rgba = (color) => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, 1, 1);
      const values = [...ctx.getImageData(0, 0, 1, 1).data];
      return [values[0], values[1], values[2], values[3] / 255];
    };
    const composite = (foreground, background) => foreground.slice(0, 3).map((value, i) => value * foreground[3] + background[i] * (1 - foreground[3]));
    const luminance = (color) => color.slice(0, 3).map((value) => { const s = value / 255; return s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4; }).reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
    const ratio = (a, b) => { const first = luminance(a); const second = luminance(b); return (Math.max(first, second) + .05) / (Math.min(first, second) + .05); };
    const rootStyle = getComputedStyle(document.documentElement);
    const pairs = [["ink", "canvas"], ["ink", "surface"], ["ink", "surface-raised"], ["muted", "surface"], ["faint", "surface"], ["primary", "surface"], ["primary", "primary-soft"], ["on-primary", "primary"], ["success", "success-soft"], ["warning", "warning-soft"], ["danger", "danger-soft"]];
    const tokenPairs = pairs.map(([foreground, background]) => {
      const fg = rootStyle.getPropertyValue(`--${foreground}`).trim();
      const bg = rootStyle.getPropertyValue(`--${background}`).trim();
      const value = ratio(rgba(fg), rgba(bg));
      return { foreground, background, foregroundValue: fg, backgroundValue: bg, ratio: Math.round(value * 100) / 100, ok: value >= 4.5 };
    });
    const failures = [];
    const skipped = [];
    let tested = 0;
    for (const element of document.querySelectorAll("h1,h2,h3,p,span,small,strong,em,label,button,a,td,th,dt,dd,input,textarea")) {
      const text = [...element.childNodes].filter((node) => node.nodeType === Node.TEXT_NODE).map((node) => node.textContent).join("").trim()
        || (element.matches("input,textarea") ? element.value || element.getAttribute("placeholder") || "" : "");
      if (!text) continue;
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      if (box.width < 2 || box.height < 2 || box.bottom <= 0 || box.top >= innerHeight || box.right <= 0 || box.left >= innerWidth || style.visibility === "hidden" || element.closest(':disabled,[aria-disabled="true"]')) continue;
      const ancestors = [];
      for (let current = element; current; current = current.parentElement) ancestors.unshift(current);
      let background = [255, 255, 255];
      let unsupported = "";
      for (const ancestor of ancestors) {
        const parentStyle = getComputedStyle(ancestor);
        if (Number(parentStyle.opacity) < .99) unsupported = "opacity";
        if (parentStyle.backgroundImage !== "none") unsupported = "background-image";
        background = composite(rgba(parentStyle.backgroundColor), background);
      }
      if (unsupported) { skipped.push({ text: text.slice(0, 55), reason: unsupported }); continue; }
      const textStyle = element.matches("input,textarea") && !element.value ? getComputedStyle(element, "::placeholder") : style;
      const foreground = composite(rgba(textStyle.color), background);
      const contrast = ratio(foreground, background);
      const large = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontSize) >= 18.66 && parseFloat(style.fontWeight) >= 700);
      const minimum = large ? 3 : 4.5;
      tested += 1;
      if (contrast + .005 < minimum) failures.push({ text: text.slice(0, 70), className: element.className, foreground: textStyle.color, background: background.map((value) => Math.round(value)), ratio: Math.round(contrast * 100) / 100, minimum });
    }
    return { tokenPairs, testedTextElements: tested, failures, skipped, limitations: "Computed CSS text colors over solid alpha-composited ancestor backgrounds. Gradients/images and opacity are reported as skipped; raster artwork, hover states, icon-only non-text contrast, text antialiasing, and occlusion are not certified." };
  });
}

async function openPage(context, path, theme) {
  const page = await context.newPage();
  page.on("pageerror", (error) => report.errors.push({ path, message: error.message }));
  await page.clock.install({ time: new Date(fixtureDate) });
  await page.addInitScript(({ theme, visualStyle }) => { localStorage.setItem("xiaowo-theme", theme); localStorage.setItem("xiaowa-visual-style", visualStyle); }, { theme, visualStyle });
  await page.goto(`${origin}${path}`, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  const closeToday = page.getByRole("button", { name: "知道了", exact: true });
  if (await closeToday.isVisible()) await closeToday.click();
  return page;
}

async function checkHistoryKeyboard(page, width, theme) {
  const trigger = page.getByRole("button", { name: /^(会话历史|打开会话历史)$/ }).filter({ visible: true }).first();
  if (!await trigger.count()) {
    report.checks.push({ name: "history-keyboard", width, theme, ok: false, reason: "No visible history trigger" });
    return;
  }
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  let trapped = true;
  for (let index = 0; index < 8; index += 1) {
    await page.keyboard.press("Tab");
    trapped &&= await dialog.evaluate((element) => element.contains(document.activeElement));
  }
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  const restored = await expect(trigger).toBeFocused({ timeout: 2000 }).then(() => true, () => false);
  report.checks.push({ name: "history-keyboard", width, theme, ok: trapped && restored, focusTrapped: trapped, focusRestored: restored });
}

try {
  for (const width of [375, 768, 1440]) {
    for (const theme of phase === "before" ? ["light"] : ["light", "dark"]) {
      const context = await browser.newContext({ viewport: { width, height: width === 375 ? 812 : 1000 }, locale: "zh-CN", timezoneId: "Asia/Shanghai", reducedMotion: "reduce", serviceWorkers: "block" });
      await context.route("**/*", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== origin) { report.blockedExternalRequests.push(url.origin); return route.abort(); }
        if (!url.pathname.startsWith("/api/")) return route.continue();
        const path = url.pathname.replace(/^\/api\/v1/, "");
        if (path === "/chat/runs/synthetic-run/events") return route.fulfill({ status: 200, contentType: "text/event-stream", body: events });
        const data = apiFixture(path, request.method());
        if (data === undefined) {
          report.unknownApiRequests.push({ path, method: request.method() });
          return route.fulfill({ status: 404, json: { error: { code: "SYNTHETIC_ROUTE_MISSING", message: "验收夹具未定义此接口。" } } });
        }
        return route.fulfill({ status: 200, json: data });
      });

      const home = await openPage(context, "/", theme);
      await home.getByRole("textbox", { name: "向小蛙助手提问" }).waitFor();
      await pageMetrics(home, "home", width, theme);
      if (phase === "after") {
        await checkHistoryKeyboard(home, width, theme);
        await home.getByRole("textbox", { name: "向小蛙助手提问" }).fill("展示合成课程长表格");
        await home.getByRole("button", { name: "发送", exact: true }).click();
        await home.getByText("所有课程与身份信息均为虚构，未向真实后台发送请求。").waitFor();
        await pageMetrics(home, "chat-wide-table", width, theme);
        const tableScroll = await home.locator(".structured-table").evaluate((element) => ({ containerWidth: element.clientWidth, scrollWidth: element.scrollWidth, overflowX: getComputedStyle(element).overflowX }));
        report.checks.push({ name: "long-table-containment", width, theme, ok: tableScroll.scrollWidth <= tableScroll.containerWidth + 1 || ["auto", "scroll"].includes(tableScroll.overflowX), ...tableScroll });
        if (width === 375) {
          await home.setViewportSize({ width, height: 480 });
          const composer = home.getByRole("textbox", { name: "向小蛙助手提问" });
          const visibleInViewport = () => composer.evaluate((element) => { const box = element.getBoundingClientRect(); return box.bottom <= innerHeight + 1 && box.top >= 0; });
          const composerVisible = await expect.poll(visibleInViewport, { timeout: 2000 }).toBe(true).then(() => true, () => false);
          const box = await composer.boundingBox();
          report.checks.push({ name: "reduced-viewport-composer", width, theme, ok: composerVisible, composerBox: box, note: "Browser viewport resize; this is not an Android IME test." });
          await pageMetrics(home, "chat-reduced-viewport", width, theme);
        }
      }
      await home.close();

      const academic = await openPage(context, "/academic", theme);
      await academic.getByRole("heading", { name: "我的学业", exact: true }).waitFor();
      await academic.locator(".academic-section").first().waitFor();
      await pageMetrics(academic, "academic", width, theme);
      await academic.getByRole("tab", { name: /课表/ }).click();
      await academic.locator(".schedule-calendar .fc").waitFor();
      await pageMetrics(academic, "schedule", width, theme);
      const scroll = await academic.locator(".schedule-calendar-scroll").evaluate((element) => ({ containerWidth: element.clientWidth, scrollWidth: element.scrollWidth, overflowX: getComputedStyle(element).overflowX }));
      report.checks.push({ name: "schedule-contained", width, theme, ok: width >= 640 ? scroll.scrollWidth <= scroll.containerWidth + 2 : scroll.scrollWidth > scroll.containerWidth && ["auto", "scroll"].includes(scroll.overflowX), ...scroll });
      if (phase === "after") {
        await academic.getByRole("tab", { name: /培养方案/ }).click();
        await pageMetrics(academic, "program", width, theme);
        const profile = await openPage(context, "/profile", theme);
        await pageMetrics(profile, "profile", width, theme);
        await profile.close();
      }
      await context.close();
    }
  }
} catch (error) {
  report.errors.push({ message: error.stack || error.message });
} finally {
  await browser.close();
  report.blockedExternalRequests = [...new Set(report.blockedExternalRequests)];
  report.summary = { screenshots: report.pages.length, overflowPages: report.pages.filter((page) => page.horizontalOverflow).length,
    smallTouchTargetCount: report.pages.reduce((sum, page) => sum + page.smallTouchTargets.length, 0),
    contrastFailures: report.pages.reduce((sum, page) => sum + (page.contrast?.failures.length ?? 0) + (page.contrast?.tokenPairs.filter((pair) => !pair.ok).length ?? 0), 0),
    failedChecks: report.checks.filter((check) => !check.ok).length, errors: report.errors.length, unknownApiRequests: report.unknownApiRequests.length };
  await writeFile(resolve(output, `${phase}-verification.json`), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report.summary, null, 2));
  if (report.errors.length || (args.strict === "true" && (report.summary.overflowPages || report.summary.smallTouchTargetCount || report.summary.contrastFailures || report.summary.failedChecks || report.summary.unknownApiRequests))) process.exitCode = 1;
}
