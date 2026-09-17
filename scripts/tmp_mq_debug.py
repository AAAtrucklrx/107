from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=True, executable_path=exe)
    page = browser.new_page(viewport={"width": 424, "height": 956})
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    info = page.evaluate("""() => {
      const logo = document.querySelector('.mobile-header .brand__logo') || document.querySelector('.brand__logo');
      const nav = document.querySelector('.mobile-bottom-nav');
      const lcs = logo ? getComputedStyle(logo) : null;
      const ncs = nav ? getComputedStyle(nav) : null;
      return {
        logoW: lcs?.width, logoH: lcs?.height, logoFS: lcs?.fontSize,
        logoParent: logo?.closest('header')?.className,
        navMargin: ncs?.margin, navRadius: ncs?.borderRadius, navBg: ncs?.backgroundColor?.slice(0,40),
      };
    }""")
    print(info)
    browser.close()
