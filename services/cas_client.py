"""
小蜗 — CAS 统一认证客户端
支持两种登录方式：
1. 表单登录：模拟 id.ustc.edu.cn CAS 登录流程（RSA 加密 + execution 令牌）
2. 重定向登录：跳转到 CAS 官方登录页，用户认证后回调带 ticket
"""

from __future__ import annotations

import base64
import re
from urllib.parse import urlparse

import requests
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from utils.logger import get_logger

log = get_logger("xiaowo.cas")


def _value_text(value) -> str:
    """把教务 API 的字符串或 nameZh/name 包装对象归一为文本。"""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("nameZh", "name", "label", "value"):
            text = _value_text(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, (str, int)):
        return str(value).strip()
    return ""


def _profile_roots(payload: dict) -> list[dict]:
    """展开 student-info 常见 data/result/student 包装层。"""
    roots: list[dict] = []
    queue = [payload]
    seen: set[int] = set()
    wrapper_keys = ("data", "result", "studentInfo", "student", "person", "user", "stdGradeRank")
    while queue:
        item = queue.pop(0)
        if not isinstance(item, dict) or id(item) in seen:
            continue
        seen.add(id(item))
        roots.append(item)
        for key in wrapper_keys:
            child = item.get(key)
            if isinstance(child, dict):
                queue.append(child)
    return roots


def _all_dict_roots(payload: dict) -> list[dict]:
    """递归展开档案对象；仅用于专业/年级等带专属字段名的查找。"""
    roots: list[dict] = []
    queue = [payload]
    seen: set[int] = set()
    while queue:
        item = queue.pop(0)
        if not isinstance(item, dict) or id(item) in seen:
            continue
        seen.add(id(item))
        roots.append(item)
        for value in item.values():
            if isinstance(value, dict):
                queue.append(value)
    return roots


def _first_profile_value(roots: list[dict], keys: tuple[str, ...]) -> str:
    for key in keys:
        for root in roots:
            text = _value_text(root.get(key))
            if text:
                return text
    return ""


def _normalize_grade(value) -> str:
    text = _value_text(value)
    if not text:
        return ""
    match = re.search(r"(20\d{2})", text)
    if match:
        return f"{match.group(1)}级"
    return text


def _normalize_student_profile(payload, expected_id: str = "") -> dict:
    """从 CAS/student-info 或 stdGradeRank 的嵌套结构提取已验证身份字段。"""
    if not isinstance(payload, dict):
        return {}
    roots = _profile_roots(payload)
    all_roots = _all_dict_roots(payload)
    returned_id = _first_profile_value(
        all_roots, ("studentCode", "studentNo", "studentId", "username", "sid"),
    )
    if returned_id and expected_id and returned_id.casefold() != expected_id.casefold():
        log.warning("学生档案学号与认证学号不一致，拒绝使用该档案")
        return {}
    return {
        "name": _first_profile_value(
            roots, ("studentName", "studentNameZh", "nameZh", "displayName", "name"),
        ),
        "major": _first_profile_value(
            all_roots, ("majorNameZh", "majorName", "major", "studentMajor", "majorZh"),
        ),
        "grade": _normalize_grade(_first_profile_value(
            all_roots, ("grade", "gradeName", "enrollmentYear", "entryYear", "enterSchoolYear"),
        )),
    }


