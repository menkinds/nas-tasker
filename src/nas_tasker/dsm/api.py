"""DSM Web API names and response fields used by nas-tasker.

PROVISIONAL: everything except SYNO.API.Auth is unofficial and must be checked
against the real NAS (plan Task 12). When it changes, change it here, in
client.py parsing, and in tests/mock_dsm.py together.
"""

ENTRY_PATH = "/webapi/entry.cgi"

AUTH = ("SYNO.API.Auth", 6)
TASK_SCHEDULER = ("SYNO.Core.TaskScheduler", 3)
SHARE = ("SYNO.Core.Share", 1)
SHARE_CRYPTO = ("SYNO.Core.Share.Crypto", 1)
DOCKER_CONTAINER = ("SYNO.Docker.Container", 1)

# The only (api, method) pairs the client will ever send. No create/set/
# delete/enable/disable, no Docker start/stop, no file APIs.
ALLOWED_METHODS = frozenset(
    {
        ("SYNO.API.Auth", "login"),
        ("SYNO.Core.TaskScheduler", "list"),
        ("SYNO.Core.TaskScheduler", "run"),
        ("SYNO.Core.Share", "get"),
        ("SYNO.Core.Share.Crypto", "decrypt"),  # mount an encrypted share
        ("SYNO.Core.Share.Crypto", "encrypt"),  # unmount an encrypted share
        ("SYNO.Docker.Container", "list"),
        ("SYNO.Docker.Container", "get"),
    }
)

# 106 session timeout, 107 duplicate login, 119 SID not found
SESSION_ERROR_CODES = frozenset({106, 107, 119})
WRONG_PASSWORD_CODES = frozenset({3302})  # PROVISIONAL
