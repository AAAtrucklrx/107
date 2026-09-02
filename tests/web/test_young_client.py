"""Young 平台客户端：活动字段解析（地点/校区/联系人/参与形式）+ 详情接口单测。

回归基线 2026-09-02 实测：
- 列表接口记录含 placeInfo/xq/linkMan/tel/form_dictText（needPlaceApply=1 时 placeInfo 非空）
- 详情接口 /mobile/item/queryItemById 返回 142 字段，含 placeInfo/xq/linkMan/tel/formName
- 无 X-Access-Token 时所有接口 500 "Token失效"（公开性已在调研确认）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _provider(token: str = "x" * 64):
    from services.young_client import EncryptedHttpProvider

    return EncryptedHttpProvider(token)


def test_list_parses_place_contact_form(monkeypatch) -> None:
    """列表记录解析：placeInfo/xq/linkMan/tel/form_dictText → 新字段。"""
    from services import young_client as yc

    records = [{
        "id": "a1", "itemName": "口琴协会活力课堂", "st": "2026-09-11 19:30:00",
        "et": "2026-09-11 20:30:00", "applySt": None, "applyEt": "2026-09-11 17:00:00",
        "businessDeptName": "学生口琴协会", "itemCategory_dictText": "系列项目",
        "module": "z", "favCount": "3", "peopleNum": "12", "serviceHour": "1",
        "baseContent": "介绍", "placeInfo": "3C301", "xq": "1",
        "linkMan": "徐小航", "tel": "13391061820", "form_dictText": "现场参与",
    }]
    monkeypatch.setattr(
        yc.requests, "get",
        lambda *a, **kw: _FakeResp({"success": True, "result": {"records": records, "total": 1}}),
    )
    acts = _provider().fetch_enrolment_activities(page_size=50)
    assert len(acts) == 1
    act = acts[0]
    assert act.place_info == "3C301"
    assert act.xq == "1"
    assert act.contact == "徐小航 13391061820"
    assert act.form == "现场参与"
    assert act.apply_end == "2026-09-11 17:00:00"
    assert act.people_num == 12


def test_list_tolerates_missing_fields() -> None:
    """老活动（无地点/联系人字段）解析不崩，新字段为空串。"""
    from services.young_client import YoungService, YoungActivity
    from tools.activity_tools import _load_snapshot_activities  # noqa: F401  (导入链正常)

    acts = [YoungActivity(id="a2", name="无地点的活动")]
    assert acts[0].place_info == ""
    assert acts[0].contact == ""
    assert acts[0].form == ""


def test_contact_text_joining() -> None:
    from services.young_client import _contact_text

    assert _contact_text("徐小航", "13391061820") == "徐小航 13391061820"
    assert _contact_text("", "133") == "133"
    assert _contact_text("徐", "") == "徐"
    assert _contact_text(None, None) == ""


def test_fetch_item_detail(monkeypatch) -> None:
    """详情接口：返回 result dict；失败返回 None。"""
    from services import young_client as yc

    detail = {"id": "a1", "placeInfo": "3C301", "linkMan": "徐小航", "tel": "13391061820",
              "formName": "现场参与", "st": "2026-09-11 19:30:00"}
    calls = []

    def fake_get(path, payload=None):
        calls.append((path, payload))
        return {"success": True, "result": detail}

    prov = _provider()
    monkeypatch.setattr(prov, "_get", fake_get)
    assert prov.fetch_item_detail("a1") == detail
    assert calls == [("/mobile/item/queryItemById", {"id": "a1"})]
    assert prov.fetch_item_detail("") is None

    monkeypatch.setattr(prov, "_get", lambda path, payload=None: {"success": True, "result": None})
    assert prov.fetch_item_detail("a1") is None


def test_enrich_places_fills_from_detail(monkeypatch) -> None:
    """_enrich_places：缺地点的活动从详情接口补全，已有值不重复请求。"""
    from services import young_client as yc
    from tools import activity_tools as at

    class _FakeSvc:
        def __init__(self, *_a, **_kw):
            pass

        @classmethod
        def from_token(cls, token: str):
            return cls(token)

        def fetch_item_detail(self, item_id: str):
            return {"placeInfo": "3C301", "xq": "1", "linkMan": "徐小航",
                    "tel": "13391061820", "formName": "现场参与"}

    monkeypatch.setattr(yc, "YoungService", _FakeSvc)
    monkeypatch.setattr(at.time, "sleep", lambda _s: None)
    at._detail_cache.clear()

    missing = yc.YoungActivity(id="a1", name="缺地点")
    filled = yc.YoungActivity(id="a2", name="已有地点", place_info="5教201")
    out = at._enrich_places([missing, filled], max_fill=4)
    assert out[0].place_info == "3C301"
    assert out[0].contact == "徐小航 13391061820"
    assert out[0].form == "现场参与"
    assert out[1].place_info == "5教201"  # 已有值不动


def test_enrich_places_bounds_and_graceful(monkeypatch) -> None:
    """补全上限 max_fill；详情异常只跳过不抛出。"""
    from services import young_client as yc
    from tools import activity_tools as at

    class _FakeSvc:
        def __init__(self, *_a, **_kw):
            pass

        @classmethod
        def from_token(cls, token: str):
            return cls(token)

        def fetch_item_detail(self, item_id: str):
            if item_id == "bad":
                raise RuntimeError("token 失效")
            return {"placeInfo": "东区操场"}

    monkeypatch.setattr(yc, "YoungService", _FakeSvc)
    monkeypatch.setattr(at.time, "sleep", lambda _s: None)
    at._detail_cache.clear()

    acts = [yc.YoungActivity(id=f"a{i}", name=f"活动{i}") for i in range(5)]
    acts.append(yc.YoungActivity(id="bad", name="异常项"))
    out = at._enrich_places(acts, max_fill=3)
    filled = [a for a in out if a.place_info]
    assert len(filled) == 3  # 上限生效，坏项不占名额也不抛错
