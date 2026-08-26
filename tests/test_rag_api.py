import pytest
from fastapi.testclient import TestClient
from core.server import app

client = TestClient(app)
_LOCAL_ORIGIN = "http://127.0.0.1:8765"


def test_rag_strategies_endpoint():
    res = client.get("/api/v1/rag/strategies", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200
    data = res.json()
    assert "strategies" in data
    assert "default" in data
    names = [s["name"] for s in data["strategies"]]
    assert "hybrid_rrf" in names


def test_rag_progress_endpoint():
    res = client.get("/api/v1/rag/progress", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200
    data = res.json()
    assert "is_running" in data
    assert "progress_percent" in data


def test_rag_folders_and_files_lifecycle(tmp_path):
    folder_path = str(tmp_path / "rag_docs")
    tmp_path.mkdir(exist_ok=True)
    import os
    os.makedirs(folder_path, exist_ok=True)
    sample_file = os.path.join(folder_path, "test_doc.md")
    with open(sample_file, "w", encoding="utf-8") as f:
        f.write("# Hello DeskRAG\nThis is a test content.")

    # 1. Add folder
    add_res = client.post(
        "/api/v1/rag/folders",
        json={"path": folder_path, "name": "TestDocs"},
        headers={"Origin": _LOCAL_ORIGIN}
    )
    assert add_res.status_code == 200
    folder_id = add_res.json()["folder_id"]

    # 2. List folders
    list_res = client.get("/api/v1/rag/folders", headers={"Origin": _LOCAL_ORIGIN})
    assert list_res.status_code == 200
    folders = list_res.json()
    assert any(f["id"] == folder_id for f in folders)

    # 3. List files
    files_res = client.get("/api/v1/rag/files", headers={"Origin": _LOCAL_ORIGIN})
    assert files_res.status_code == 200
    files_data = files_res.json()
    assert "items" in files_data

    # 4. Delete folder
    del_res = client.delete(f"/api/v1/rag/folders/{folder_id}", headers={"Origin": _LOCAL_ORIGIN})
    assert del_res.status_code == 200


def test_rag_chat_sessions_lifecycle():
    # 1. Create session
    create_res = client.post(
        "/api/v1/rag/chat/sessions",
        json={"title": "Test Chat Session"},
        headers={"Origin": _LOCAL_ORIGIN}
    )
    assert create_res.status_code == 200
    session_id = create_res.json()["session_id"]

    # 2. List sessions
    list_res = client.get("/api/v1/rag/chat/sessions", headers={"Origin": _LOCAL_ORIGIN})
    assert list_res.status_code == 200
    sessions = list_res.json()
    assert any(s["id"] == session_id for s in sessions)

    # 3. Add message
    msg_res = client.post(
        "/api/v1/rag/chat/messages",
        json={
            "session_id": session_id,
            "role": "user",
            "content": "Hello RAG",
            "provider": "ollama",
            "model": "llama3.2:latest"
        },
        headers={"Origin": _LOCAL_ORIGIN}
    )
    assert msg_res.status_code == 200

    # 4. Get messages
    get_msgs_res = client.get(f"/api/v1/rag/chat/messages/{session_id}", headers={"Origin": _LOCAL_ORIGIN})
    assert get_msgs_res.status_code == 200
    msgs = get_msgs_res.json()
    assert len(msgs) >= 1
    assert msgs[0]["content"] == "Hello RAG"

    # 5. Delete session
    del_res = client.delete(f"/api/v1/rag/chat/sessions/{session_id}", headers={"Origin": _LOCAL_ORIGIN})
    assert del_res.status_code == 200
