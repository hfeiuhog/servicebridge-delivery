from fastapi.testclient import TestClient

from enterprise.demo import create_app

SOURCE = "设备 SYN-A001 反复报E11，重启2次未恢复，影响2名前台人员无法收款，暂无替代设备。"


def prepare(client):
    draft = client.post("/api/intake", json={"source": SOURCE}).json()
    confirmed = client.post(
        "/api/confirm",
        json={
            "draft_id": draft["id"],
            "revision": draft["revision"],
            "final_content": {"candidate": draft["candidate"], "urgency": 2},
        },
    ).json()
    proposal = client.post("/api/prepare", json={"confirmation_id": confirmed["confirmation_id"]}).json()
    grant = client.post("/api/approve", json={"digest": proposal["change"]["digest"]}).json()["grant"]
    return proposal, grant


def test_demo_end_to_end_and_receipt_replay(tmp_path):
    app = create_app("sqlite:///" + str(tmp_path / "demo.sqlite"), test_host=True)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.post("/api/intake", json={"source": SOURCE}).status_code == 403
        client.headers["X-Demo-Token"] = app.state.demo_token
        proposal, grant = prepare(client)
        value = {"digest": proposal["change"]["digest"], "grant": grant}
        first = client.post("/api/deliver", json=value)
        assert first.status_code == 200
        assert first.json() == client.post("/api/deliver", json=value).json()
        assert app.state.counters["post"] == 1
        assert (
            client.post(
                "/api/intake", json={"source": SOURCE}, headers={"Origin": "https://attacker.invalid"}
            ).status_code
            == 403
        )


def test_demo_uncertain_does_not_resend_after_reconciliation(tmp_path):
    app = create_app("sqlite:///" + str(tmp_path / "timeout.sqlite"), test_host=True)
    with TestClient(app) as client:
        client.headers["X-Demo-Token"] = app.state.demo_token
        proposal, grant = prepare(client)
        value = {"digest": proposal["change"]["digest"], "grant": grant, "scenario": "post-timeout"}
        assert client.post("/api/deliver", json=value).status_code == 409
        report = client.post("/api/reconcile", json={"digest": value["digest"]}).json()
        assert report["local_state"] == "uncertain"
        assert report["simulated_remote_sys_id"]
        assert client.post("/api/deliver", json=value).status_code == 409
        assert app.state.counters["post"] == 1
