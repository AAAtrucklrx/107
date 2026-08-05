"""
小蜗 — Catalog API 客户端
封装 catalog.ustc.edu.cn 的公开 API，全部无需认证
"""

from __future__ import annotations

import time
from datetime import date

import requests

from utils.logger import get_logger

log = get_logger("xiaowo.api.catalog")

# ── 教学楼代码映射 ──────────────────────────────────

BUILDING_CODE_MAP: dict[int, str] = {
    1: "第一教学楼",
    2: "第二教学楼",
    3: "第三教学楼",
    5: "第五教学楼",
    8: "中校区综合体育馆",
    9: "中校区艺术教学中心",
    11: "高新校区图书教育中心A楼",
    12: "高新校区图书教育中心B楼",
    13: "高新校区图书教育中心C楼",
    14: "高新校区师生活动中心",
    15: "高新校区2号学科楼",
    16: "高新校区3号学科楼",
    22: "高新校区信智楼",
    41: "太湖路校区教学楼1",
    42: "太湖路校区教学楼2",
    43: "太湖路校区教学楼3",
}

# 常用简称映射（用户输入 → buildingCode）
BUILDING_ALIAS: dict[str, int] = {
    "一教": 1, "第一教学楼": 1, "1教": 1,
    "二教": 2, "第二教学楼": 2, "2教": 2,
    "三教": 3, "第三教学楼": 3, "3教": 3,
    "五教": 5, "第五教学楼": 5, "5教": 5,
    "高新A": 11, "高新A楼": 11, "图教A": 11,
    "高新B": 12, "高新B楼": 12, "图教B": 12,
    "高新C": 13, "高新C楼": 13, "图教C": 13,
    "信智楼": 22,
}


def resolve_building(name: str) -> int | None:
    """
    将用户输入的教学楼名称转换为 buildingCode。
    支持 "三教"、"第三教学楼"、"3"、3 等多种输入。
    """
    # 直接数字
    if isinstance(name, int):
        return name
    try:
        code = int(name)
        if code in BUILDING_CODE_MAP:
            return code
    except (ValueError, TypeError):
        pass
    # 别名查找
    for alias, code in BUILDING_ALIAS.items():
        if alias in name:
            return code
    return None


def building_name(code: int) -> str:
    """buildingCode → 中文名称"""
    return BUILDING_CODE_MAP.get(code, f"教学楼{code}")


def building_short_name(code: int) -> str:
    """buildingCode → 简称"""
    short = {1: "一教", 2: "二教", 3: "三教", 5: "五教",
             11: "高新A", 12: "高新B", 13: "高新C", 22: "信智楼"}
    return short.get(code, f"{code}教")


# ── CatalogAPI 客户端 ───────────────────────────────

