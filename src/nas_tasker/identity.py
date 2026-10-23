from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    subject: str  # verified, lower-cased email from the Access JWT
    auth_method: str  # "bearer+access" for the bot, "ui" for a person in the browser
