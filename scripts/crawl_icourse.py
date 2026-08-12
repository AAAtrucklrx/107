# -*- coding: utf-8 -*-
"""icourse.club 全量爬虫：课程列表 + 课程详情（含评论）+ 培养方案。

用法:
    python scripts/crawl_icourse.py list              # 爬课程列表（约1865页, 每页10门）→ scripts/data/icourse_list.json
    python scripts/crawl_icourse.py detail            # 爬详情页（review_count>0 的课程）→ scripts/data/raw/{cid}.json
    python scripts/crawl_icourse.py programs          # 爬培养方案 → scripts/data/programs/{pid}.json
    python scripts/crawl_icourse.py all               # 依次执行以上三项

特性: 断点续爬（已存在文件跳过）、指数退避重试、随机限速、进度日志。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://icourse.club"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
DATA_DIR = Path(__file__).resolve().parent / "data"
LIST_FILE = DATA_DIR / "icourse_list.json"
RAW_DIR = DATA_DIR / "raw"
PROG_DIR = DATA_DIR / "programs"
PROG_LIST_FILE = DATA_DIR / "program_list.json"

SLEEP_MIN, SLEEP_MAX = 0.3, 0.8
MAX_RETRY = 3


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def http_get(url: str, timeout: int = 25) -> requests.Response:
    """带指数退避的 GET。"""
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return r
            log(f"HTTP {r.status_code} @ {url} (attempt {attempt})")
        except requests.RequestException as e:
            log(f"ERR {e} @ {url} (attempt {attempt})")
        time.sleep(2 ** attempt * 1.5)
    return requests.Response()  # 类型占位, 调用方检查 status_code


# ───────────────────────── 列表页 ─────────────────────────

def parse_list_page(html: str) -> list[dict]:
    """解析课程列表页：返回课程卡片字典列表（含均分/人数, 便于筛选）。"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for block in soup.select("div.col-md-12.col-xs-12"):
        link = block.select_one("a.px16[href*='/course/']")
        if not link:
            continue
        m = re.fullmatch(r"/course/(\d+)/", link.get("href", ""))
        if not m:
            continue
        cid = int(m.group(1))
        title = link.get_text(strip=True)  # 形如 数学分析(B1)（汪琥庭）
        score = block.select_one(".rl-pd-sm.h4")
        count_el = block.select_one("span.text-muted.px12")
        dims = [li.get_text(strip=True) for li in block.select("ul.list-inline li")]
        rating = 0.0
        if score:
            try:
                rating = float(score.get_text(strip=True))
            except ValueError:
                rating = 0.0  # “暂无评价”等非数字
        count = 0
        if count_el:
            cm = re.search(r"(\d+)", count_el.get_text())
            count = int(cm.group(1)) if cm else 0
        out.append({
            "id": cid,
            "title": title,
            "rating": rating,
            "review_count": count,
            "dims": dims,  # [课程难度:xx, 作业多少:xx, 给分好坏:xx, 收获大小:xx]
        })
    return out


def cmd_list() -> None:
    collected: list[dict] = []
    seen_ids: set[int] = set()
    page = 1
    if LIST_FILE.exists():
        collected = json.loads(LIST_FILE.read_text(encoding="utf-8"))
        seen_ids = {c["id"] for c in collected}
        page = len(collected) // 10 + 1
        log(f"列表缓存 {len(collected)} 门, 从 page={page} 续爬")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    empty_streak = 0
    while True:
        r = http_get(f"{BASE}/course/?page={page}")
        if r.status_code == 404:
            log(f"page={page} → 404, 列表结束")
            break
        items = parse_list_page(r.text)
        if not items:
            empty_streak += 1
            if empty_streak >= 3:
                log(f"page={page} 连续空页, 结束")
                break
        else:
            empty_streak = 0
        for it in items:
            if it["id"] not in seen_ids:
                seen_ids.add(it["id"])
                collected.append(it)
        if page % 100 == 0:
            log(f"列表进度: page={page} 累计 {len(collected)} 门 (有评论 {sum(1 for c in collected if c['review_count'] > 0)})")
        LIST_FILE.write_text(json.dumps(collected, ensure_ascii=False, indent=1), encoding="utf-8")
        page += 1
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
    log(f"列表完成: 共 {len(collected)} 门, 有评论 {sum(1 for c in collected if c['review_count'] > 0)} 门")


