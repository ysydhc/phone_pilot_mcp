from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> int:
    """
    Start the MCP stdio server.

    This is a thin wrapper around the existing server module `mcp_android`.
    """
    p = argparse.ArgumentParser(description="Start phone-touch MCP server (stdio by default).")
    p.add_argument(
        "--transport",
        default=os.environ.get("PHONE_TOUCH_MCP_TRANSPORT", "stdio"),
        choices=["stdio"],
        help="MCP transport (currently only stdio is supported).",
    )
    ns = p.parse_args(argv)

    # Import at runtime to avoid import-time side effects for tooling.
    from mcp_android import mcp  # noqa: WPS433

    mcp.run(transport=ns.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


