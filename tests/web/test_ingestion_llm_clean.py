"""LLM 语义清洗器：成功路径、异常/空/幻觉回退确定性清洗。"""

from __future__ import annotations

from xiaowo_web.worker.ingestion import IngestionWorker, LlmCleaner, _CleanPayload
from xiaowo_web.review import ReviewStore
from tests.web.helpers import make_settings

_SNAPSHOT = (
    "关于2026年秋季学期选课服务时段调整的通知\n\n"
    "为迎接本科教学审核评估，选课系统将于9月10日22:00至9月11日06:00停机维护。\n\n"
    "维护期间无法提交补退选申请，请各位同学提前规划。咨询服务电话0551-63600123。\n\n"
    "校园资讯平台为您推荐：二手交易平台、失物招领、快递代取等生活服务。"
)


def _worker(store: ReviewStore, invoke) -> IngestionWorker:
    return IngestionWorker(store, cleaner=LlmCleaner("test-model", invoke=invoke), worker_id="w-llm")


def test_llm_cleaner_uses_model_output(tmp_path) -> None:
    store = ReviewStore(make_settings(tmp_path))
    store.initialize()
    store.enqueue_candidate("demo", _candidate_snapshot())

    def invoke(prompt: str):
        assert "关键精炼知识稿" in prompt
        assert "0551-63600123" in prompt  # 原文注入
        return _CleanPayload(content="选课系统将于9月10日22:00至9月11日06:00停机维护。\n\n维护期间无法提交补退选申请，咨询电话0551-63600123。")

    assert _worker(store, invoke).run_once() == "done"
    items = store.list_items("demo")
    detail = store.get_item("demo", items[0]["item_id"])
    model_text = next(v["content_text"] for v in detail["versions"] if v["kind"] == "model")
    assert "停机维护" in model_text
    assert "二手交易平台" not in model_text  # 噪音被清洗掉
    assert detail["chunks"]


def _candidate_snapshot() -> dict:
    return {
        "source_id": "s1",
        "normalized_url": "https://www.teach.ustc.edu.cn/notice/llm",
        "final_url": "https://www.teach.ustc.edu.cn/notice/llm",
        "title": "选课服务时段调整通知",
        "institution": "测试机构",
        "level": "official_primary",
        "fetched_at": "2026-09-04T00:00:00Z",
        "content_type": "text/html",
        "snapshot_text": _SNAPSHOT,
        "evidence_span_hash": "span-llm",
    }


def test_llm_cleaner_falls_back_on_invocation_error(tmp_path) -> None:
    store = ReviewStore(make_settings(tmp_path))
    store.initialize()
    store.enqueue_candidate("demo", _candidate_snapshot())

    def invoke(prompt: str):
        raise TimeoutError("llm read timeout")

    assert _worker(store, invoke).run_once() == "done"
    items = store.list_items("demo")
    detail = store.get_item("demo", items[0]["item_id"])
    model_text = next(v["content_text"] for v in detail["versions"] if v["kind"] == "model")
    assert "二手交易平台" in model_text  # 回退到确定性输出（保留全部段落）


def test_llm_cleaner_falls_back_on_empty_content(tmp_path) -> None:
    store = ReviewStore(make_settings(tmp_path))
    store.initialize()
    store.enqueue_candidate("demo", _candidate_snapshot())

    assert _worker(store, lambda prompt: _CleanPayload(content="")).run_once() == "done"
    items = store.list_items("demo")
    detail = store.get_item("demo", items[0]["item_id"])
    model_text = next(v["content_text"] for v in detail["versions"] if v["kind"] == "model")
    assert len(model_text) >= 20


def test_llm_cleaner_rejects_hallucinated_numbers(tmp_path) -> None:
    store = ReviewStore(make_settings(tmp_path))
    store.initialize()
    store.enqueue_candidate("demo", _candidate_snapshot())

    def invoke(prompt: str):
        return _CleanPayload(content="选课系统将于12345678年9月停机维护，此数字原文中并不存在，属于编造。")

    assert _worker(store, invoke).run_once() == "done"
    items = store.list_items("demo")
    detail = store.get_item("demo", items[0]["item_id"])
    model_text = next(v["content_text"] for v in detail["versions"] if v["kind"] == "model")
    assert "12345678" not in model_text  # 幻觉数字触发回退，使用确定性输出