# ───────────────────────── 课程详情 ─────────────────────────

def parse_course_detail(html: str) -> dict:
    """解析课程详情页 → 结构化字典。"""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    # 教师: 标题形如 组合数学（邵帅） - USTC评课社区
    teacher = ""
    tm = re.match(r"(.+?)（(.+?)）\s*-\s*USTC评课社区", title)
    if tm:
        course_name, teacher = tm.group(1), tm.group(2)
    else:
        course_name = title.replace(" - USTC评课社区", "").strip()

    # 评分区: 星级 + 均分 + (n人评价)
    rating = 0.0
    rate_count = 0
    score_el = soup.select_one("span.rl-pd-sm.h4")
    if score_el:
        try:
            rating = float(score_el.get_text(strip=True))
        except ValueError:
            rating = 0.0
    for el in soup.select("span.rl-pd-sm.text-muted"):
        cm = re.search(r"(\d+)", el.get_text())
        if cm and "评价" in el.get_text():
            rate_count = int(cm.group(1))

    # 开课学期 + 课程号: 标题行 span（形如 "2026春 2025秋 ... 课程号：COMP6002P04"）
    terms: list[str] = []
    code = ""
    title_span = soup.select_one("span.small.grey.align-bottom")
    if title_span:
        t = title_span.get_text(" ", strip=True)
        for m in re.finditer(r"20\d{2}[春秋夏]", t):
            if m.group(0) not in terms:
                terms.append(m.group(0))
        cm = re.search(r"课程号[：:]\s*([\w-]+)", t)
        if cm:
            code = cm.group(1)

    # 课程信息表: table.table-condensed.no-border 内 td>strong 键值对
    info: dict[str, str] = {}
    for td in soup.select("table.table-condensed.no-border td"):
        strong = td.find("strong")
        if not strong:
            continue
        k = strong.get_text(strip=True).rstrip("：:")
        v = td.get_text(" ", strip=True)
        v = v.replace(k, "", 1).lstrip("：:").strip()
        if k and v:
            info[k] = v
    if code:
        info["课程号"] = code
    credit = info.get("学分", "")
    dept = info.get("开课单位", "")
    course_type = info.get("课程类别", "")
    course_level = info.get("课程层次", "")

    # 四维度聚合（评论块 ul.desktop li）
    dim_map = {"课程难度": None, "作业多少": None, "给分好坏": None, "收获大小": None}

    # 评论
    reviews: list[dict] = []
    for blk in soup.select("div.review"):
        rid_el = blk.get("id", "")
        rid = int(rid_el.replace("review-", "")) if rid_el.startswith("review-") else 0
        author_el = blk.select_one("span.right-pd-sm.px16")
        author = author_el.get_text(strip=True) if author_el else ""
        stars = len(blk.select("span.glyphicon-star")) + 0.5 * len(blk.select("span.glyphicon-star-half"))
        term_el = blk.select_one("span.left-pd-md")
        term = term_el.get_text(strip=True) if term_el else ""
        dims: dict[str, str] = {}
        for li in blk.select("ul.desktop li"):
            t = li.get_text(" ", strip=True)
            if "：" in t:
                k, v = t.split("：", 1)
                dims[k.strip()] = v.strip()
        content_el = blk.select_one("div.review-content")
        content = content_el.get_text("\n", strip=True) if content_el else ""
        reviews.append({
            "id": rid,
            "author": author,
            "stars": stars,
            "term": term,
            "dims": dims,
            "content": content,
        })
        if dims.get("课程难度"):
            dim_map["课程难度"] = dims["课程难度"]
        if dims.get("作业多少"):
            dim_map["作业多少"] = dims["作业多少"]
        if dims.get("给分好坏"):
            dim_map["给分好坏"] = dims["给分好坏"]
        if dims.get("收获大小"):
            dim_map["收获大小"] = dims["收获大小"]

    return {
        "id": 0,  # 由调用方回填 cid
        "course_name": course_name,
        "teacher": teacher,
        "rating": rating,
        "rate_count": rate_count,
        "terms": terms,
        "code": code,
        "credit": credit,
        "dept": dept,
        "course_type": course_type,
        "course_level": course_level,
        "dims_agg": {k: v for k, v in dim_map.items() if v},
        "reviews": reviews,
    }


