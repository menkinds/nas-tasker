import html
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .audit import AuditLogger, safe_args
from .dispatch import current_call, outcome_of
from .errors import ToolError
from .shares import ShareService
from .tasks import iso

SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
}

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nas-tasker</title>
<style>body{{font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem}}
input,button{{font-size:1rem;padding:.5rem;width:100%;box-sizing:border-box;margin-top:.5rem}}
.err{{color:#b00020}}</style></head>
<body>{body}</body></html>"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(body=body), status_code=status, headers=SECURITY_HEADERS)


def _form(request_id: str, share: str, csrf: str, expires_at: float, error: str = "") -> HTMLResponse:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return _page(
        f"<h1>공유 폴더 마운트</h1>"
        f"<p><b>{html.escape(share)}</b> 폴더의 암호를 입력하세요. "
        f"이 링크는 {html.escape(iso(expires_at))}까지 한 번만 쓸 수 있습니다.</p>{err}"
        f'<form method="post" action="/ui/mount/{html.escape(request_id)}" autocomplete="off">'
        f'<input type="hidden" name="csrf" value="{html.escape(csrf)}">'
        f'<input type="password" name="password" autocomplete="off" required autofocus>'
        f"<button type=\"submit\">마운트</button></form>"
    )


MESSAGES = {
    "REQUEST_EXPIRED": "링크가 만료되었거나 이미 사용되었습니다. 봇에게 다시 요청하세요.",
    "FORBIDDEN": "요청을 확인할 수 없습니다. 링크를 다시 열어 주세요.",
    "INVALID_INPUT": "암호를 입력하세요.",
    "DSM_UNREACHABLE": "NAS에 연결할 수 없습니다. 잠시 후 다시 시도하세요.",
}


def build_ui_router(shares: ShareService, audit: AuditLogger) -> APIRouter:
    router = APIRouter()

    @router.get("/ui/mount/{request_id}")
    async def mount_form(request_id: str) -> HTMLResponse:
        try:
            req = shares.open_request(request_id)
        except ToolError as err:
            return _page(f"<p>{MESSAGES[err.code]}</p>", err.http_status)
        return _form(req.id, req.share, req.csrf, req.expires_at)

    @router.post("/ui/mount/{request_id}")
    async def mount_submit(request_id: str, request: Request) -> HTMLResponse:
        started = time.monotonic()
        form = await request.form()
        csrf, password = form.get("csrf"), form.get("password")
        del form
        share = None
        code = dsm_code = None
        try:
            share = shares.open_request(request_id).share
            await shares.complete_mount(request_id, csrf, password)
            response = _page("<h1>마운트 완료</h1><p>이 창을 닫아도 됩니다.</p>")
        except ToolError as err:
            code, dsm_code = err.code, err.dsm_error_code
            if err.code == "WRONG_PASSWORD":
                try:
                    req = shares.open_request(request_id)
                    response = _form(req.id, req.share, req.csrf, req.expires_at, "암호가 틀렸습니다.")
                except ToolError:
                    response = _page("<p>암호를 여러 번 틀려 이 링크는 막혔습니다. 봇에게 다시 요청하세요.</p>", 400)
            else:
                message = MESSAGES.get(err.code, f"마운트하지 못했습니다 ({err.code}).")
                response = _page(f"<p>{html.escape(message)}</p>", err.http_status)
        finally:
            password = None  # noqa: F841
        ctx = current_call.get()
        audit.record(
            request_id=uuid.uuid4().hex,
            actor_email=ctx.identity.email,
            auth_method=ctx.identity.auth_method,
            client_ip=ctx.client_ip,
            tool="ui_mount",
            args=safe_args("ui_mount", {"name": share, "request_id": request_id}),
            result=outcome_of(code),
            error_code=code,
            duration_ms=round((time.monotonic() - started) * 1000),
            dsm_error_code=dsm_code,
        )
        return response

    return router
