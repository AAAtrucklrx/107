import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { apiFixture, events, fixtureDate } from './fixtures.mjs';

// Only synthetic records. Unknown APIs and all external origins are blocked.
const arg = process.argv.find(value => value.startsWith('--url='));
const origin = new URL(arg?.slice(6) ?? 'http://127.0.0.1:5175').origin;
const output = fileURLToPath(new URL('../../docs/xiaowa-styles/style-switch-verification.json', import.meta.url));
const report = { syntheticDataOnly: true, origin, startedAt: new Date().toISOString(), checks: [], errors: [] };
const browser = await chromium.launch();
const draft = '合成验证：切换风格时保留这段输入';
try {
  for (const width of [320, 375, 768, 1440]) for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', serviceWorkers: 'block', reducedMotion: 'reduce' });
    const requests = { session: 0, config: 0 }, errors = [], checked = [];
    try {
      await context.addInitScript(({ theme }) => {
        // Do not override preferences on reload: persistence is part of the check.
        if (!localStorage.getItem('xiaowo-theme')) localStorage.setItem('xiaowo-theme', theme);
        localStorage.setItem('xiaowo.mascot.greeting-day', '2026-10-26');
      }, { theme });
      await context.route('**/*', route => {
        const target = new URL(route.request().url());
        if (target.origin !== origin) { errors.push(`External origin: ${target.origin}`); return route.abort(); }
        if (!target.pathname.startsWith('/api/')) return route.continue();
        const path = target.pathname.replace(/^\/api\/v1/, '');
        if (path === '/auth/session') requests.session++;
        if (path === '/config/public') requests.config++;
        if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: events });
        const data = apiFixture(path, route.request().method());
        if (data === undefined) { errors.push(`Missing fixture: ${path}`); return route.abort(); }
        return route.fulfill({ json: data });
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.clock.install({ time: new Date(fixtureDate) });
      await page.goto(origin, { waitUntil: 'networkidle' });
      const dismiss = page.getByRole('button', { name: '知道了', exact: true });
      if (await dismiss.isVisible()) await dismiss.click();
      const input = page.getByRole('textbox', { name: '向小蛙助手提问' });
      await input.fill(draft);
      const before = { ...requests };
      const switchTo = async (label, style) => {
        await page.getByRole('button', { name: '切换界面风格', exact: true }).click();
        await page.getByRole('menuitemradio', { name: label }).click();
        await expect(page.locator('html')).toHaveAttribute('data-style', style);
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      };
      for (const [label, style] of [['科大蓝', 'ustc'], ['奶蛙黄', 'frog']]) {
        await switchTo(label, style);
        await expect(input).toHaveValue(draft);
        await expect(page.getByRole('button', { name: '我的，合成同学' })).toBeVisible();
        expect(requests).toEqual(before);
      }
      checked.push('real menu preserves draft, identity, brightness and bootstrap counts');
      await page.getByRole('button', { name: '发送', exact: true }).click();
      const answer = page.locator('.message--assistant');
      await expect(answer).toContainText('未向真实后台发送请求');
      for (const [label, style] of [['科大蓝', 'ustc'], ['奶蛙黄', 'frog']]) {
        await switchTo(label, style);
        await expect(answer).toContainText('未向真实后台发送请求');
        expect(requests).toEqual(before);
      }
      checked.push('completed answer survives both styles');
      await context.setOffline(true);
      await page.evaluate(() => window.dispatchEvent(new Event('offline')));
      await switchTo('科大蓝', 'ustc');
      await expect(answer).toContainText('未向真实后台发送请求');
      await expect(page.getByRole('button', { name: '我的，合成同学' })).toBeVisible();
      await expect(page.getByText('当前离线，发送消息和查询教务需要网络连接。', { exact: true })).toBeVisible();
      expect(requests).toEqual(before);
      checked.push('offline switch preserves loaded conversation and identity');
      await context.setOffline(false);
      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('html')).toHaveAttribute('data-style', 'ustc');
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      if (await dismiss.isVisible()) await dismiss.click();
      await page.getByRole('button', { name: '我的，合成同学' }).click();
      const beforeProfile = { ...requests };
      const frogChoice = page.getByRole('button', { name: /奶蛙黄 奶油底色，开心陪伴/ });
      await frogChoice.click();
      await expect(page.locator('html')).toHaveAttribute('data-style', 'frog');
      expect(requests).toEqual(beforeProfile);
      await page.reload({ waitUntil: 'networkidle' });
      await expect(page.locator('html')).toHaveAttribute('data-style', 'frog');
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(frogChoice).toHaveAttribute('aria-pressed', 'true');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      checked.push('profile picker and reload preserve selected style independently of brightness');
    } catch (error) { errors.push(error.stack ?? String(error)); }
    finally {
      report.checks.push({ width, theme, checked, ok: !errors.length, requests, errors });
      report.errors.push(...errors.map(error => ({ width, theme, error })));
      await context.close();
    }
  }
} finally {
  await browser.close();
  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ cases: report.checks.length, completedChecks: report.checks.reduce((sum, item) => sum + item.checked.length, 0), errors: report.errors.length, output }, null, 2));
  if (report.errors.length) process.exitCode = 1;
}
