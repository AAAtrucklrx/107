# -*- coding: utf-8 -*-
"""任务 A：全量爬取 jw.ustc.edu.cn 本科主修培养方案（1201 个）到 scripts/data/programs_jw/。

- 复用已登录的 Edge 窗口（CDP 127.0.0.1:9223），用 ctx.request 发请求带登录态，只读使用，
  不新建/不关闭页面。
- 列表阶段（search, queryPage__ 分页）取每个方案的元数据（name/grade/department/major/...）；
  详情阶段（root-module-json/{pid}）取模块树与课程。
- 支持断点续爬（{pid}.json 已存在则跳过，临时文件写完再落盘）与失败重试
  （3 次，退避 1s/2s/4s）；最终失败写入 scripts/data/programs_jw.log，不中断全量。
- 每 100 个打印一次进度；结束打印汇总。
"""
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"
SEARCH_URL = "https://jw.ustc.edu.cn/for-std/program-search/search"
# 详情树：2026-09-03 实测 2763/3011 均含“英语通修”分组，树完整
DETAIL_URL = "https://jw.ustc.edu.cn/for-std/program-search/root-module-json"
PAGE_SIZE = 50

PROJ = Path(__file__).resolve().parents[1]
DATA_DIR = PROJ / "scripts" / "data" / "programs_jw"
LOG_FILE = PROJ / "scripts" / "data" / "programs_jw.log"


def log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def fetch_json(ctx, url: str, retries: int = 3):
    """带重试的 GET，返回 JSON；判断未登录（302/401/403）则抛明确错误。"""
    for attempt in range(retries):
        try:
            resp = ctx.request.get(url)
        except Exception as e:
            if attempt == retries - 1:
                raise RuntimeError(f"请求异常 {url}: {e}")
            time.sleep([1, 2, 4][attempt])
            continue
        if resp.status == 200:
            try:
                return resp.json()
            except Exception as e:
                if attempt == retries - 1:
                    raise RuntimeError(f"JSON 解析失败 {url}: {e}")
        else:
            if resp.status in (302, 401, 403):
                raise RuntimeError(f"未登录或无权访问 {url} -> HTTP {resp.status}")
        time.sleep([1, 2, 4][attempt])
    raise RuntimeError(f"请求失败 {url}")


def pick_credit(course_obj: dict):
    """从 course 对象取学分，字段名以实测为准（credit/credits/points 等）。"""
    if not isinstance(course_obj, dict):
        return 0
    for k in ("credit", "credits", "creditHours", "score", "points", "creditNum"):
        if k in course_obj and course_obj[k] is not None:
            v = course_obj[k]
            if isinstance(v, dict):
                if "value" in v:
                    return v["value"]
                continue
            return v
    return 0


def pick_exam(course_obj: dict):
    """取考核方式文字（examMode 可能为 str/dict）。"""
    em = course_obj.get("examMode")
    if em is None:
        return ""
    if isinstance(em, str):
        return em
    if isinstance(em, dict):
        for k in ("nameZh", "name", "value"):
            if em.get(k):
                return em[k]
        return json.dumps(em, ensure_ascii=False)
    return str(em)


