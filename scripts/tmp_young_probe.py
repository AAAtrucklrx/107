# -*- coding: utf-8 -*-
"""young 平台接口探测（临时脚本）：验证 token + 探测"已报名"接口。

用法：py -3 scripts/tmp_young_probe.py <token>  （或设置环境变量 YOUNG_PROBE_TOKEN）
协议与 young_client 一致：AES-128-CBC + X-Access-Token。
"""
import base64
import json
import sys
import time
import urllib.parse

import requests
from Crypto.Cipher import AES

BASE_CANDIDATES = [
    "https://young.ustc.edu.cn/login/wisdom-group-learning-bg",
    "https://young.ustc.edu.cn/login/sc-wisdom-group-learning",
]

# 已报名/我的项目 候选路径（jeecg 风格，实测哪个通算哪个）
PATHS = [
    "/mobile/item/enrolmentList",          # 已验证：报名中列表
    "/mobile/item/myEnrolmentList",
    "/mobile/item/myEnrolment",
    "/mobile/item/myItemList",
    "/mobile/item/signUpList",
    "/mobile/item/mySignUp",
    "/mobile/myproject/signUp",
    "/mobile/myProject/signUpList",
]


def main() -> int:
    token = sys.argv[1] if len(sys.argv) > 1 else ""
    if not token or len(token) < 32:
        print("用法: py -3 scripts/tmp_young_probe.py <token>")
        return 1

    tk = token[-32:]
    key, iv = tk[16:32].encode(), tk[0:16].encode()

    def enc(plain: str) -> str:
        raw = plain.encode()
        pad = (16 - len(raw) % 16) % 16
        raw += b"\x00" * pad
        return base64.b64encode(AES.new(key, AES.MODE_CBC, iv).encrypt(raw)).decode()

    session = requests.Session()
    session.headers.update({"X-Access-Token": token})

    for base in BASE_CANDIDATES:
        for path in PATHS:
            _t = int(time.time() * 1000)
            e = enc(json.dumps({"_t": _t, "pageNo": 1, "pageSize": 3}))
            url = f"{base}{path}?_t={_t}&requestParams={urllib.parse.quote(e, safe='')}"
            try:
                r = session.get(url, timeout=15)
                body = r.text[:180].replace("\n", " ")
                mark = "OK " if (r.status_code == 200 and '"success":true' in r.text) else "    "
                print(f"[{mark}] {r.status_code} {base.split('/login/')[1]}{path}\n        {body}")
            except Exception as ex:
                print(f"[ERR] {base.split('/login/')[1]}{path}: {ex}")
            time.sleep(0.4)
    return 0


if __name__ == "__main__":
    sys.exit(main())
