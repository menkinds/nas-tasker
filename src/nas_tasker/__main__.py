import logging

import uvicorn

from .app import build_app
from .config import load_settings


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(
        build_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        log_level=settings.log_level.lower(),
        access_log=False,  # access lines would contain /ui/mount/<request_id>
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
