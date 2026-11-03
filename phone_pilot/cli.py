from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> int:
    """
    Start the MCP stdio server or generate telemetry dashboard.
    """
    import sys

    raw_args = argv if argv is not None else sys.argv[1:]
    if raw_args and raw_args[0] == "dashboard":
        p = argparse.ArgumentParser(
            prog="phone-pilot dashboard",
            description="Generate Telemetry & Scoring Dashboard HTML report.",
        )
        p.add_argument(
            "--output",
            default=".recordings/pp_dashboard.html",
            help="Dashboard HTML output path.",
        )
        p.add_argument(
            "--telemetry",
            default="",
            help="Path to telemetry JSONL file.",
        )
        p.add_argument(
            "--graph",
            default="",
            help="Optional UI knowledge graph JSON file to embed.",
        )
        ns = p.parse_args(raw_args[1:])
        from phone_pilot.core.telemetry.dashboard import DashboardGenerator

        gen = DashboardGenerator(telemetry_path=ns.telemetry or None)
        out = gen.generate_html(output_path=ns.output, graph_path=ns.graph or None)
        print(f"Phone Pilot Dashboard generated successfully: {out}")
        return 0

    p = argparse.ArgumentParser(
        description="Start phone-pilot MCP server (stdio by default)."
    )
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
