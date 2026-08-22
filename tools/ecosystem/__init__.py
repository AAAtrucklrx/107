# -*- coding: utf-8 -*-
"""生态工具包 —— Spec 驱动的第三方工具注册（P4-1 注册协议 v1）。

接入方式（三步，详见本目录 README.md）：
1. 写一个 Spec 文件 `tools/ecosystem/{tool}.spec.yaml`；
2. 写一个同名的实现文件 `{tool}.py`，暴露 `run(params: dict, ctx: dict) -> dict`；
3. 无需改任何其他代码——加载器自动扫描注册进 `agents/tool_registry.py`。

约束：
- 工具名必须 `eco:` 前缀（与内置工具隔离）；
- Spec 必填字段见 REQUIRED_FIELDS；缺字段/签名不符 → 拒载该工具并记日志，不影响其他工具；
- `run` 返回 dict 且必须含 `source`（中文来源标识，含提供者署名）；
- 失败路径返回 {"error": ...} 而非抛异常（由 act 层统一捕获）。
"""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import yaml

from utils.logger import get_logger

log = get_logger("xiaowo.ecosystem")

ECO_DIR = Path(__file__).resolve().parent

REQUIRED_FIELDS = ("name", "display_name", "provider", "description", "version",
                   "permission", "params_schema", "result_schema", "source_hint")
_VALID_PERMISSIONS = ("read_only", "write")

# 进程级缓存：注册表每次 act 都会重建（_build_tool_registry 被反复调用），
# 不缓存则每次工具调用都重新 yaml 解析 + import
_TOOLS_CACHE: dict | None = None
_SPECS_CACHE: list[dict] | None = None


def _validate_spec(spec: dict, spec_path: Path) -> str | None:
    """Spec 校验：返回错误描述；合法返回 None。"""
    if not isinstance(spec, dict):
        return "Spec 顶层必须是映射"
    missing = [f for f in REQUIRED_FIELDS if not spec.get(f)]
    if missing:
        return f"缺少必填字段: {', '.join(missing)}"
    name = str(spec["name"])
    if not name.startswith("eco:"):
        return f"工具名必须以 eco: 开头（当前: {name}）"
    if spec["permission"] not in _VALID_PERMISSIONS:
        return f"permission 必须为 {'/'.join(_VALID_PERMISSIONS)}（当前: {spec['permission']}）"
    if not isinstance(spec.get("params_schema"), dict) or not isinstance(spec.get("result_schema"), dict):
        return "params_schema / result_schema 必须是映射（JSON Schema 子集）"
    return None


def _load_module(py_path: Path):
    mod_name = f"ecotool_{py_path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, py_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_ctx() -> dict:
    """执行上下文：当前登录学号（未登录为空字符串）。"""
    try:
        from services.session_ctx import current_student
        return {"student_id": current_student()}
    except Exception:
        return {"student_id": ""}


def _scan(force: bool = False) -> tuple[dict, list[dict]]:
    """扫描并加载全部生态工具；返回 (tools, specs)。逐个加载，坏 Spec 拒载。"""
    global _TOOLS_CACHE, _SPECS_CACHE
    if _TOOLS_CACHE is not None and not force:
        return _TOOLS_CACHE, _SPECS_CACHE

    tools: dict = {}
    specs: list[dict] = []
    for spec_path in sorted(ECO_DIR.glob("*.spec.yaml")):
        try:
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"[ecosystem] Spec 解析失败，拒载 {spec_path.name}: {e}")
            continue

        err = _validate_spec(spec, spec_path)
        if err:
            log.warning(f"[ecosystem] Spec 校验失败，拒载 {spec_path.name}: {err}")
            continue

        py_path = spec_path.parent / f"{spec_path.stem.split('.')[0]}.py"
        if not py_path.exists():
            log.warning(f"[ecosystem] 缺少实现文件 {py_path.name}，拒载 {spec_path.name}")
            continue
        try:
            module = _load_module(py_path)
            run = getattr(module, "run", None)
            if not callable(run):
                raise ValueError("实现文件未暴露 run(params, ctx)")
            params = list(inspect.signature(run).parameters)
            if params[:2] != ["params", "ctx"]:
                raise ValueError(f"run 签名须为 run(params, ctx)（当前: {params}）")
        except Exception as e:
            log.warning(f"[ecosystem] 实现文件加载失败，拒载 {spec_path.name}: {e}")
            continue

        # 纯函数包装：act 层 func(**args) 直接兼容；错误返回 error 字段不抛异常
        def _make_wrapper(run_fn, spec_item):
            def _wrapper(**kwargs):
                try:
                    result = run_fn(dict(kwargs), _build_ctx())
                    if not isinstance(result, dict):
                        return {"error": f"生态工具返回了非 dict 结果: {result!r}"}
                    result.setdefault("source", str(spec_item.get("source_hint", "第三方工具")))
                    return result
                except Exception as e:  # noqa: BLE001
                    return {"error": f"生态工具执行失败: {e}"}
            _wrapper.__name__ = str(spec_item["name"]).replace(":", "_")
            _wrapper.__doc__ = f"{spec_item['display_name']}（第三方·{spec_item['provider']}）：{spec_item['description']}"
            return _wrapper

        name = str(spec["name"])
        tools[name] = _make_wrapper(run, spec)
        specs.append(spec)
        log.info(f"[ecosystem] 已注册生态工具 {name}（{spec['display_name']}·{spec['provider']}）")

    _TOOLS_CACHE, _SPECS_CACHE = tools, specs
    return tools, specs


def load_ecosystem_tools() -> dict:
    """{工具名: callable}，供注册表合并。"""
    return dict(_scan()[0])


def ecosystem_specs() -> list[dict]:
    """全部生态工具 Spec 元数据（UI/测试用）。"""
    return list(_scan()[1])


def reload_ecosystem_tools() -> tuple[dict, list[dict]]:
    """强制重扫（新增/修改 Spec 后调用；测试用）。"""
    return _scan(force=True)
