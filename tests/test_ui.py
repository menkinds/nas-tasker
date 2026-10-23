import logging
import re

import pytest

from .app_helpers import audit_lines, bot_headers, make_client, ui_headers
from .mock_dsm import SHARE_PASSWORD


@pytest.fixture
def client(tmp_path, mock_dsm):
    with make_client(tmp_path, mock_dsm) as c:
        yield c


def start_mount(client) -> str:
    return client.post("/api/mount_share", headers=bot_headers(), json={"name": "secret"}).json()["request_id"]


def open_form(client, request_id) -> str:
    resp = client.get(f"/ui/mount/{request_id}", headers=ui_headers())
    assert resp.status_code == 200
    return re.search(r'name="csrf" value="([^"]+)"', resp.text).group(1)


def test_ui_path_needs_only_jwt_not_bearer(client):
    assert client.get("/ui/mount/nope", headers=ui_headers()).status_code == 410
    assert client.get("/ui/mount/nope").status_code == 401


def test_form_page(client):
    request_id = start_mount(client)
    resp = client.get(f"/ui/mount/{request_id}", headers=ui_headers())
    assert 'type="password"' in resp.text and 'autocomplete="off"' in resp.text
    assert 'method="post"' in resp.text
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["cache-control"] == "no-store"


def test_mount_end_to_end(client, mock_dsm, tmp_path):
    request_id = start_mount(client)
    csrf = open_form(client, request_id)
    resp = client.post(f"/ui/mount/{request_id}", headers=ui_headers(),
                       data={"csrf": csrf, "password": SHARE_PASSWORD})
    assert resp.status_code == 200 and "마운트 완료" in resp.text
    assert mock_dsm.shares["secret"]["is_mounted"] is True
    status = client.post("/api/mount_share", headers=bot_headers(),
                         json={"name": "secret", "request_id": request_id}).json()
    assert status == {"status": "mounted"}
    ui_entry = [e for e in audit_lines(tmp_path) if e["tool"] == "ui_mount"][0]
    assert ui_entry["auth_method"] == "ui" and ui_entry["result"] == "ok"
    assert ui_entry["args"]["name"] == "secret"


def test_double_submit_mounts_once(client, mock_dsm):
    request_id = start_mount(client)
    csrf = open_form(client, request_id)
    form = {"csrf": csrf, "password": SHARE_PASSWORD}
    client.post(f"/ui/mount/{request_id}", headers=ui_headers(), data=form)
    second = client.post(f"/ui/mount/{request_id}", headers=ui_headers(), data=form)
    assert second.status_code == 410
    assert mock_dsm.calls.count(("SYNO.Core.Share.Crypto", "decrypt")) == 1


def test_csrf_required(client, mock_dsm):
    request_id = start_mount(client)
    resp = client.post(f"/ui/mount/{request_id}", headers=ui_headers(),
                       data={"csrf": "forged", "password": SHARE_PASSWORD})
    assert resp.status_code == 403
    assert ("SYNO.Core.Share.Crypto", "decrypt") not in mock_dsm.calls


def test_get_never_mounts(client, mock_dsm):
    request_id = start_mount(client)
    client.get(f"/ui/mount/{request_id}?password={SHARE_PASSWORD}", headers=ui_headers())
    assert ("SYNO.Core.Share.Crypto", "decrypt") not in mock_dsm.calls


def test_expired_or_unknown_link_is_friendly(client):
    resp = client.get("/ui/mount/does-not-exist", headers=ui_headers())
    assert resp.status_code == 410 and "만료" in resp.text


def test_password_never_leaks(client, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    typo = "Typo-Pass-Ω-123"
    request_id = start_mount(client)
    csrf = open_form(client, request_id)
    wrong = client.post(f"/ui/mount/{request_id}", headers=ui_headers(),
                        data={"csrf": csrf, "password": typo})
    assert "암호가 틀렸습니다" in wrong.text
    right = client.post(f"/ui/mount/{request_id}", headers=ui_headers(),
                        data={"csrf": csrf, "password": SHARE_PASSWORD})
    status = client.post("/api/mount_share", headers=bot_headers(),
                         json={"name": "secret", "request_id": request_id})
    haystack = "\n".join([
        caplog.text,
        (tmp_path / "audit.jsonl").read_text(),
        wrong.text, right.text, status.text,
    ])
    for secret in (typo, SHARE_PASSWORD, "dsm-secret-pw", "t" * 40, "sid-1"):
        assert secret not in haystack, secret
