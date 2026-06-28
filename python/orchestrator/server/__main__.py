"""
CLI entry point:  orchestrator serve
"""

import logging
import os
import sys

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "curate":
        from orchestrator.curation.cli import curate_cli
        sys.exit(curate_cli(sys.argv[2:]))

    # Default to running the server if first arg is 'serve' or not specified
    if len(sys.argv) >= 2 and sys.argv[1] not in ("serve", "curate"):
        print(f"Unknown subcommand: {sys.argv[1]}", file=sys.stderr)
        print("Usage: ses [serve | curate ...]", file=sys.stderr)
        sys.exit(1)

    import uvicorn

    host = os.getenv("System_HOST", "0.0.0.0")
    port = int(os.getenv("System_PORT", "8000"))

    uvicorn.run(
        "orchestrator.server.app:create_app",
        host=host,
        port=port,
        reload=False,
        factory=True,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()