def cmd_detail(limit: int = 0, cids: list[int] | None = None, shard: int = 0, shards: int = 1) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if cids:
        targets = [{"id": c, "title": str(c), "review_count": 1, "rating": 0.0} for c in cids]
    else:
        if not LIST_FILE.exists():
            cmd_list()
        listing = json.loads(LIST_FILE.read_text(encoding="utf-8"))
        targets = [c for c in listing if c["review_count"] > 0]
        if shards > 1:
            # 分片并行: 按序号均分, 已存在文件跳过实现断点
            targets = [c for i, c in enumerate(targets) if i % shards == shard - 1]
        if limit:
            targets = targets[:limit]
    log(f"详情目标: {len(targets)} 门 (有评论, 分片 {shard}/{shards})")
    done = 0
    failed: list[int] = []
    for i, c in enumerate(targets, 1):
        cid = c["id"]
        out_file = RAW_DIR / f"{cid}.json"
        if out_file.exists():
            done += 1
            continue
        r = http_get(f"{BASE}/course/{cid}/")
        if r.status_code != 200:
            failed.append(cid)
            log(f"FAIL {cid} {c['title'][:30]}")
            continue
        data = parse_course_detail(r.text)
        data["id"] = cid
        data["list_rating"] = c["rating"]
        data["list_review_count"] = c["review_count"]
        out_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        done += 1
        if done % 50 == 0 or i == len(targets):
            log(f"详情进度: {done}/{len(targets)} (本次新增) 失败 {len(failed)}")
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
    log(f"详情完成: 本次新增 {done}, 累计失败 {len(failed)}: {failed[:20]}")


def cmd_refill_stars() -> None:
    """补爬: 重新解析星级缺失（stars=0）的课程详情, 覆盖 raw JSON。

    历史版本只数全星漏了半星(glyphicon-star-half), 导致半星评论被记为 0。
    扫描已有 raw 文件找出含 stars=0 评论的课程并强制重抓。
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    need: list[int] = []
    for f in RAW_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            need.append(int(f.stem))
            continue
        if any(not r.get("stars") for r in d.get("reviews", [])):
            need.append(d["id"])
    need = sorted(set(need))
    log(f"补爬目标: {len(need)} 门 (含 stars=0 评论)")
    failed: list[int] = []
    for i, cid in enumerate(need, 1):
        r = http_get(f"{BASE}/course/{cid}/")
        if r.status_code != 200:
            failed.append(cid)
            log(f"FAIL {cid}")
            continue
        data = parse_course_detail(r.text)
        data["id"] = cid
        # 保留列表页快照字段
        try:
            old = json.loads((RAW_DIR / f"{cid}.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            old = {}
        for k in ("list_rating", "list_review_count"):
            if k in old:
                data[k] = old[k]
        (RAW_DIR / f"{cid}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        if i % 50 == 0 or i == len(need):
            log(f"补爬进度: {i}/{len(need)} 失败 {len(failed)}")
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
    log(f"补爬完成: {len(need)}, 失败 {len(failed)}: {failed[:20]}")


# ───────────────────────── 培养方案 ─────────────────────────

def parse_program_list(html: str) -> list[dict]:
    """解析 /program/ 列表页 → [{pid, name, college, grade}]。"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    # 按学院分组的结构: h4/strong 标题 + 链接
    college = ""
    for el in soup.find_all(["h4", "h3", "strong"]):
        txt = el.get_text(strip=True)
        if txt and "培养方案" not in txt and not re.search(r"级$", txt):
            college = txt
        for a in el.find_next_siblings("a"):
            pass
    # 直接遍历所有 /program/{id}/ 链接, 记录所在最近标题
    for a in soup.select("a[href*='/program/']"):
        href = a.get("href", "")
        m = re.fullmatch(r"/program/(\d+)/", href)
        if not m:
            continue
        grade = a.get_text(strip=True)
        # 向上找学院标题
        parent = a.find_parent(["div", "li", "p"])
        ctx = ""
        if parent:
            ctx = parent.get_text(" ", strip=True)[:60]
        out.append({"pid": int(m.group(1)), "grade": grade, "ctx": ctx})
    return out


