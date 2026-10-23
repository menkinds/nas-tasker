HTTP_STATUS = {
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "NOT_ALLOWED": 403,
    "SHARE_NOT_ALLOWED": 403,
    "INVALID_INPUT": 400,
    "INVALID_CONFIRM_TOKEN": 400,
    "CONFIRM_TOKEN_EXPIRED": 400,
    "WRONG_PASSWORD": 400,
    "TASK_NOT_FOUND": 404,
    "OUTPUT_NOT_FOUND": 404,
    "TASK_ALREADY_RUNNING": 409,
    "TASK_DISABLED": 409,
    "TASK_AMBIGUOUS": 409,
    "ALREADY_MOUNTED": 409,
    "NOT_MOUNTED": 409,
    "REQUEST_EXPIRED": 410,
    "RATE_LIMITED": 429,
    "INTERNAL": 500,
    "DSM_UNREACHABLE": 502,
    "DSM_AUTH_FAILED": 502,
    "DSM_API_ERROR": 502,
    "CONTAINER_LOOKUP_FAILED": 502,
}

# Audit log "result" is "denied" for these codes, "error" for the rest.
DENIED_CODES = frozenset(
    {"UNAUTHENTICATED", "FORBIDDEN", "NOT_ALLOWED", "SHARE_NOT_ALLOWED", "RATE_LIMITED"}
)


class ToolError(Exception):
    """An error safe to show to the caller. str(err) is only the code."""

    def __init__(self, code: str, message: str, dsm_error_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.message = message
        self.dsm_error_code = dsm_error_code

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)

    def to_dict(self) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.dsm_error_code is not None:
            err["dsm_error_code"] = self.dsm_error_code
        return {"error": err}
