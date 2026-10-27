"""查重 Web 服务测试：令牌认证 E2E、任务生命周期（TTL）、入口限速、本地端点判断。

运行：pytest tests/test_service.py -v
不依赖真实比对库：run_check 被替换为桩函数，专注服务层逻辑。
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

import paper_check.service as svc
from paper_grader.llm import is_local_endpoint


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """干净的测试客户端：重置任务表、默认无令牌、替换 run_check 为桩。"""
    monkeypatch.setattr(svc, "ACCESS_TOKEN", "")
    monkeypatch.setattr(svc, "TASK_TTL_SECONDS", 7200)
    monkeypatch.setattr(svc, "RATE_PER_MIN", 30)
    svc._tasks.clear()
    svc._upload_log.clear()
    reports = tmp_path / "reports"
    reports.mkdir()

    def fake_run_check(db, reports_dir, path, use_semantic=True, verbose=False):
        out = reports / "r.html"
        out.write_text("<html>报告</html>", encoding="utf-8")
        return {
            "report_no": "PC-TEST-1",
            "html_path": str(out),
            "total_ratio": 0.366,
            "single_max_ratio": 0.2,
            "sources": [],
            "cached": False,
        }

    monkeypatch.setattr(svc, "run_check", fake_run_check)
    with TestClient(svc.app) as c:
        yield c


def _upload(client, name="论文.docx", content=b"%PDF-fake"):
    return client.post(
        "/api/check",
        files={"file": (name, content, "application/octet-stream")},
        data={"use_semantic": "true"},
    )


def _poll_done(client, task_id, timeout=5.0, headers=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/task/{task_id}", headers=headers)
        if r.status_code == 200 and r.json().get("status") in ("done", "error"):
            return r.json()
        time.sleep(0.05)
    raise TimeoutError(f"任务 {task_id} 未在 {timeout}s 内完成")


# ---------- 令牌认证 E2E（评审高1：受保护部署全链路可用） ----------
class TestTokenAuth:
    def test_protected_endpoints_require_token(self, client, monkeypatch):
        monkeypatch.setattr(svc, "ACCESS_TOKEN", "secret123")
        assert client.get("/api/stats").status_code == 401
        assert client.get("/api/task/xyz").status_code == 401
        assert _upload(client).status_code == 401

    def test_wrong_token_rejected(self, client, monkeypatch):
        monkeypatch.setattr(svc, "ACCESS_TOKEN", "secret123")
        r = client.get("/api/stats", headers={"X-Admin-Token": "wrong"})
        assert r.status_code == 401

    def test_token_e2e_upload_poll_report(self, client, monkeypatch):
        """设置 PAPER_CHECK_TOKEN 后，带令牌完成 上传→轮询→报告 全链路。"""
        monkeypatch.setattr(svc, "ACCESS_TOKEN", "secret123")
        h = {"X-Admin-Token": "secret123"}
        r = client.post(
            "/api/check",
            headers=h,
            files={"file": ("论文.docx", b"x", "application/octet-stream")},
        )
        assert r.status_code == 200, r.text
        task_id = r.json()["task_id"]

        r2 = client.get(f"/api/task/{task_id}", headers=h)
        assert r2.status_code == 200
        assert r2.json()["status"] == "done"

        rep = client.get(f"/api/report/{task_id}", headers=h)
        assert rep.status_code == 200
        assert "报告" in rep.text

    def test_report_without_token_blocked(self, client, monkeypatch):
        """报告链接不带令牌不可访问（评审指出的报告泄露面）。"""
        monkeypatch.setattr(svc, "ACCESS_TOKEN", "secret123")
        h = {"X-Admin-Token": "secret123"}
        task_id = client.post(
            "/api/check",
            headers=h,
            files={"file": ("a.docx", b"x", "application/octet-stream")},
        ).json()["task_id"]
        _poll_done(client, task_id, headers=h)
        assert client.get(f"/api/report/{task_id}").status_code == 401


# ---------- 无令牌模式 E2E（默认本机部署） ----------
class TestPlainE2E:
    def test_upload_poll_report(self, client):
        r = _upload(client)
        assert r.status_code == 200
        j = _poll_done(client, r.json()["task_id"])
        assert j["status"] == "done"
        assert j["total_ratio"] == 0.366
        rep = client.get(f"/api/report/{r.json()['task_id']}")
        assert rep.status_code == 200
        assert "Cache-Control" in rep.headers and "no-store" in rep.headers["Cache-Control"]

    def test_bad_extension_rejected(self, client):
        assert _upload(client, name="virus.exe").status_code == 400


# ---------- 任务生命周期（评审中3：TTL 与运行中任务保护） ----------
class TestTaskLifecycle:
    def test_expired_done_task_returns_410(self, client, monkeypatch):
        r = _upload(client)
        tid = r.json()["task_id"]
        _poll_done(client, tid)
        # 直接把 created_ts 拨回 TTL 之前
        svc._tasks[tid]["created_ts"] = time.time() - svc.TASK_TTL_SECONDS - 1
        assert client.get(f"/api/task/{tid}").status_code == 410
        assert client.get(f"/api/report/{tid}").status_code == 410

    def test_running_task_never_expires(self, client, monkeypatch):
        """进行中任务即使创建很久也不被判过期（TTL 只回收终态）。"""
        monkeypatch.setattr(svc, "TASK_TTL_SECONDS", 1)
        svc._tasks["t-running"] = {
            "status": "running",
            "stage": "精排",
            "pct": 50,
            "message": "",
            "filename": "x.docx",
            "created_ts": time.time() - 3600,
        }
        assert client.get("/api/task/t-running").status_code == 200
        svc._cleanup_expired()  # 清理也不得移除
        assert "t-running" in svc._tasks

    def test_cleanup_prefers_finished_over_running(self, client, monkeypatch):
        """容量淘汰：超限时淘汰最旧终态任务，运行中任务保留。"""
        monkeypatch.setattr(svc, "MAX_TASKS", 2)
        now = time.time()
        svc._tasks["old-done"] = {"status": "done", "created_ts": now - 100, "report_path": "x"}
        svc._tasks["running"] = {"status": "running", "created_ts": now - 50}
        svc._tasks["new-done"] = {"status": "done", "created_ts": now, "report_path": "y"}
        svc._cleanup_expired()  # 3 > 2，应淘汰最旧终态 old-done
        assert "old-done" not in svc._tasks
        assert "running" in svc._tasks and "new-done" in svc._tasks


# ---------- 入口限速（评审中3：入口限额） ----------
class TestRateLimit:
    def test_per_ip_limit(self, client, monkeypatch):
        monkeypatch.setattr(svc, "RATE_PER_MIN", 3)
        codes = [_upload(client, name=f"p{i}.docx").status_code for i in range(4)]
        assert codes == [200, 200, 200, 429]

    def test_window_slides(self, client, monkeypatch):
        monkeypatch.setattr(svc, "RATE_PER_MIN", 2)
        assert _upload(client).status_code == 200
        assert _upload(client).status_code == 200
        # 把窗口拨回 61 秒前 → 配额恢复
        svc._upload_log["testclient"] = [time.time() - 61]
        assert _upload(client).status_code == 200


# ---------- 本地端点严格判断（评审高2：绕过案例锁定） ----------
class TestLocalEndpoint:
    @pytest.mark.parametrize(
        "url,want",
        [
            ("http://localhost:11434/v1", True),
            ("http://127.0.0.1:8080/v1", True),
            ("http://127.8.8.8:8080/v1", True),  # 完整回环段
            ("http://[::1]:11434/v1", True),
            ("http://foo.localhost:9000/v1", True),  # RFC 6761
            ("http://localhost.evil.com/v1", False),  # hostname 精确匹配，不子串
            ("http://evil.com/?x=localhost", False),  # query 注入无效
            ("http://10.0.0.5:11434/v1", False),  # 内网≠本机
            ("http://0.0.0.0:11434/v1", False),
            ("", False),
        ],
    )
    def test_cases(self, url, want):
        assert is_local_endpoint(url) is want


# ---------- 并发安全（多线程同时上传不丢任务） ----------
class TestConcurrency:
    def test_parallel_uploads(self, client):
        results = []

        def up(i):
            results.append(_upload(client, name=f"c{i}.docx").status_code)

        threads = [threading.Thread(target=up, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results == [200] * 5
        assert len(svc._tasks) == 5