def parse_program_detail(html: str) -> dict:
    """解析 /program/{pid}/ 详情页 → 方案信息 + 课程行列表。"""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    # 学院/专业/年级: 页面文本 "数学与应用数学专业培养方案 数学科学学院 数学与应用数学 2025级 主修学位"
    text = soup.get_text(" ", strip=True)
    m_name = re.search(r"(.+?培养方案)\s+", text)
    name = m_name.group(1) if m_name else title.replace(" - USTC评课社区", "")
    college = ""
    cm = re.search(r"培养方案\s+(.+?学院|.+?系)", text)
    if cm:
        college = cm.group(1)
    gm = re.search(r"(20\d{2}级)", text)
    grade = gm.group(1) if gm else ""

    # 课程表格: 课程号|课程名|必修/选修|考核形式|学分|课程类别 (+ 行内学期标注)
    courses: list[dict] = []
    table = soup.select_one("table")
    current_term = ""
    if table:
        for tr in table.select("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            txts = [c.get_text(" ", strip=True) for c in cells]
            # 学期分隔行: 单个单元格形如 "1秋" / "1秋,1春"
            if len(cells) == 1 and re.fullmatch(r"[\d春秋夏,，]+", txts[0]):
                current_term = txts[0]
                continue
            if len(cells) >= 5:
                code = txts[0]
                cname = txts[1]
                required = txts[2] if len(txts) > 2 else ""
                exam = txts[3] if len(txts) > 3 else ""
                credit = txts[4] if len(txts) > 4 else ""
                category = txts[5] if len(txts) > 5 else ""
                courses.append({
                    "code": code,
                    "name": cname,
                    "required": required,   # 必修/选修
                    "exam": exam,
                    "credit": credit,
                    "category": category,
                    "term": current_term,   # 学期标注（可能为空）
                })
    return {
        "name": name,
        "college": college,
        "grade": grade,
        "course_count": len(courses),
        "courses": courses,
    }


def cmd_programs() -> None:
    PROG_DIR.mkdir(parents=True, exist_ok=True)
    r = http_get(f"{BASE}/program/")
    if r.status_code != 200:
        log("培养方案列表页获取失败")
        return
    items = parse_program_list(r.text)
    # 去重(pid)
    uniq: dict[int, dict] = {}
    for it in items:
        uniq.setdefault(it["pid"], it)
    items = list(uniq.values())
    PROG_LIST_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"培养方案列表: {len(items)} 个")
    done = 0
    failed = 0
    for i, it in enumerate(items, 1):
        pid = it["pid"]
        out_file = PROG_DIR / f"{pid}.json"
        if out_file.exists():
            done += 1
            continue
        r2 = http_get(f"{BASE}/program/{pid}/")
        if r2.status_code != 200:
            failed += 1
            continue
        data = parse_program_detail(r2.text)
        data["pid"] = pid
        data["grade"] = it["grade"]
        out_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        done += 1
        if done % 25 == 0 or i == len(items):
            log(f"方案进度: {done}/{len(items)} 失败 {failed}")
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
    log(f"方案完成: 本次新增 {done}, 失败 {failed}")


def main() -> None:
    ap = argparse.ArgumentParser(description="icourse.club 全量爬虫")
    ap.add_argument("stage", choices=["list", "detail", "programs", "refill_stars", "all"])
    ap.add_argument("--limit", type=int, default=0, help="detail 阶段仅爬前 N 门（测试用）")
    ap.add_argument("--cids", type=int, nargs="*", help="detail 阶段仅爬指定课程 id")
    ap.add_argument("--shard", type=int, default=0, help="detail 分片序号 (1..shards)")
    ap.add_argument("--shards", type=int, default=1, help="detail 分片总数")
    args = ap.parse_args()
    if args.stage in ("list", "all"):
        cmd_list()
    if args.stage in ("detail", "all"):
        cmd_detail(limit=args.limit, cids=args.cids, shard=args.shard, shards=args.shards)
    if args.stage == "refill_stars":
        cmd_refill_stars()
    if args.stage in ("programs", "all"):
        cmd_programs()


if __name__ == "__main__":
    main()
