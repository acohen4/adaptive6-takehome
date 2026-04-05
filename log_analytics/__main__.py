from __future__ import annotations

import argparse
import sys

from .parser import DEFAULT_DB_PATH, make_country_lookup
from .pipeline import analyze


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="log-analytics",
        description="Analyze Apache log files and report breakdowns by Country, OS, and Browser.",
    )
    parser.add_argument("logfile", help="Path to the Apache log file")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="Path to GeoLite2-Country.mmdb (default: data/GeoLite2-Country.mmdb)",
    )
    args = parser.parse_args(argv)

    country_lookup = make_country_lookup(args.db)

    try:
        with open(args.logfile, encoding="utf-8") as fh:
            analyze(fh, country_lookup=country_lookup)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
