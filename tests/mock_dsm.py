"""In-process fake of the DSM Web API, served through httpx.MockTransport.

Mirrors the PROVISIONAL names in nas_tasker/dsm/api.py.
"""

import json
from urllib.parse import parse_qsl

import httpx

DSM_USER = "nas-tasker"
DSM_PASS = "dsm-secret-pw"
SHARE_PASSWORD = "share-pw-Zq9!"


def ok(data=None):
    return httpx.Response(200, json={"success": True, "data": data or {}})


def fail(code):
    return httpx.Response(200, json={"success": False, "error": {"code": code}})


class MockDsm:
    def __init__(self):
        self.tasks = [
            {"id": 1, "name": "bot-gbrain-rebuild", "enable": True, "running": False,
             "real_owner": "root", "last_run_time": "2026-10-22 03:00", "last_exit_code": 0,
             "next_trigger_time": "2026-10-24 03:00"},
            {"id": 2, "name": "nightly-backup", "enable": True, "running": False,
             "real_owner": "root", "last_run_time": None, "last_exit_code": None,
             "next_trigger_time": None},
            {"id": 3, "name": "bot-disabled", "enable": False, "running": False,
             "real_owner": "root", "last_run_time": None, "last_exit_code": None,
             "next_trigger_time": None},
        ]
        self.shares = {
            "secret": {"encryption": 1, "is_mounted": False, "password": SHARE_PASSWORD},
            "other": {"encryption": 1, "is_mounted": True, "password": "x"},
        }
        self.containers = [
            {"name": "jellyfin", "status": "running",
             "binds": [{"host_volume_file": "/secret/media", "mount_point": "/media"}]},
            {"name": "unrelated", "status": "stopped",
             "binds": [{"host_volume_file": "/docker/app", "mount_point": "/app"}]},
        ]
        self.sids: set[str] = set()
        self.logins = 0
        self.calls: list[tuple[str, str]] = []
        self.runs: list[int] = []
        self.down = False
        self.run_sets_running = True  # False simulates DSM status lag
        self.fail_containers = False
        self.lose_run_reply = False  # DSM runs the task but the reply never arrives
        self.refuse_run = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def expire_sessions(self):
        self.sids.clear()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        assert request.url.path == "/webapi/entry.cgi"
        form = dict(parse_qsl(request.content.decode()))
        api, method = form["api"], form["method"]
        self.calls.append((api, method))
        if api == "SYNO.API.Auth" and method == "login":
            if form.get("account") != DSM_USER or form.get("passwd") != DSM_PASS:
                return fail(400)
            self.logins += 1
            sid = f"sid-{self.logins}"
            self.sids.add(sid)
            return ok({"sid": sid})
        if form.get("_sid") not in self.sids:
            return fail(119)
        handler = getattr(self, f"_{api.replace('.', '_')}_{method}", None)
        if handler is None:
            return fail(103)
        return handler(form)

    def _SYNO_Core_TaskScheduler_list(self, form):
        return ok({"tasks": [dict(t) for t in self.tasks], "total": len(self.tasks)})

    def _SYNO_Core_TaskScheduler_run(self, form):
        if self.refuse_run:
            return fail(117)
        for item in json.loads(form["tasks"]):
            task = next(t for t in self.tasks if t["id"] == item["id"])
            self.runs.append(task["id"])
            if self.run_sets_running:
                task["running"] = True
        if self.lose_run_reply:
            raise httpx.ReadTimeout("timed out")
        return ok()

    def _SYNO_Core_Share_get(self, form):
        share = self.shares.get(form["name"])
        if share is None:
            return fail(3300)
        return ok({"shares": [{"name": form["name"], "encryption": share["encryption"],
                               "is_mounted": share["is_mounted"]}]})

    def _SYNO_Core_Share_Crypto_decrypt(self, form):
        share = self.shares[form["name"]]
        if form["password"] != share["password"]:
            return fail(3302)
        share["is_mounted"] = True
        return ok()

    def _SYNO_Core_Share_Crypto_encrypt(self, form):
        self.shares[form["name"]]["is_mounted"] = False
        return ok()

    def _SYNO_Docker_Container_list(self, form):
        if self.fail_containers:
            return fail(117)
        return ok({"containers": [{"name": c["name"], "status": c["status"]}
                                  for c in self.containers]})

    def _SYNO_Docker_Container_get(self, form):
        c = next(c for c in self.containers if c["name"] == form["name"])
        return ok({"profile": {"name": c["name"], "volume_bindings": c["binds"]}})
