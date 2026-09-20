from tests.test_api import csrf, logged_in_client


def test_history_is_persisted_and_scoped_to_authorized_organization() -> None:
    client = logged_in_client()
    try:
        created = client.post(
            "/api/organizations/demo-org/analyses",
            json={"projectId": "alpha", "mode": "recovery"},
            headers=csrf(client),
        )
        assert created.status_code == 200
        client.app.state.store.create_analysis("other-org", "someone", {}, "completed")
        response = client.get("/api/organizations/demo-org/analyses")
        assert response.status_code == 200
        assert [item["id"] for item in response.json()] == [created.json()["id"]]
        assert response.json()[0]["createdAt"]
        assert client.get("/api/organizations/other-org/analyses").status_code == 403
    finally:
        client.__exit__(None, None, None)