def extract_courses(node, parent_cats):
    """递归遍历模块树，产出课程记录；category 为模块路径（父/子 type.nameZh）。"""
    results = []
    t = node.get("type")
    node_type = t.get("nameZh") if isinstance(t, dict) else None
    cats = parent_cats + ([node_type] if node_type else [])
    cur_cat = "/".join(cats)

    for pc in node.get("planCourses") or []:
        c = pc.get("course") or {}
        results.append({
            "code": c.get("code", ""),
            "name": c.get("nameZh", ""),
            "required": "必修" if pc.get("compulsory") else "选修",
            "exam": pick_exam(pc),
            "credit": pick_credit(c),
            "category": cur_cat or "",
            "term": ",".join(pc.get("readableTerms") or []),
        })

    for child in node.get("children") or []:
        results.extend(extract_courses(child, cats))
    return results


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP)
            ctx = browser.contexts[0]
        except Exception as e:
            print(f"错误: CDP 连接失败或浏览器未运行在 127.0.0.1:9223 ({e})，请由指挥中心登录后重试")
            sys.exit(1)

        # ---- 阶段 1：列表分页，收集全部方案元数据 ----
        # 先取第 1 页确认登录态与总页数
        try:
            seed = int(time.time() * 1000)
            first = fetch_json(ctx, f"{SEARCH_URL}?type=MAJOR&queryPage__=1,{PAGE_SIZE}&_={seed}")
        except Exception as e:
            print(f"错误: 登录态校验失败 {e}（可能未登录/请求被拒），退出")
            sys.exit(1)
        pager = first.get("_page_") or {}
        total = pager.get("totalRows") or 0
        total_pages = pager.get("totalPages") or 0
        print(f"登录态 OK。totalRows={total}, totalPages={total_pages}")

        meta = {}  # pid -> dict
        pages_json = [first]
        for pg in range(2, total_pages + 1):
            seed = int(time.time() * 1000)
            try:
                j = fetch_json(ctx, f"{SEARCH_URL}?type=MAJOR&queryPage__={pg},{PAGE_SIZE}&_={seed}")
            except Exception as e:
                print(f"错误: 列表分页 {pg} 抓取中断 {e}")
                sys.exit(1)
            pages_json.append(j)
            time.sleep(random.uniform(0.2, 0.5))

        for j in pages_json:
            for item in j.get("data") or []:
                prog = item.get("program") or {}
                pid = prog.get("id")
                if pid is None:
                    continue
                dept = prog.get("department") or {}
                major = prog.get("major") or {}
                meta[str(pid)] = {
                    "name": prog.get("nameZh") or "",
                    "nameEn": prog.get("nameEn"),
                    "grade": (prog.get("grade") or "") + "级",
                    "bizType": (prog.get("bizType") or {}).get("nameZh") or "",
                    "department": dept.get("nameZh") or "",
                    "major": major.get("nameZh") or "",
                    "stdType": (prog.get("stdType") or {}).get("nameZh") or "",
                    "education": (prog.get("education") or {}).get("nameZh") or "",
                }
        pids = list(meta.keys())
        print(f"列表共采集 {len(pids)} 个方案（期望 {total}）")

        # ---- 阶段 2：逐个方案详情 ----
        failed = 0
        done = 0
        n = len(pids)
        for pid in pids:
            out = DATA_DIR / f"{pid}.json"
            if out.exists():
                done += 1
                if done % 100 == 0 or done == n:
                    print(f"方案进度: {done}/{n} 失败 {failed}", flush=True)
                continue
            try:
                tree = fetch_json(ctx, f"{DETAIL_URL}/{pid}")
            except Exception as e:
                failed += 1
                log(f"FAIL {pid}: {e}")
                done += 1
                if done % 100 == 0 or done == n:
                    print(f"方案进度: {done}/{n} 失败 {failed}", flush=True)
                continue

            require = tree.get("requireInfo") or {}
            m = meta.get(pid, {})
            record = {
                "pid": int(pid),
                "name": m.get("name", ""),
                "nameEn": m.get("nameEn"),
                "grade": m.get("grade", ""),
                "bizType": m.get("bizType", ""),
                "department": m.get("department", ""),
                "major": m.get("major", ""),
                "stdType": m.get("stdType", ""),
                "education": m.get("education", ""),
                "totalCredits": require.get("requiredCredits"),
                "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "courses": extract_courses(tree, []),
            }
            tmp = out.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=1)
            tmp.replace(out)
            done += 1
            if done % 100 == 0 or done == n:
                print(f"方案进度: {done}/{n} 失败 {failed}", flush=True)
            time.sleep(random.uniform(0.2, 0.5))

        print(f"\n完成: 共 {n} 个方案，生成 {done - failed} 个，失败 {failed}")
        log(f"SUMMARY total={n} done={done - failed} failed={failed}")


if __name__ == "__main__":
    main()