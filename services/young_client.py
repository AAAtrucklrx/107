# -*- coding: utf-8 -*-
"""
young_client.py — 青春科大智慧团学（young.ustc.edu.cn）活动数据客户端

数据源抽象：BaseYoungProvider 定义统一接口
- EncryptedHttpProvider：当前加密 HTTP 协议实现（AES-128-CBC，密钥由 token 派生）
- 未来学校开放官方接口后，实现 BaseYoungProvider 即可无缝替换（架构预留）

关键协议要点（已用真实账号实测验证）：
- 认证：请求头仅需 X-Access-Token；额外 Origin/Referer/自定义 UA 会导致 token 不被识别
- 加密：AES-128-CBC + ZeroPadding，key = token[-32:][16:32]，iv = token[-32:][0:16]
- GET 请求：URL 带 _t(毫秒) + requestParams(百分号编码的 base64 密文)
- 分页：明文 {"_t":..., "pageNo":1, "pageSize":50}
"""
import base64
import json
import logging
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests
from Crypto.Cipher import AES

log = logging.getLogger("xiaowo.young")

# young 平台后端与图片前缀（已实测）
YOUNG_BASE = "https://young.ustc.edu.cn/login/wisdom-group-learning-bg"
YOUNG_PIC_PREFIX = "https://young.ustc.edu.cn/login/"


def parse_dt(s) -> Optional[datetime]:
    """解析 young 平台时间字符串 "2026-08-31 23:30:00" → datetime"""
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


@dataclass
class YoungActivity:
    """标准化的活动模型（与 young 平台原始字段解耦）"""
    id: str = ""
    name: str = ""
    start_time: Optional[str] = None   # st 活动开始
    end_time: Optional[str] = None     # et 活动结束
    apply_start: Optional[str] = None  # applySt 报名开始
    apply_end: Optional[str] = None    # applyEt 报名截止
    pic: str = ""
    organizer: str = ""      # businessDeptName 主办组织
    sponsor: str = ""        # sponsor_dictText 指导/赞助方
    category: str = ""       # itemCategory_dictText 分类
    module: str = ""         # m/z/t 等模块
    fav_count: int = 0
    people_num: int = 0
    service_hour: str = ""
    description: str = ""    # baseContent 活动简介
    raw: dict = field(default_factory=dict)

    @property
    def pic_url(self) -> str:
        return YOUNG_PIC_PREFIX + self.pic if self.pic else ""

    @property
    def apply_deadline(self) -> Optional[datetime]:
        return parse_dt(self.apply_end)

    @property
    def start_dt(self) -> Optional[datetime]:
        return parse_dt(self.start_time)

    @property
    def end_dt(self) -> Optional[datetime]:
        return parse_dt(self.end_time)


class BaseYoungProvider:
    """数据源抽象：未来官方开放接口只需实现 fetch_enrolment_activities"""

    def fetch_enrolment_activities(self, page_size: int = 50) -> list[YoungActivity]:
        raise NotImplementedError


class EncryptedHttpProvider(BaseYoungProvider):
    """加密 HTTP 协议实现（当前唯一实现，协议已实测验证）"""

    def __init__(self, token: str, timeout: int = 25):
        self.token = token
        self.timeout = timeout
        tk = token[-32:]
        self._key = tk[16:32]
        self._iv = tk[0:16]

    def _encrypt(self, plain: str) -> str:
        """AES-128-CBC + ZeroPadding → base64（与前端 encry_http_v1 一致）"""
        raw = plain.encode("utf-8")
        pad = (16 - len(raw) % 16) % 16
        raw += b"\x00" * pad
        cipher = AES.new(self._key.encode(), AES.MODE_CBC, self._iv.encode())
        return base64.b64encode(cipher.encrypt(raw)).decode("ascii")

    def _get(self, path: str, payload: Optional[dict] = None) -> dict:
        _t = int(time.time() * 1000)
        plain = dict(payload) if payload else {}
        plain["_t"] = _t
        enc = self._encrypt(json.dumps(plain, ensure_ascii=False))
        url = f"{YOUNG_BASE}{path}?_t={_t}&requestParams={urllib.parse.quote(enc, safe='')}"
        resp = requests.get(
            url, headers={"X-Access-Token": self.token}, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Young API 失败: {data.get('message')}")
        return data

    def fetch_enrolment_activities(self, page_size: int = 50) -> list[YoungActivity]:
        """拉取“报名中”活动列表（分页取满 page_size 条）"""
        data = self._get("/mobile/item/enrolmentList",
                         {"pageNo": 1, "pageSize": page_size})
        records = (data.get("result") or {}).get("records") or []
        activities = []
        for r in records:
            activities.append(YoungActivity(
                id=r.get("id", ""),
                name=r.get("itemName", ""),
                start_time=r.get("st"),
                end_time=r.get("et"),
                apply_start=r.get("applySt"),
                apply_end=r.get("applyEt"),
                pic=r.get("pic", ""),
                organizer=r.get("businessDeptName", ""),
                sponsor=r.get("sponsor_dictText", ""),
                category=r.get("itemCategory_dictText", ""),
                module=r.get("module", ""),
                fav_count=int(r.get("favCount") or 0),
                people_num=int(r.get("peopleNum") or r.get("sumPersons") or 0),
                service_hour=r.get("serviceHour", "") or "",
                description=r.get("baseContent", "") or "",
                raw=r,
            ))
        return activities


class YoungService:
    """入口：按配置选择数据源 Provider（测试期 token 方式，预留官方 API 切换）"""

    def __init__(self, provider: Optional[BaseYoungProvider] = None):
        self.provider = provider

    @classmethod
    def from_token(cls, token: str) -> "YoungService":
        return cls(provider=EncryptedHttpProvider(token))

    def fetch_enrolment_activities(self, page_size: int = 50) -> list[YoungActivity]:
        if self.provider is None:
            raise RuntimeError("Young 数据源未配置（YOUNG_TOKEN 缺失）")
        return self.provider.fetch_enrolment_activities(page_size)
