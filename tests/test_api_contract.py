"""接口契约：camelCase、只读约束、鉴权要求。"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from app.config import get_settings
from app.repositories.qa_log_repo import QaLogRepository


def _admin_headers() -> dict[str, str]:
    settings = get_settings()
    token = base64.b64encode(
        f"{settings.admin_username}:{settings.admin_password}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}"}


def test_ask_empty_question_returns_camel_case(client: TestClient, db: None) -> None:
    response = client.post("/api/ask", json={"question": ""})

    assert response.status_code == 200
    body = response.json()
    assert sorted(body) == ["answer", "path", "question", "sources", "topSimilarity"]
    assert body["path"] == "empty_question"
    assert body["sources"] == []


def test_ask_response_uses_camel_case_source_fields(
    client: TestClient, db: None, monkeypatch
) -> None:
    from app.services import qa_service
    from app.services.intent_service import IntentResult

    async def fake_intent(question: str, sections: list[str]) -> IntentResult:
        return IntentResult(
            intent="其他", emotion="normal", urgency="normal", section=None
        )

    async def fake_retrieve(question: str, top_k: int | None = None):
        return [
            qa_service.RetrievedClause(
                doc_title="《测试文档》",
                chapter="第一章",
                article_num="一",
                article_title="标题",
                content="正文",
                similarity=0.6,
            )
        ]

    async def fake_notices(question: str, top_k: int):
        return []

    async def fake_generate(question: str, clauses) -> str:
        return "答案"

    monkeypatch.setattr(qa_service, "analyze_intent", fake_intent)
    monkeypatch.setattr(qa_service, "retrieve_clauses", fake_retrieve)
    monkeypatch.setattr(qa_service, "search_notices", fake_notices)
    monkeypatch.setattr(qa_service, "_generate_answer", fake_generate)
    monkeypatch.setattr(qa_service.alert_service, "is_configured", lambda: False)

    body = client.post("/api/ask", json={"question": "宿舍能用电磁炉吗"}).json()

    assert body["path"] == "normal"
    assert body["topSimilarity"] == 0.6
    assert sorted(body["sources"][0]) == [
        "articleNum",
        "articleTitle",
        "chapter",
        "content",
        "docTitle",
        "similarity",
    ]


def test_admin_logs_require_auth_and_use_camel_case(
    client: TestClient, db: None
) -> None:
    assert client.get("/admin/api/logs").status_code == 401

    page = client.get("/admin/api/logs", headers=_admin_headers()).json()
    assert sorted(page) == ["content", "number", "size", "totalElements", "totalPages"]


def test_admin_stats_fields(client: TestClient, db: None) -> None:
    stats = client.get("/admin/api/stats", headers=_admin_headers()).json()
    assert sorted(stats) == [
        "daily",
        "pathDistribution",
        "recentCrisis",
        "recentVenting",
        "rejectionRate",
        "total",
    ]


def test_alert_test_requires_basic_auth(client: TestClient, db: None) -> None:
    assert client.post("/admin/api/alert/test", json={"msg": "x"}).status_code == 401

    response = client.post(
        "/admin/api/alert/test",
        headers=_admin_headers(),
        json={"msg": "x", "openid": "oTest"},
    )
    assert response.status_code == 200
    assert sorted(response.json()) == ["message", "success"]


def test_no_write_or_delete_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    methods = {method.upper() for operations in paths.values() for method in operations}
    assert not methods & {"PUT", "PATCH", "DELETE"}
    assert "/api/alert/test" not in paths  # 已挪到 /admin 下
    assert "/admin/api/alert/test" in paths


def test_qa_log_repository_has_no_update_or_delete() -> None:
    for name in ("update", "delete", "remove", "bulk_update"):
        assert not hasattr(QaLogRepository, name), f"qa_log 不允许出现 {name}"
