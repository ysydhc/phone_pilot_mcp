from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> int:
    """
    Start the MCP stdio server.

    This is a thin wrapper around `phone_pilot.mcp.server`.
    """
    p = argparse.ArgumentParser(description="Start phone-pilot MCP server (stdio by default).")
    p.add_argument(
        "--transport",
        default=os.environ.get("PHONE_PILOT_MCP_TRANSPORT", "stdio"),
        choices=["stdio"],
        help="MCP transport (currently only stdio is supported).",
    )
    ns = p.parse_args(argv)

    # Import at runtime to avoid import-time side effects for tooling.
    import phone_pilot.mcp.server as mcp
    mcp.run_server(transport=ns.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


