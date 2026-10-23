#!/usr/bin/env node
/**
 * 小蜗 · 中科大统一身份认证「半自动」登录，导出会话 cookie（2026-10-23）
 *
 * 背景：中科大 SSO（id.ustc.edu.cn）是自定义 SSO：
 *   - 密码经 AES 加密成 16 字节块（key 来自页面 login-croypto）
 *   - 提交前有风控引擎（设备指纹 / itdr token / 密码 SHA-1 上报）
 *   - 登录**强制短信二次验证**（type=smsLogin）
 * 因此不做「服务端全自动登录」（既过不了短信，也会触发风控），改为：
 *   **真实浏览器 + 人工输入 → 我们只负责驱动和拿 cookie**。
 *
 * 两种模式：
 *   node sso_login_browser.mjs                  # 浏览器模式（推荐）：你在弹出的窗口里自己输入
 *   node sso_login_browser.mjs --assisted       # 终端模式：按提示输入学号/密码/短信码，脚本代填
 *   node sso_login_browser.mjs --out ./x.json   # 指定导出路径
 *
 * 安全：不落明文密码；密码只在内存/页面里停留；导出文件权限 600。
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';

const args = process.argv.slice(2);
const ASSISTED = args.includes('--assisted');
const OUT = (() => {
  const i = args.indexOf('--out');
  return i >= 0 && args[i + 1] ? args[i + 1] : path.join(process.env.HOME || '.', '.xiaowo_sso_cookies.json');
})();
const LOGIN = 'https://id.ustc.edu.cn/cas/login?service=https%3A%2F%2Fjw.ustc.edu.cn%2Fucas-sso%2Flogin';
const TIMEOUT_MS = 300_000;

function ask(question, { hidden = false } = {}) {
  return new Promise(resolve => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: true });
    if (hidden) {
      const onData = () => {
        // 隐藏回显：把已输入的字符换成 *
        readline.moveCursor(process.stdout, 0, 0);
        readline.clearLine(process.stdout, 0);
        process.stdout.write(question + '*'.repeat(rl.line.length));
      };
      process.stdin.on('data', onData);
      rl.question(question, ans => { process.stdin.off('data', onData); rl.close(); process.stdout.write('\n'); resolve(ans); });
    } else {
      rl.question(question, ans => { rl.close(); resolve(ans); });
    }
  });
}

const browser = await chromium.launch({ headless: false });
const ctx = await browser.newContext();
const page = await ctx.newPage();
await page.goto(LOGIN, { waitUntil: 'domcontentloaded' });
console.log('已打开统一身份认证页面。');

if (ASSISTED) {
  const user = await ask('学号: ');
  const pwd = await ask('密码（不回显）: ', { hidden: true });
  // 填表：不提交密码到日志
  const userBox = page.locator('input[type="text"], input[name="username"], #username').first();
  const pwdBox = page.locator('input[type="password"], input[name="password"], #password').first();
  await userBox.fill(user);
  await pwdBox.fill(pwd);
  await pwdBox.evaluate(el => el.dispatchEvent(new Event('input', { bubbles: true })));
  await page.keyboard.press('Enter');
  console.log('已提交账号密码，等待短信验证码界面…');
  await page.waitForTimeout(4000);
  const code = await ask('短信验证码（6 位）: ');
  // 验证码输入框：第一个可见的 text/tel 输入框
  const codeBox = page.locator('input[type="tel"], input[type="text"]').first();
  await codeBox.fill(code);
  await page.keyboard.press('Enter');
  console.log('已提交验证码…');
} else {
  console.log('请在浏览器里自己完成：学号 → 密码 → 短信验证码（最多等 5 分钟）。');
}

// 等待登录成功：URL 落到 jw.ustc.edu.cn，或出现 CASTGC cookie
const deadline = Date.now() + TIMEOUT_MS;
let ok = false;
while (Date.now() < deadline) {
  await page.waitForTimeout(1500);
  const cookies = await ctx.cookies();
  const hasCastgc = cookies.some(c => c.domain.includes('ustc.edu.cn') && /CASTGC/i.test(c.name));
  if (page.url().includes('jw.ustc.edu.cn') || hasCastgc) { ok = true; break; }
}
const cookies = await ctx.cookies();
const relevant = cookies.filter(c => c.domain.includes('ustc.edu.cn'));
const payload = {
  createdAt: new Date().toISOString(),
  mode: ASSISTED ? 'assisted' : 'browser',
  finalUrl: page.url(),
  success: ok,
  cookies: relevant.map(c => ({ name: c.name, value: c.value, domain: c.domain, path: c.path,
                                expires: c.expires, httpOnly: c.httpOnly, secure: c.secure })),
};
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(payload, null, 2), { mode: 0o600 });
try { fs.chmodSync(OUT, 0o600); } catch {}
console.log(`\n${ok ? '✅ 登录成功' : '⚠️ 未检测到登录成功（超时）'} · 最终 URL: ${page.url()}`);
console.log(`   cookie 数量: ${relevant.length}（${relevant.map(c => c.name).slice(0, 8).join(', ')}${relevant.length > 8 ? '…' : ''}）`);
console.log(`   已写入: ${OUT}（权限 600，含会话票据，勿提交/勿外传）`);
await browser.close();
process.exit(ok ? 0 : 1);
