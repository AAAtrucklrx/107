from playwright.sync_api import sync_playwright

FPS = """(frames => new Promise(resolve => {
  let n = 0;
  const start = performance.now();
  function tick() { n += 1; if (n < frames) requestAnimationFrame(tick); else resolve({ frames: n, ms: performance.now() - start }); }
  requestAnimationFrame(tick);
}))(180)"""

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    # 注入 30 条消息（15 组 user+assistant，assistant 卡含正文与来源行）模拟长对话
    page.evaluate("""() => {
      const scroll = document.querySelector('.message-scroll');
      const empty = scroll.querySelector('.chat-empty');
      if (empty) empty.remove();
      for (let i = 0; i < 15; i++) {
        const u = document.createElement('article');
        u.className = 'message message--user';
        u.innerHTML = '<div class="message__identity">你</div><div class="message__body"><div class="markdown-body"><p>帮我分析一下下学期的选课策略，需要综合考虑培养方案学分缺口、先修课程满足情况和课表负载。</p></div></div>';
        const a = document.createElement('article');
        a.className = 'message message--assistant';
        a.innerHTML = '<div class="message__identity">小蜗</div><div class="message__body"><div class="markdown-body"><p>根据培养方案核对结果，你当前已修 68 学分，距离毕业要求还差 97 学分。建议下学期优先补齐 3 门核心课，同时搭配 2 门通修课平衡负载。</p></div><div class="source-section"><div class="source-item"><span class="source-item__number">1</span><div>培养方案 · 计算机科学与技术（2024 版）</div></div></div></div>';
        scroll.appendChild(u);
        scroll.appendChild(a);
      }
    }""")
    page.wait_for_timeout(600)
    r1 = page.evaluate(FPS)
    print(f"long-chat idle (30 msg, blurred cards): {r1['frames']/(r1['ms']/1000):.1f} fps")
    r2 = page.evaluate("""(async () => {
      const el = document.querySelector('.message-scroll');
      let n = 0;
      const start = performance.now();
      for (let y = 0; y < 80; y += 1) {
        el.scrollTop = y * 25;
        await new Promise(r => requestAnimationFrame(r));
        n += 1;
      }
      return { frames: n, ms: performance.now() - start };
    })()""")
    print(f"long-chat fast scroll: {r2['frames']/(r2['ms']/1000):.1f} fps")
    page.screenshot(path="docs/qa/glass-longchat-blur.png")
    browser.close()
