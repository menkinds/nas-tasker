import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .dsm import DsmApiError, DsmClient
from .dsm.api import WRONG_PASSWORD_CODES
from .errors import ToolError
from .identity import Identity
from .tasks import iso
from .tokens import OneTimeStore, TokenExpired, TokenInvalid, new_token
from .validation import check_share_name

MAX_PASSWORD_ATTEMPTS = 5
KEEP_FINISHED_SECONDS = 24 * 3600


@dataclass
class MountRequest:
    id: str
    share: str
    expires_at: float
    csrf: str = field(default_factory=new_token, repr=False)
    status: str = "pending"  # pending | in_progress | mounted | failed
    attempts: int = 0


class ShareService:
    def __init__(
        self,
        dsm: DsmClient,
        allowlist: frozenset[str],
        public_base_url: str,
        mount_ttl_seconds: int,
        confirm_ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ):
        self._dsm = dsm
        self._allowlist = allowlist
        self._base_url = public_base_url
        self._mount_ttl = mount_ttl_seconds
        self._clock = clock
        self._mounts: dict[str, MountRequest] = {}
        self._confirm = OneTimeStore(confirm_ttl_seconds, clock)

    # --- mount: step 1 (bot) -------------------------------------------

    def _status_of(self, req: MountRequest) -> str:
        if req.status in ("pending", "in_progress") and self._clock() >= req.expires_at:
            return "expired"
        return req.status

    def _pending_response(self, req: MountRequest) -> dict:
        return {
            "status": "pending_user_input",
            "request_id": req.id,
            "ui_url": f"{self._base_url}/ui/mount/{req.id}",
            "expires_at": iso(req.expires_at),
        }

    async def mount_share(self, name: object, request_id: object = None) -> dict:
        name = check_share_name(name, self._allowlist)
        if request_id is not None:
            req = self._mounts.get(request_id) if isinstance(request_id, str) else None
            if req is None:
                return {"status": "expired"}  # unknown ids include ones lost on restart
            if req.share != name:
                raise ToolError("INVALID_INPUT", "request_id does not belong to this share")
            status = self._status_of(req)
            return self._pending_response(req) if status == "pending" else {"status": status}

        share = await self._dsm.get_share(name)
        if share.mounted:
            raise ToolError("ALREADY_MOUNTED", "share is already mounted")
        now = self._clock()
        self._mounts = {
            k: r for k, r in self._mounts.items() if r.expires_at + KEEP_FINISHED_SECONDS > now
        }
        for req in self._mounts.values():
            if req.share == name and self._status_of(req) == "pending":
                return self._pending_response(req)
        req = MountRequest(id=new_token(), share=name, expires_at=now + self._mount_ttl)
        self._mounts[req.id] = req
        return self._pending_response(req)

    # --- mount: step 2 (person, web UI) ----------------------------------

    def open_request(self, request_id: str) -> MountRequest:
        req = self._mounts.get(request_id)
        if req is None or self._status_of(req) != "pending":
            raise ToolError("REQUEST_EXPIRED", "this mount link is expired or already used")
        return req

    async def complete_mount(self, request_id: str, csrf: object, password: object) -> str:
        req = self.open_request(request_id)
        if not isinstance(csrf, str) or not hmac.compare_digest(csrf, req.csrf):
            raise ToolError("FORBIDDEN", "invalid form token")
        if not isinstance(password, str) or not password:
            raise ToolError("INVALID_INPUT", "password is required")
        req.status = "in_progress"
        req.attempts += 1
        try:
            # A retry after a lost reply: DSM may have mounted it already.
            if not (await self._dsm.get_share(req.share)).mounted:
                await self._dsm.mount_share(req.share, password)
        except DsmApiError as err:
            if err.dsm_error_code in WRONG_PASSWORD_CODES:
                req.status = "failed" if req.attempts >= MAX_PASSWORD_ATTEMPTS else "pending"
                raise ToolError("WRONG_PASSWORD", "wrong password") from None
            req.status = "failed"
            raise
        except ToolError:
            req.status = "pending"  # DSM unreachable: the person may try again
            raise
        except BaseException:
            req.status = "failed"
            raise
        finally:
            password = None  # drop our reference right away  # noqa: F841
        req.status = "mounted"
        return req.status

    # --- unmount ---------------------------------------------------------

    async def unmount_share(self, name: object, confirm_token: object, identity: Identity) -> dict:
        name = check_share_name(name, self._allowlist)
        if confirm_token is None:
            share = await self._dsm.get_share(name)
            if not share.mounted:
                raise ToolError("NOT_MOUNTED", "share is not mounted")
            using = await self._dsm.list_containers_using(name)
            token, expires_at = self._confirm.issue({"share": name, "actor": identity.subject})
            return {
                "status": "confirmation_required",
                "share": name,
                "containers_using": [
                    {"name": c.name, "state": c.state, "mount_path": c.mount_path} for c in using
                ],
                "confirm_token": token,
                "expires_at": iso(expires_at),
            }
        try:
            self._confirm.take(
                confirm_token,
                lambda p: p["share"] == name and p["actor"] == identity.subject,
            )
        except TokenExpired:
            raise ToolError("CONFIRM_TOKEN_EXPIRED", "confirmation expired, start again") from None
        except TokenInvalid:
            raise ToolError("INVALID_CONFIRM_TOKEN", "invalid confirmation token") from None
        share = await self._dsm.get_share(name)
        if not share.mounted:
            raise ToolError("NOT_MOUNTED", "share is not mounted")
        await self._dsm.unmount_share(name)
        return {"status": "unmounted"}
