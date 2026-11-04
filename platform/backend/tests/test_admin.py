"""Administration API: RBAC, moderation, support, settings, audit and exports."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import auth, register_user


async def _user_id(client, user) -> str:
    return (await client.get("/api/v1/users/me", headers=auth(user["access_token"]))).json()["id"]


async def test_dashboard_requires_staff(client, alice):
    response = await client.get("/api/v1/admin/stats", headers=auth(alice["access_token"]))
    assert response.status_code == 403


async def test_super_admin_identity_and_permissions(client, staff_user):
    response = await client.get("/api/v1/admin/me", headers=auth(staff_user["access_token"]))
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "super_admin"
    assert "users.block" in body["permissions"]
    assert "roles.write" in body["permissions"]


async def test_dashboard_stats_and_timeseries(client, staff_user, alice, bob):
    chat = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                              json={"type": "private",
                                    "member_ids": [await _user_id(client, bob)]})).json()
    await client.post(f"/api/v1/chats/{chat['id']}/messages",
                      headers=auth(alice["access_token"]),
                      json={"type": "text", "text": "hello"})

    stats = await client.get("/api/v1/admin/stats", headers=auth(staff_user["access_token"]))
    assert stats.status_code == 200
    body = stats.json()
    assert body["total_users"] >= 3
    assert body["total_messages"] >= 1
    assert body["messages_today"] >= 1
    assert body["online_users"] == 0

    series = await client.get("/api/v1/admin/stats/timeseries",
                              headers=auth(staff_user["access_token"]), params={"days": 7})
    assert series.status_code == 200
    assert len(series.json()) == 7
    assert sum(point["messages"] for point in series.json()) >= 1


async def test_health_endpoint(client, staff_user):
    response = await client.get("/api/v1/admin/health", headers=auth(staff_user["access_token"]))
    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert body["api"] == "ok"
    assert 0 <= body["cpu_percent"] <= 100


async def test_user_listing_search_and_detail(client, staff_user, alice):
    listing = await client.get("/api/v1/admin/users", headers=auth(staff_user["access_token"]))
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1

    searched = await client.get("/api/v1/admin/users", headers=auth(staff_user["access_token"]),
                                params={"q": "alice_test"})
    assert [u["username"] for u in searched.json()["items"]] == ["alice_test"]

    detail = await client.get(f"/api/v1/admin/users/{await _user_id(client, alice)}",
                              headers=auth(staff_user["access_token"]))
    assert detail.status_code == 200
    body = detail.json()
    assert body["user"]["username"] == "alice_test"
    assert "messages_sent" in body["stats"]
    assert isinstance(body["security_events"], list)


async def test_block_and_unblock_user(client, staff_user, alice):
    user_id = await _user_id(client, alice)

    blocked = await client.post(f"/api/v1/admin/users/{user_id}/block",
                                headers=auth(staff_user["access_token"]),
                                json={"blocked": True, "reason": "spam"})
    assert blocked.status_code == 204

    # Existing access token stops working immediately.
    assert (await client.get("/api/v1/users/me",
                             headers=auth(alice["access_token"]))).status_code == 403
    # And the account cannot log in again.
    login = await client.post("/api/v1/auth/login",
                              json={"username": "alice_test", "password": alice["password"]})
    assert login.status_code == 403

    unblocked = await client.post(f"/api/v1/admin/users/{user_id}/block",
                                  headers=auth(staff_user["access_token"]),
                                  json={"blocked": False})
    assert unblocked.status_code == 204
    assert (await client.post("/api/v1/auth/login",
                              json={"username": "alice_test",
                                    "password": alice["password"]})).status_code == 200


async def test_suspension_is_time_bounded(client, staff_user, alice):
    user_id = await _user_id(client, alice)
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    response = await client.post(f"/api/v1/admin/users/{user_id}/suspend",
                                 headers=auth(staff_user["access_token"]),
                                 json={"until": future, "reason": "abuse"})
    assert response.status_code == 204

    login = await client.post("/api/v1/auth/login",
                              json={"username": "alice_test", "password": alice["password"]})
    assert login.status_code == 403
    assert "suspended" in login.json()["detail"].lower()

    await client.post(f"/api/v1/admin/users/{user_id}/suspend",
                      headers=auth(staff_user["access_token"]),
                      json={"until": None, "reason": None})
    assert (await client.post("/api/v1/auth/login",
                              json={"username": "alice_test",
                                    "password": alice["password"]})).status_code == 200


async def test_support_role_is_limited(client, staff_user, support_user, alice):
    user_id = await _user_id(client, alice)

    # Support can read tickets...
    tickets = await client.get("/api/v1/admin/tickets", headers=auth(support_user["access_token"]))
    assert tickets.status_code == 200

    # ...but cannot moderate accounts.
    denied = await client.post(f"/api/v1/admin/users/{user_id}/block",
                               headers=auth(support_user["access_token"]),
                               json={"blocked": True})
    assert denied.status_code == 403


async def test_role_assignment_grants_permissions(client, staff_user, alice):
    user_id = await _user_id(client, alice)
    assigned = await client.post(f"/api/v1/admin/users/{user_id}/role",
                                 headers=auth(staff_user["access_token"]),
                                 json={"role": "moderator"})
    assert assigned.status_code == 204

    me = (await client.get("/api/v1/admin/me", headers=auth(alice["access_token"]))).json()
    assert me["role"] == "moderator"
    assert "users.block" in me["permissions"]

    # A moderator cannot edit roles.
    forbidden = await client.post("/api/v1/admin/roles", headers=auth(alice["access_token"]),
                                  json={"name": "rogue", "permissions": ["users.read"]})
    assert forbidden.status_code == 403

    removed = await client.post(f"/api/v1/admin/users/{user_id}/role",
                                headers=auth(staff_user["access_token"]), json={"role": "none"})
    assert removed.status_code == 204
    assert (await client.get("/api/v1/admin/me",
                             headers=auth(alice["access_token"]))).status_code == 200


async def test_reports_workflow_and_audit(client, staff_user, alice, bob):
    bob_id = await _user_id(client, bob)
    report = await client.post("/api/v1/reports", headers=auth(alice["access_token"]),
                               json={"target_type": "user", "target_id": bob_id,
                                     "reason": "spam", "description": "flooding"})
    assert report.status_code == 201
    report_id = report.json()["id"]

    listing = await client.get("/api/v1/admin/reports", headers=auth(staff_user["access_token"]))
    assert listing.json()["total"] == 1

    resolved = await client.post(f"/api/v1/admin/reports/{report_id}/resolve",
                                 headers=auth(staff_user["access_token"]),
                                 json={"status": "resolved", "resolution": "warned"})
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    audit = await client.get("/api/v1/admin/audit", headers=auth(staff_user["access_token"]),
                             params={"action": "report.resolve"})
    assert audit.status_code == 200
    assert len(audit.json()) == 1
    assert audit.json()[0]["target_id"] == report_id


async def test_ticket_workflow(client, staff_user, support_user, alice):
    created = await client.post("/api/v1/tickets", headers=auth(alice["access_token"]),
                                json={"subject": "Cannot login", "category": "account",
                                      "priority": "high", "body": "I lost my password"})
    assert created.status_code == 201
    ticket_id = created.json()["id"]

    mine = await client.get("/api/v1/tickets/mine", headers=auth(alice["access_token"]))
    assert len(mine.json()) == 1

    listing = await client.get("/api/v1/admin/tickets", headers=auth(support_user["access_token"]),
                               params={"status": "open"})
    assert listing.json()["total"] == 1

    reply = await client.post(f"/api/v1/admin/tickets/{ticket_id}/reply",
                              headers=auth(support_user["access_token"]),
                              json={"body": "Please reset via settings"})
    assert reply.status_code == 201
    assert reply.json()["is_staff"] is True

    assigned = await client.patch(f"/api/v1/admin/tickets/{ticket_id}",
                                  headers=auth(support_user["access_token"]),
                                  json={"assigned_to": support_user["id"], "priority": "urgent",
                                        "status": "pending"})
    assert assigned.status_code == 200
    assert assigned.json()["priority"] == "urgent"
    assert assigned.json()["status"] == "pending"
    assert assigned.json()["assigned_to"] == support_user["id"]

    detail = await client.get(f"/api/v1/admin/tickets/{ticket_id}",
                              headers=auth(staff_user["access_token"]))
    assert len(detail.json()["messages"]) == 2

    performance = await client.get("/api/v1/admin/tickets-performance",
                                   headers=auth(staff_user["access_token"]))
    assert performance.status_code == 200
    assert any(row["username"] == "support_agent" for row in performance.json())


async def test_settings_roundtrip_and_maintenance_mode(client, staff_user, alice):
    settings = await client.get("/api/v1/admin/settings", headers=auth(staff_user["access_token"]))
    assert settings.status_code == 200
    keys = {row["key"] for row in settings.json()}
    assert {"registration", "security", "maintenance"} <= keys

    updated = await client.put("/api/v1/admin/settings", headers=auth(staff_user["access_token"]),
                               json={"values": {"limits": {"max_file_size_bytes": 1048576,
                                                           "max_message_length": 4096,
                                                           "max_group_members": 100}}})
    assert updated.status_code == 200
    limits = next(row for row in updated.json() if row["key"] == "limits")
    assert limits["value"]["max_group_members"] == 100

    await client.post("/api/v1/admin/maintenance", headers=auth(staff_user["access_token"]),
                      json={"enabled": True, "message": "Upgrading"})
    blocked = await client.get("/api/v1/chats", headers=auth(alice["access_token"]))
    assert blocked.status_code == 503
    assert "maintenance" in blocked.json()["detail"].lower()

    # Authentication stays reachable so admins can still operate.
    assert (await client.get("/api/v1/healthz")).status_code == 200

    await client.post("/api/v1/admin/maintenance", headers=auth(staff_user["access_token"]),
                      json={"enabled": False})
    allowed = await client.get("/api/v1/chats", headers=auth(alice["access_token"]))
    assert allowed.status_code == 200


async def test_custom_roles(client, staff_user):
    created = await client.post("/api/v1/admin/roles", headers=auth(staff_user["access_token"]),
                                json={"name": "chat_auditor", "description": "read chats",
                                      "permissions": ["chats.read", "stats.read"]})
    assert created.status_code == 201
    role_id = created.json()["id"]

    invalid = await client.post("/api/v1/admin/roles", headers=auth(staff_user["access_token"]),
                                json={"name": "broken", "permissions": ["not.a.permission"]})
    assert invalid.status_code == 422

    duplicate = await client.post("/api/v1/admin/roles", headers=auth(staff_user["access_token"]),
                                  json={"name": "chat_auditor", "permissions": []})
    assert duplicate.status_code == 409

    updated = await client.patch(f"/api/v1/admin/roles/{role_id}",
                                 headers=auth(staff_user["access_token"]),
                                 json={"permissions": ["chats.read"]})
    assert updated.status_code == 200
    assert updated.json()["permissions"] == ["chats.read"]

    roles = (await client.get("/api/v1/admin/roles", headers=auth(staff_user["access_token"]))).json()
    super_admin = next(r for r in roles if r["name"] == "super_admin")
    immutable = await client.patch(f"/api/v1/admin/roles/{super_admin['id']}",
                                   headers=auth(staff_user["access_token"]),
                                   json={"permissions": []})
    assert immutable.status_code == 403


async def test_exports(client, staff_user):
    csv_response = await client.get("/api/v1/admin/export/users.csv",
                                    headers=auth(staff_user["access_token"]))
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "username" in csv_response.text.splitlines()[0]

    pdf_response = await client.get("/api/v1/admin/export/summary.pdf",
                                    headers=auth(staff_user["access_token"]))
    assert pdf_response.status_code == 200
    assert pdf_response.content.startswith(b"%PDF")

    unknown = await client.get("/api/v1/admin/export/unknown.csv",
                               headers=auth(staff_user["access_token"]))
    assert unknown.status_code == 404


async def test_audit_and_security_events(client, staff_user, alice):
    login = await client.post("/api/v1/auth/login",
                              json={"username": "alice_test", "password": alice["password"]})
    assert login.status_code == 200

    events = await client.get("/api/v1/admin/security-events",
                              headers=auth(staff_user["access_token"]),
                              params={"type": "auth.login"})
    assert events.status_code == 200
    assert len(events.json()) >= 1
    assert events.json()[0]["ip"] is not None

    await client.post(f"/api/v1/admin/users/{await _user_id(client, alice)}/block",
                      headers=auth(staff_user["access_token"]), json={"blocked": True})
    audit = await client.get("/api/v1/admin/audit", headers=auth(staff_user["access_token"]))
    assert audit.status_code == 200
    assert any(entry["action"] == "user.block" for entry in audit.json())
    assert any(entry["actor_id"] == staff_user["id"] for entry in audit.json())


async def test_chat_moderation(client, staff_user, alice, bob):
    chat = (await client.post("/api/v1/chats", headers=auth(alice["access_token"]),
                              json={"type": "group", "title": "To Moderate",
                                    "member_ids": [await _user_id(client, bob)]})).json()

    listing = await client.get("/api/v1/admin/chats", headers=auth(staff_user["access_token"]),
                               params={"type": "group"})
    assert listing.json()["total"] == 1

    detail = await client.get(f"/api/v1/admin/chats/{chat['id']}",
                              headers=auth(staff_user["access_token"]))
    assert len(detail.json()["members"]) == 2

    deleted = await client.delete(f"/api/v1/admin/chats/{chat['id']}",
                                  headers=auth(staff_user["access_token"]))
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/chats/{chat['id']}",
                             headers=auth(alice["access_token"]))).status_code == 404
