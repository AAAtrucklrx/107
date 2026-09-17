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
    page.evaluate("""() => {
      const scroll = document.querySelector('.message-scroll');
      const empty = scroll.querySelector('.chat-empty');
      if (empty) empty.remove();
      const stages = '<div class="stage-strip" role="status"><span class="stage-chip done">检索本地资料</span><span class="stage-chip done">联网搜索</span><span class="stage-chip done">核验证据</span><span class="stage-chip doing">组织回答</span></div>';
      const answer = '<div class="markdown-body"><p>根据评课社区近三年数据 <sup class="cite">1</sup> 和你本学期的课表负载 <sup class="cite">2</sup>，给出两位老师对比：</p><table><thead><tr><th>维度</th><th>徐宏力·周三班</th><th>李蔓·周五班</th></tr></thead><tbody><tr><td>作业量</td><td>每2周1次，量少</td><td>每周小作业，量中等</td></tr><tr><td>平均给分</td><td>84</td><td>88</td></tr></tbody></table><p>综合你的诉求，<strong>徐宏力班更合适</strong>。</p></div>';
      const sources = '<div class="source-pills"><button class="source-pill" type="button"><span class="source-pill__num">1</span>评课社区·教师对比</button><button class="source-pill" type="button"><span class="source-pill__num">2</span>教务系统·本学期课表</button></div>';
      for (let i = 0; i < 6; i++) {
        const u = document.createElement('article');
        u.className = 'message message--user';
        u.innerHTML = '<div class="message__body"><div class="user-message"><div class="markdown-body"><p>计算机网络这门课选哪个老师好？我下学期课比较多，想要作业少一点的。</p></div></div></div>';
        const a = document.createElement('article');
        a.className = 'message message--assistant';
        a.innerHTML = '<div class="message__body">' + stages + answer + sources + '</div>';
        scroll.appendChild(u);
        scroll.appendChild(a);
      }
      scroll.scrollTop = scroll.scrollHeight;
    }""")
    page.wait_for_timeout(500)
    r1 = page.evaluate(FPS)
    print(f"long-chat idle: {r1['frames']/(r1['ms']/1000):.1f} fps")
    r2 = page.evaluate("""(async () => {
      const el = document.querySelector('.message-scroll');
      let n = 0;
      const start = performance.now();
      for (let y = 0; y < 80; y += 1) { el.scrollTop = Math.max(0, el.scrollHeight - y * 25); await new Promise(r => requestAnimationFrame(r)); n += 1; }
      return { frames: n, ms: performance.now() - start };
    })()""")
    print(f"fast scroll: {r2['frames']/(r2['ms']/1000):.1f} fps")
    page.screenshot(path="docs/qa/glass-answer-replica.png")
    print("saved docs/qa/glass-answer-replica.png")
    browser.close()
