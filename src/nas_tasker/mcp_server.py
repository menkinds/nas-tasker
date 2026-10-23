from mcp.server.mcpserver import MCPServer

from .dispatch import Dispatcher, current_call

INSTRUCTIONS = (
    "Runs pre-approved Synology DSM tasks (names start with 'bot-') and mounts or "
    "unmounts allowlisted encrypted shares. Never ask the user for a share password in "
    "chat: mount_share returns a link the user opens in a browser. unmount_share needs "
    "two calls: first without confirm_token, show the user containers_using, then call "
    "again with the confirm_token once the user agrees."
)


def build_mcp(dispatcher: Dispatcher) -> MCPServer:
    mcp = MCPServer("nas-tasker", instructions=INSTRUCTIONS)

    async def call(tool: str, args: dict) -> dict:
        _, payload = await dispatcher.call(tool, args, current_call.get())
        return payload

    @mcp.tool()
    async def list_tasks() -> dict:
        """List DSM scheduled tasks whose names start with 'bot-'."""
        return await call("list_tasks", {})

    @mcp.tool()
    async def run_task(name: str) -> dict:
        """Start a 'bot-' task now. Refused if it is disabled or already running."""
        return await call("run_task", {"name": name})

    @mcp.tool()
    async def task_result(name: str, tail_lines: int = 100) -> dict:
        """Last run status of a 'bot-' task and the tail of its log (1-1000 lines)."""
        return await call("task_result", {"name": name, "tail_lines": tail_lines})

    @mcp.tool()
    async def mount_share(name: str, request_id: str | None = None) -> dict:
        """Start mounting an allowlisted encrypted share. Returns a ui_url for the user to
        type the password in. Call again with request_id to check the result."""
        args = {"name": name} if request_id is None else {"name": name, "request_id": request_id}
        return await call("mount_share", args)

    @mcp.tool()
    async def unmount_share(name: str, confirm_token: str | None = None) -> dict:
        """Unmount an allowlisted encrypted share in two steps (see server instructions)."""
        args = {"name": name}
        if confirm_token is not None:
            args["confirm_token"] = confirm_token
        return await call("unmount_share", args)

    return mcp
