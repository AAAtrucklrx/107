# -*- coding: utf-8 -*-
"""捕获 young 学生端 myproject/SignUp 页面的真实 XHR 接口路径（临时脚本）。

原理：headless Edge 注入 localStorage 登录态（token/userinfo）→ 打开报名页 →
监听全部请求 URL。SPA 的 requestParams 是加密的，但**路径是明文**——拿到路径
后即可用 young_client 的协议层重放。
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

TOKEN_ENTRY = json.dumps({
    "value": sys.argv[1] if len(sys.argv) > 1 else "",
    "expire": 1788012014282,
})
USERINFO_ENTRY = json.dumps({"value": {
    "id": "PB25111691", "username": "PB25111691", "creditScore": 100,
    "realname": "刘睿翔", "orgCode": "011", "status": 1, "grade": "2025",
    "college": "计算机科学与技术系", "classes": "25级215院011系03班",
    "gid": "2202522923", "type": "S", "loginNum": 253,
    "scientificqiValue": 111, "endTime": "2026-07-10 00:00:00",
    "email": "lrx_25@mail.ustc.edu.cn", "isCharge": False,
    "isTwCharge": False, "isScCharge": False, "examine": 0,
}, "expire": 1788012014282})

SIGNUP_URL = "https://young.ustc.edu.cn/login/sc-wisdom-group-learning/myproject/SignUp"

captured: list[str] = []


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()

        page.on("request", lambda req: captured.append(req.url)
                if "young.ustc.edu.cn" in req.url and req.resource_type in ("xhr", "fetch") else None)

        # 1) 先到登录页拿到 origin，再注入 localStorage
        page.goto("https://young.ustc.edu.cn/", wait_until="domcontentloaded", timeout=60000)
        page.evaluate(
            "([t, u]) => { localStorage.setItem('pro__Access-Token-zsxc-base', t);"
            " localStorage.setItem('pro__Login_Userinfo', u); }",
            [TOKEN_ENTRY, USERINFO_ENTRY])

        # 2) 打开报名页（SPA 路由）
        page.goto(SIGNUP_URL, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(8000)
        page.screenshot(path=str(Path(r"F:\小蜗\docs") / "tmp_young_signup_page.png"),
                        full_page=True)

        # 3) 若有 tab（报名中/已报名）尝试全部点一遍再抓
        for tab_text in ("已报名", "全部", "报名中", "待审核", "已结束"):
            try:
                tab = page.locator(f"text={tab_text}").first
                if tab.count() and tab.is_visible():
                    tab.click(timeout=3000)
                    page.wait_for_timeout(3000)
            except Exception:
                pass

        page.wait_for_timeout(4000)
        page.screenshot(path=str(Path(r"F:\小蜗\docs") / "tmp_young_signup_tabs.png"),
                        full_page=True)

        body = page.locator("body").inner_text()[:600]
        print("== 页面文本（前600字）==")
        print(body)
        print("\n== 捕获的 XHR 路径（去重）==")
        seen = []
        for url in captured:
            path = url.split("?")[0]
            if path not in seen:
                seen.append(path)
        for path in seen:
            print(path)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