class CatalogAPI:
    """
    catalog.ustc.edu.cn 公开 API 封装。

    特性：
    - 5 分钟内存缓存（避免重复请求）
    - 网络错误返回 {"error": "..."} 不抛异常
    - 自动日志记录
    """

    BASE_URL = "https://catalog.ustc.edu.cn"
    TIMEOUT = 15  # 秒
    CACHE_TTL = 300  # 5 分钟

    def __init__(self, session: requests.Session = None) -> None:
        """
        Args:
            session: 可选的外部已认证 session（来自 CASClient）。
                     不提供则创建新 session（需要 warmup 建立 cookie）。
        """
        self._session = session or requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        })
        self._cache: dict[str, tuple[float, object]] = {}
        self._external_session = session is not None

    def _warmup(self) -> None:
        """
        如果使用外部 session（已认证），跳过 warmup。
        否则访问一个页面建立 session cookie。
        """
        if self._external_session:
            return
        # 无外部 session 时，warmup 也无法通过 CAS 认证
        # 仅作为 fallback 尝试
        pass

    def set_session(self, session: requests.Session) -> None:
        """设置外部已认证的 session（CAS 登录后调用）"""
        self._session = session
        self._external_session = True
        self._cache.clear()
        log.debug("CatalogAPI 已切换到外部认证 session")

    def _get(self, path: str, params: dict = None) -> dict | list:
        """
        带缓存的 GET 请求。
        成功返回 JSON，失败返回 {"error": "..."}。
        """
        cache_key = f"{path}:{params}"
        now = time.time()

        # 检查缓存
        if cache_key in self._cache:
            cached_time, cached_data = self._cache[cache_key]
            if now - cached_time < self.CACHE_TTL:
                log.debug(f"缓存命中: {path}")
                return cached_data

        self._warmup()
        url = f"{self.BASE_URL}{path}"
        try:
            log.debug(f"GET {url} params={params}")
            resp = self._session.get(url, params=params, timeout=self.TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            # 写入缓存
            self._cache[cache_key] = (now, data)
            return data
        except requests.exceptions.Timeout:
            log.error(f"API 超时: {url}")
            return {"error": f"请求超时 ({self.TIMEOUT}s)"}
        except requests.exceptions.ConnectionError:
            log.error(f"API 连接失败: {url}")
            return {"error": "网络连接失败"}
        except requests.exceptions.HTTPError as e:
            log.error(f"API HTTP 错误: {e}")
            return {"error": f"HTTP 错误: {e.response.status_code}"}
        except Exception as e:
            log.error(f"API 未知错误: {e}")
            return {"error": str(e)}

    def _post(self, path: str, json_data: dict = None) -> dict | list:
        """带缓存的 POST 请求"""
        cache_key = f"POST:{path}:{json_data}"
        now = time.time()

        if cache_key in self._cache:
            cached_time, cached_data = self._cache[cache_key]
            if now - cached_time < self.CACHE_TTL:
                log.debug(f"缓存命中: {path}")
                return cached_data

        self._warmup()
        url = f"{self.BASE_URL}{path}"
        try:
            log.debug(f"POST {url} json={json_data}")
            resp = self._session.post(url, json=json_data, timeout=self.TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            self._cache[cache_key] = (now, data)
            return data
        except Exception as e:
            log.error(f"API POST 错误: {e}")
            return {"error": str(e)}

    # ── 公开 API 方法 ─────────────────────────────

    def get_timetable(self, date_str: str) -> dict:
        """
        获取某天全校教室占用。

        Args:
            date_str: 日期字符串，格式 "YYYY-MM-DD" 或 "YYYYMMDD"

        Returns:
            {timetable: {period: [{buildingCode, classroomName, courseName, teachers, start, end}]}}
            或 {"error": "..."}
        """
        # 规范化日期格式
        date_str = date_str.replace("-", "")
        if len(date_str) == 8:
            date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        return self._get(f"/api/teach/timetable-public-all/{date_str}")

    def get_semesters(self) -> list[dict]:
        """
        获取学期列表。

        Returns:
            [{"id": 461, "name": "2026-2027学年第一学期", "start": "2026-09-01", ...}, ...]
            按 start 降序排列
        """
        result = self._get("/api/teach/semester/list")
        if isinstance(result, dict) and "error" in result:
            return [result]
        return result if isinstance(result, list) else []

    def get_exams(self, semester_id: int) -> list[dict]:
        """
        获取专业课考试安排。

        Args:
            semester_id: 学期 ID（如 461）

        Returns:
            考试列表
        """
        result = self._get(f"/api/teach/exam/list/{semester_id}")
        if isinstance(result, dict) and "error" in result:
            return [result]
        return result if isinstance(result, list) else []

    def get_general_exams(self, semester_id: int) -> list[dict]:
        """
        获取通修课考试安排（马原、英语等全校课）。

        Args:
            semester_id: 学期 ID

        Returns:
            考试列表
        """
        result = self._get(f"/api/teach/general-exam/list/{semester_id}")
        if isinstance(result, dict) and "error" in result:
            return [result]
        return result if isinstance(result, list) else []

    def search_courses(self, keyword: str) -> list[dict]:
        """
        课程搜索。

        Args:
            keyword: 搜索关键词

        Returns:
            [{"id": ..., "name": {"cn": "...", "en": "..."}, "dept": "...", ...}, ...]
        """
        result = self._get("/api/teach/course/search", params={"keyword": keyword})
        if isinstance(result, dict) and "error" in result:
            return [result]
        return result if isinstance(result, list) else []

    def get_lessons(self, semester_id: int) -> list[dict]:
        """
        获取某学期全部开课信息。

        Args:
            semester_id: 学期 ID

        Returns:
            开课列表
        """
        result = self._get(f"/api/teach/lesson/list-for-teach/{semester_id}")
        if isinstance(result, dict) and "error" in result:
            return [result]
        return result if isinstance(result, list) else []

    def get_lesson_infos(self, codes: list[str], semester: int) -> dict:
        """
        批量查询课程详情。

        Args:
            codes: 课程代码列表
            semester: 学期 ID

        Returns:
            课程详情
        """
        return self._post("/api/teach/lesson/infos", json_data={
            "codes": codes,
            "semester": semester,
        })

    # ── 工具方法 ──────────────────────────────────

    def get_current_semester(self) -> dict | None:
        """
        获取当前学期信息（基于当前日期推断）。

        Returns:
            当前学期 dict 或 None
        """
        semesters = self.get_semesters()
        if not semesters or (isinstance(semesters[0], dict) and "error" in semesters[0]):
            return None

        today = date.today().isoformat()
        # 找到 start <= today 的最近学期
        for sem in semesters:
            if isinstance(sem, dict) and sem.get("start", "") <= today:
                return sem
        # 如果没有匹配的，返回最新的
        return semesters[0] if semesters else None

    def clear_cache(self) -> None:
        """清除缓存"""
        self._cache.clear()
        log.debug("CatalogAPI 缓存已清除")