class CASClient:
    """
    CAS 统一认证客户端。

    模拟登录流程：
    1. GET /cas/login?service=... → 获取 execution 令牌 + RSA 公钥
    2. 用 RSA 公钥加密密码
    3. POST /cas/login → CAS 验证通过返回 ticket
    4. 跟随重定向到目标站点建立会话
    """

    CAS_BASE = "https://id.ustc.edu.cn"
    JW_BASE = "https://jw.ustc.edu.cn"
    CATALOG_BASE = "https://catalog.ustc.edu.cn"

    JW_SERVICE = "https://jw.ustc.edu.cn/ucas-sso/login"
    CATALOG_SERVICE = "https://catalog.ustc.edu.cn/ustc_cas_login?next=https://catalog.ustc.edu.cn/query/classroom"

    TIMEOUT = 20

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        self._logged_in = False
        self._student_id: str | None = None
        self._student_data_id: int | None = None
        self._student_profile: dict[str, str] = {}
        self._profile_sources: list[str] = []
        # 测试数据也按 CASClient 实例隔离，避免一个测试用户覆盖另一用户的方案树。
        self._injected_program_tree: dict | list | None = None

    @property
    def session(self) -> requests.Session:
        """获取已认证的 session（供 CatalogAPI 等复用）"""
        return self._session

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in

    @property
    def student_id(self) -> str | None:
        return self._student_id

    @property
    def student_data_id(self) -> int | None:
        return self._student_data_id

    # ── 重定向登录（推荐）─────────────────────

    def get_login_url(self, service_url: str = None) -> str:
        """
        生成 CAS 登录页 URL，用于浏览器重定向。
        用户将在科大官方 CAS 页面输入凭证，认证后回调带 ticket。

        Args:
            service_url: 回调地址，默认从 config 读取

        Returns:
            完整的 CAS 登录 URL
        """
        from config import CAS_SERVICE_URL
        service = service_url or CAS_SERVICE_URL
        return f"{self.CAS_BASE}/cas/login?service={service}"

    def validate_ticket(self, ticket: str, service_url: str = None) -> tuple[bool, str | None]:
        """
        通过 CAS /serviceValidate 验证 ticket。

        Args:
            ticket: CAS 返回的 Service Ticket (ST-xxx)
            service_url: 回调地址，需与 get_login_url 中的一致

        Returns:
            (success, username) 元组
        """
        from config import CAS_SERVICE_URL
        service = service_url or CAS_SERVICE_URL
        url = f"{self.CAS_BASE}/cas/serviceValidate"
        try:
            resp = self._session.get(
                url,
                params={"ticket": ticket, "service": service},
                timeout=self.TIMEOUT,
            )
            if resp.status_code == 200:
                text = resp.text.strip()
                if "<cas:authenticationSuccess>" in text:
                    import re
                    m = re.search(r'<cas:user>([^<]+)</cas:user>', text)
                    username = m.group(1) if m else None
                    log.info(f"CAS ticket 验证成功: user={username}")
                    return True, username
                else:
                    log.warning(f"CAS ticket 验证失败: {text[:200]}")
                    return False, None
        except Exception as e:
            log.error(f"CAS ticket 验证请求失败: {e}")
        return False, None

    def login_with_ticket(self, ticket: str, service_url: str = None) -> bool:
        """
        用 CAS ticket 建立教务系统会话。

        流程：
        1. 验证 ticket 获取用户名
        2. 手动访问 jw 系统建立 cookie session
        3. 获取学生基本信息

        Returns:
            True 登录成功
        """
        # 1. 验证 ticket
        success, username = self.validate_ticket(ticket, service_url)
        if not success or not username:
            return False

        self._student_id = username
        log.info(f"CAS 重定向登录: user={username}")

        # 2. 手动访问 jw 建立 session cookies
        try:
            resp = self._session.get(
                f"{self.JW_BASE}/home/menu",
                timeout=self.TIMEOUT,
                allow_redirects=True,
            )
            expected_url = urlparse(self.JW_BASE)
            final_url = urlparse(resp.url)
            if (
                resp.status_code == 200
                and final_url.scheme == expected_url.scheme
                and final_url.hostname == expected_url.hostname
            ):
                self._logged_in = True
                self._extract_data_id(resp.text)
                self._home_page_html = resp.text
                log.info(f"jw session 建立成功: {username}")

                # 3. 建立 catalog session
                try:
                    self._session.get(
                        f"{self.CATALOG_BASE}/query/classroom",
                        headers={"Accept": "text/html"},
                        timeout=self.TIMEOUT,
                        allow_redirects=True,
                    )
                    log.info("Catalog session 建立成功")
                except Exception as e:
                    log.warning(f"Catalog session 建立失败（不影响教务功能）: {e}")

                # 4. 获取学生信息
                self._fetch_student_info()
                return True
            log.error(
                f"jw session 未建立: status={resp.status_code}, final_url={resp.url}"
            )
            return False
        except Exception as e:
            log.error(f"jw session 建立失败: {e}")
            return False

    # ── 表单登录（备用）─────────────────────

    def login(self, username: str, password: str, service: str = None) -> bool:
        """
        执行完整 CAS 登录（表单方式，备用）。

        Args:
            username: 学工号
            password: 密码（明文，会在本地 RSA 加密）
            service: CAS 回调地址，默认登录教务系统

        Returns:
            True 登录成功，False 失败
        """
        service = service or self.JW_SERVICE
        log.info(f"CAS 登录开始: user={username}, service={service[:50]}...")

        # Step 1: 获取登录页面，提取 execution 和 RSA 公钥
        login_url = f"{self.CAS_BASE}/cas/login"
        try:
            resp = self._session.get(
                login_url,
                params={"service": service},
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as e:
            log.error(f"CAS 登录页获取失败: {e}")
            return False

        html = resp.text

        # 提取 execution 令牌
        execution = self._extract_execution(html)
        if not execution:
            log.error("无法提取 execution 令牌")
            return False

        # 提取 RSA 公钥
        rsa_key = self._extract_rsa_key(html)
        if not rsa_key:
            log.error("无法提取 RSA 公钥")
            return False

        # Step 2: RSA 加密密码
        encrypted_pwd = self._encrypt_password(password, rsa_key)
        if not encrypted_pwd:
            log.error("密码加密失败")
            return False

        # Step 3: POST 登录
        login_data = {
            "username": username,
            "password": encrypted_pwd,
            "type": "UsernamePassword",
            "_eventId": "submit",
            "geolocation": "",
            "execution": execution,
            "captcha_code": "",
        }

        try:
            resp = self._session.post(
                login_url,
                data=login_data,
                params={"service": service},
                timeout=self.TIMEOUT,
                allow_redirects=True,  # 自动跟随重定向
            )
        except Exception as e:
            log.error(f"CAS 登录 POST 失败: {e}")
            return False

        # Step 4: 验证登录成功
        if resp.status_code == 200 and "login" not in resp.url.lower().split("/")[-1]:
            self._logged_in = True
            self._student_id = username
            log.info(f"CAS 登录成功: {username}")
            # 尝试获取 student_data_id
            self._fetch_student_info()
            return True
        else:
            log.error(f"CAS 登录失败: status={resp.status_code}, url={resp.url}")
            return False

    def login_catalog(self, username: str, password: str) -> bool:
        """登录并建立 catalog.ustc.edu.cn 的 session"""
        # 先登录教务系统
        if not self._logged_in:
            if not self.login(username, password):
                return False

        # 再访问 catalog 建立 session
        try:
            log.debug("访问 catalog 建立 session")
            self._session.get(
                self.CATALOG_SERVICE.replace(
                    "https://catalog.ustc.edu.cn/ustc_cas_login?next=",
                    f"{self.CAS_BASE}/cas/login?service=",
                ),
                timeout=self.TIMEOUT,
                allow_redirects=True,
            )
            # 直接访问一个 catalog 页面确认 session
            resp = self._session.get(
                f"{self.CATALOG_BASE}/query/classroom",
                headers={"Accept": "text/html"},
                timeout=self.TIMEOUT,
                allow_redirects=True,
            )
            if resp.status_code == 200:
                log.info("Catalog session 建立成功")
                return True
        except Exception as e:
            log.warning(f"Catalog session 建立失败: {e}")
        return False

    def get(self, url: str, **kwargs) -> requests.Response:
        """带认证的 GET 请求"""
        if not self._logged_in:
            raise RuntimeError("未登录，请先调用 login()")
        return self._session.get(url, timeout=self.TIMEOUT, **kwargs)

    def get_json(self, path: str, base: str = None) -> dict | list:
        """
        带认证的 GET 请求，返回 JSON。

        Args:
            path: API 路径，如 "/for-std/course-table/get-data"
            base: 基础 URL，默认 jw.ustc.edu.cn
        """
        if not self._logged_in:
            return {"error": "未登录"}
        base = base or self.JW_BASE
        url = f"{base}{path}"
        try:
            resp = self._session.get(url, timeout=self.TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"GET {url} 失败: {e}")
            return {"error": str(e)}

    # ── 内部方法 ──────────────────────────────────

    def _extract_execution(self, html: str) -> str | None:
        """从 CAS 登录页提取 execution hidden field"""
        # 查找 <input type="hidden" name="execution" value="...">
        m = re.search(r'name=["\']execution["\']\s+value=["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)
        # 尝试另一种格式
        m = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']execution["\']', html)
        return m.group(1) if m else None

    def _extract_rsa_key(self, html: str) -> str | None:
        """从 CAS 登录页提取 RSA 公钥（login-croypto 字段）"""
        m = re.search(r'id=["\']login-croypto["\'][^>]*value=["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)
        # 尝试 content 属性
        m = re.search(r'id=["\']login-croypto["\'][^>]*content=["\']([^"\']+)["\']', html)
        return m.group(1) if m else None

    def _encrypt_password(self, password: str, public_key_b64: str) -> str | None:
        """用 RSA 公钥加密密码"""
        try:
            # 解码 Base64 公钥
            key_bytes = base64.b64decode(public_key_b64)
            rsa_key = RSA.import_key(key_bytes)
            cipher = PKCS1_v1_5.new(rsa_key)
            encrypted = cipher.encrypt(password.encode("utf-8"))
            return base64.b64encode(encrypted).decode("utf-8")
        except Exception as e:
            log.error(f"RSA 加密失败: {e}")
            return None

    def _fetch_student_info(self) -> None:
        """登录后获取学生基本信息，提取 student_data_id"""
        try:
            resp = self._session.get(
                f"{self.JW_BASE}/home/menu",
                timeout=self.TIMEOUT,
            )
            if resp.status_code == 200:
                log.debug("jw session 已确认")
                # 从页面中提取 dataId（用于课表等 API 调用）
                self._extract_data_id(resp.text)
                # jw 为 SPA，home/menu 可能不含 dataId；成绩接口无需 dataId 且返回
                # studentAssoc，可作为兜底提取源（2026-08-05 登录实测确认）
                if not self._student_data_id:
                    self._student_data_id = self._resolve_data_id_from_grades()
                # 保存页面内容供后续解析
                self._home_page_html = resp.text
        except Exception:
            self._home_page_html = None

    def _merge_student_profile(self, profile: dict, source: str) -> None:
        """只用同一认证会话返回的非空字段补齐档案，不用页面选择器或猜测值。"""
        used_identity_field = False
        for key in ("name", "major", "grade"):
            value = str(profile.get(key) or "").strip()
            if value and not self._student_profile.get(key):
                self._student_profile[key] = value
                if key in {"major", "grade"}:
                    used_identity_field = True
        if used_identity_field and source not in self._profile_sources:
            self._profile_sources.append(source)

    def _fetch_grade_profile(self) -> dict | list | None:
        """读取成绩接口中的 stdGradeRank，作为专业/入学年级的认证兜底。"""
        try:
            sem_data = self.get_grade_semesters()
            if not isinstance(sem_data, list) or not sem_data:
                return None
            ids = [s.get("id") for s in sem_data if isinstance(s, dict) and s.get("id")]
            if not ids:
                return None
            grade_data = self.get_grades(ids[:2])
            if not isinstance(grade_data, dict):
                return grade_data
            profile = _normalize_student_profile(
                grade_data.get("stdGradeRank") or {}, self._student_id or "",
            )
            self._merge_student_profile(profile, "cas_grade_profile")
            return grade_data
        except Exception as e:
            log.warning(f"从成绩接口读取学生档案失败: {e}")
            return None

    def _resolve_data_id_from_grades(self) -> int | None:
        """从成绩接口提取 student_data_id（成绩查询不需要 data_id）"""
        try:
            grade_data = self._fetch_grade_profile()
            if isinstance(grade_data, dict):
                for sem in grade_data.get("semesters", []):
                    for sc in (sem.get("scores") or []):
                        did = sc.get("studentAssoc")
                        if did:
                            log.info(f"从成绩接口提取到 student_data_id: {did}")
                            return int(did)
        except Exception as e:
            log.warning(f"从成绩接口解析 data_id 失败: {e}")
        return None

    def get_student_info(self) -> dict | None:
        """
        获取学生基本信息（姓名、学号等）。
        需要已登录状态。

        Returns:
            {"name": "xxx", "id": "PBxxxxx", "major": "xxx"} 或 None
        """
        if not self._logged_in:
            return None
        info = {"id": self._student_id or ""}
        # 尝试从 jw API 获取更详细的信息
        try:
            resp = self._session.get(
                f"{self.JW_BASE}/for-std/student/home/student-info",
                timeout=self.TIMEOUT,
            )
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "").lower()
                data = resp.json() if "application/json" in content_type else {}
                if data:
                    self._merge_student_profile(
                        _normalize_student_profile(data, self._student_id or ""),
                        "cas_student_info",
                    )
        except Exception:
            pass

        # 2026-08-27:旧接口 /for-std/student/home/student-info 已 404,
        # 用档案页 /for-std/student-info/info/{dataId} 解析兜底(含所在学院/院系/专业)
        if not self._student_profile.get("major") or not self._student_profile.get("grade"):
            page = self.get_student_info_page()
            if isinstance(page, dict) and page.get("profile"):
                prof = page["profile"]
                major = prof.get("所修专业") or prof.get("所在专业") or ""
                if major:
                    major = re.sub(r"^\d+", "", major)  # "01101计算机科学与技术" -> "计算机科学与技术"
                merged = {
                    "name": prof.get("姓名") or "",
                    "major": major,
                    "grade": _normalize_grade(prof.get("年级") or ""),
                    "college": prof.get("所在学院") or "",
                    "department": prof.get("学生所在院系") or "",
                }
                merged = {key: value for key, value in merged.items() if value}
                self._merge_student_profile(merged, "cas_student_info_page")

        if not self._student_profile.get("major") or not self._student_profile.get("grade"):
            self._fetch_grade_profile()
        info.update(self._student_profile)

        # Fallback: 从页面 HTML 中提取姓名
        if not info.get("name"):
            html = getattr(self, "_home_page_html", None) or ""
            name_patterns = [
                r'"nameZh"\s*:\s*"([^"]+)"',
                r'"studentName"\s*:\s*"([^"]+)"',
                r'class="[^"]*name[^"]*"[^>]*>([^<]+)<',
            ]
            for pattern in name_patterns:
                m = re.search(pattern, html)
                if m:
                    info["name"] = m.group(1).strip()
                    break
        if self._profile_sources:
            info["profile_source"] = "+".join(self._profile_sources)
        else:
            info["profile_source"] = "cas_authenticated"
        return info if info.get("id") else None

    def _extract_data_id(self, html: str) -> None:
        """从 jw 页面提取 student_data_id"""
        # 常见模式: dataId=504586 或 "dataId":504586
        patterns = [
            r'dataId["\s:=]+(\d+)',
            r'/for-std/[^"\']*\?.*?dataId=(\d+)',
            r'studentId["\s:=]+(\d+)',
        ]
        for pattern in patterns:
            m = re.search(pattern, html)
            if m:
                self._student_data_id = int(m.group(1))
                log.info(f"提取到 student_data_id: {self._student_data_id}")
                return
        log.debug("未能从页面提取 student_data_id")

    # ── jw 内部 API ──────────────────────────────────

    def get_course_table(self, semester_id: int, data_id: int = None) -> dict | list:
        """
        获取个人课表。
        GET /for-std/course-table/get-data?bizTypeId=2&semesterId={semId}&dataId={dataId}
        """
        if not self._logged_in:
            return {"error": "未登录"}
        did = data_id or self._student_data_id
        if not did:
            return {"error": "缺少 student_data_id，请先登录或手动指定"}
        path = f"/for-std/course-table/get-data?bizTypeId=2&semesterId={semester_id}&dataId={did}"
        return self.get_json(path)

    def get_course_table_by_week(self, semester_id: int, week_index: int, data_id: int = None) -> dict | list:
        """
        获取指定教学周的个人课表（打印格式，按周精确到节次/分钟）。
        GET /for-std/course-table/semester/{semId}/print-data/{dataId}?weekIndex={week}

        Args:
            semester_id: 学期 ID（如 461=2026秋季）
            week_index: 教学周序号（从 1 开始）
            data_id: student_data_id，默认用登录时提取的
        """
        if not self._logged_in:
            return {"error": "未登录"}
        did = data_id or self._student_data_id
        if not did:
            return {"error": "缺少 student_data_id，请先登录或手动指定"}
        path = f"/for-std/course-table/semester/{semester_id}/print-data/{did}?weekIndex={week_index}"
        return self.get_json(path)

    def get_grade_semesters(self) -> list | dict:
        """
        获取成绩学期列表。
        GET /for-std/grade/sheet/getSemesters
        """
        return self.get_json("/for-std/grade/sheet/getSemesters")

    def get_grades(self, semester_ids: list[int]) -> dict | list:
        """
        获取成绩列表。
        GET /for-std/grade/sheet/getGradeList?trainTypeId=1&semesterIds={ids}
        """
        ids_str = ",".join(str(s) for s in semester_ids)
        path = f"/for-std/grade/sheet/getGradeList?trainTypeId=1&semesterIds={ids_str}"
        return self.get_json(path)

    def get_course_selection(self, semester_id: int, data_id: int = None) -> dict | list:
        """
        获取选课结果。
        GET /for-std/course-take-query/semester/{semId}/search
        参数(2026-08-27 实测):bizTypeAssoc=2&studentAssoc={dataId}&courseTakeStatusSetVal=1
        返回 data[]: courseName/credits/courseCategoryName/openDepartmentName/teacherAssignmentList/dateTimePlacePersonText/courseTakeStatus...
        """
        if not self._logged_in:
            return {"error": "未登录"}
        did = data_id or self._student_data_id
        if not did:
            return {"error": "缺少 student_data_id，请先登录或手动指定"}
        path = (
            f"/for-std/course-take-query/semester/{semester_id}/search"
            f"?bizTypeAssoc=2&studentAssoc={did}&courseCodeLike=&courseNameZhLike="
            f"&courseCategoryAssoc=&courseTakeStatusSetVal=1"
        )
        return self.get_json(path)

    def get_course_table_datum(self, semester_id: int, lesson_ids: list[int], data_id: int = None) -> dict:
        """按周课表全量数据(POST /for-std/course-table/datum)。
        body: {"lessonIds": [...]}；返回 result.lessonList（每门教学班的完整信息：
        courseName/teachers/schedule/周次等）。注意响应较大（500KB+），慎频繁调用。"""
        if not self._logged_in:
            return {"error": "未登录"}
        did = data_id or self._student_data_id
        if not did or not lesson_ids:
            return {"error": "缺少 student_data_id 或 lesson_ids"}
        try:
            resp = self._session.post(
                f"{self.JW_BASE}/for-std/course-table/datum",
                json={"lessonIds": lesson_ids},
                params={"bizTypeId": 2, "semesterId": semester_id, "dataId": did},
                timeout=self.TIMEOUT,
            )
            if resp.status_code != 200:
                return {"error": f"datum 返回 {resp.status_code}"}
            return resp.json()
        except Exception as exc:
            return {"error": str(exc)[:120]}

    def search_all_lessons(self, semester_id: int, data_id: int = None) -> list | dict:
        """全校开课查询(GET /for-std/lesson-search/semester/{semId}/search/{dataId})。
        返回全学期教学班列表（~240KB）：code(带班号)/nameZh/courseName/teachers/
        schedule/开课院系等；关键词过滤建议在调用侧做（接口参数名未公开）。"""
        if not self._logged_in:
            return {"error": "未登录"}
        did = data_id or self._student_data_id
        if not did:
            return {"error": "缺少 student_data_id，请先登录或手动指定"}
        return self.get_json(f"/for-std/lesson-search/semester/{semester_id}/search/{did}")

    # ── 2026-08-27 实测新接口(旧 student/home/student-info 已 404) ──

    def get_student_info_page(self, data_id: int = None) -> dict:
        """
        学生档案页解析(GET /for-std/student-info/info/{dataId})。
        现行档案来源,含所在学院/院系/所修专业/行政班级/学籍状态等 139 个 dt/dd 字段。
        """
        if not self._logged_in:
            return {"error": "未登录"}
        did = data_id or self._student_data_id
        if not did:
            return {"error": "缺少 student_data_id，请先登录或手动指定"}
        try:
            resp = self._session.get(
                f"{self.JW_BASE}/for-std/student-info/info/{did}", timeout=self.TIMEOUT)
            if resp.status_code != 200:
                return {"error": f"档案页返回 {resp.status_code}"}
            html = resp.text
        except Exception as exc:
            return {"error": str(exc)[:120]}
        pairs = re.findall(r"<dt>([^<]+)</dt>\s*<dd>([^<]*)</dd>", html)
        profile = {key.strip(): value.strip() for key, value in pairs if key.strip()}
        return {"profile": profile, "fields": len(profile)}

    def get_sport_grades(self) -> dict | list:
        """体育成绩(GET /for-std/sport-grade/list)。
        返回 list:schoolYear/score/state/appraisingReachStandard(达标)/updateDate"""
        if not self._logged_in:
            return {"error": "未登录"}
        return self.get_json("/for-std/sport-grade/list")

    def get_current_teach_week(self) -> dict:
        """官方当前教学周(GET /home/get-current-teach-week)。
        返回 {currentSemester, weekIndex, dayIndex, isInSemester}；
        weekIndex 为教务权威教学周序号（学期未开始时 dayIndex 可能为负）。"""
        if not self._logged_in:
            return {"error": "未登录"}
        return self.get_json("/home/get-current-teach-week")

    def get_exam_arrange(self, data_id: int = None) -> dict:
        """
        考试安排页解析(GET /for-std/exam-arrange/info/{dataId})。
        #exams 表:课堂号/考试类型/课程名称/日期时间/考场/教学楼/考场所在校区/考试说明
        """
        if not self._logged_in:
            return {"error": "未登录"}
        did = data_id or self._student_data_id
        if not did:
            return {"error": "缺少 student_data_id，请先登录或手动指定"}
        try:
            resp = self._session.get(
                f"{self.JW_BASE}/for-std/exam-arrange/info/{did}", timeout=self.TIMEOUT)
            if resp.status_code != 200:
                return {"error": f"考试页返回 {resp.status_code}"}
            html = resp.text
        except Exception as exc:
            return {"error": str(exc)[:120]}
        match = re.search(r"<table[^>]*id=[\"']exams[\"'][^>]*>(.*?)</table>", html, re.S)
        if not match:
            return {"exams": [], "count": 0, "note": "无考试安排表格"}
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.S)
        exams = []
        for row in rows[1:]:  # 跳过表头
            cells = [re.sub(r"<[^>]+>", "", cell).strip()
                     for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
            if cells and any(cells):
                exams.append({
                    "lesson_code": cells[0] if len(cells) > 0 else "",
                    "exam_type": cells[1] if len(cells) > 1 else "",
                    "course_name": cells[2] if len(cells) > 2 else "",
                    "datetime": cells[3] if len(cells) > 3 else "",
                    "room": cells[4] if len(cells) > 4 else "",
                    "building": cells[5] if len(cells) > 5 else "",
                    "campus": cells[6] if len(cells) > 6 else "",
                    "note": cells[7] if len(cells) > 7 else "",
                })
        return {"exams": exams, "count": len(exams)}

    def get_program_modules(self, module_id: int = None) -> dict | list:
        """
        获取培养方案模块。
        GET /for-std/program/root-module-json/{moduleId}
        如果不指定 moduleId，尝试获取根模块。
        """
        mid = module_id or 0
        path = f"/for-std/program/root-module-json/{mid}"
        return self.get_json(path)

    def get_my_program_tree(self) -> dict | list:
        """
        获取学生个人培养方案模块树（登录后优先使用，比全量库方案更精准）。

        实测链路（2026-08-13）：
        1. GET /for-std/program → 302 到 /for-std/program/info/{dataId}，
           HTML 内嵌 program JSON（含 'hasAttachment':null,'id':{programId}）；
        2. GET /for-std/program/root-module-json/{programId} → 个人模块树
           （节点 children 递归、type.nameZh 模块名、planCourses[].course、
           readableTerms(list)、compulsory、credits）。

        Returns:
            模块树 dict；未登录或解析失败返回 {"error": ...}
        """
        if self._injected_program_tree is not None:
            return self._injected_program_tree
        if not self._logged_in:
            return {"error": "未登录"}
        try:
            resp = self._session.get(f"{self.JW_BASE}/for-std/program", timeout=self.TIMEOUT)
            resp.raise_for_status()
            html = resp.text
            # HTML 内嵌 program JSON 锚点（hasAttachment 为 program 特有字段，其后紧跟 id）
            m = re.search(r"'hasAttachment':null,'id':(\d+),'logs'", html)
            if not m:
                log.warning("未能从个人方案页提取 program id（可能未登录）")
                return {"error": "未能定位个人培养方案"}
            program_id = int(m.group(1))
            return self.get_json(f"/for-std/program/root-module-json/{program_id}")
        except Exception as e:
            log.error(f"获取个人培养方案失败: {e}")
            return {"error": str(e)}

    def inject_program_tree(self, tree: dict | list | None) -> None:
        """仅为当前客户端注入离线个人方案；测试用户之间互不共享。"""
        self._injected_program_tree = tree

    def logout(self) -> None:
        """清除会话"""
        self._session.cookies.clear()
        self._logged_in = False
        self._student_id = None
        self._student_data_id = None
        self._student_profile = {}
        self._profile_sources = []
        self._injected_program_tree = None
        self._home_page_html = None
        log.info("CAS 会话已清除")
