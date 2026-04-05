from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import apache_log_parser
import geoip2.database
import geoip2.errors
from ua_parser import parse as parse_ua

from .types import LogSummary

logger = logging.getLogger(__name__)

_COMBINED_FMT = '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"'
_line_parser = apache_log_parser.make_parser(_COMBINED_FMT)

CountryLookup = Callable[[str], str]

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "GeoLite2-Country.mmdb"


def make_country_lookup(db_path: str | Path = DEFAULT_DB_PATH) -> CountryLookup:
    """Open a GeoIP reader and return a lookup function that maps IP -> country name."""
    reader = geoip2.database.Reader(str(db_path))

    def lookup(ip: str) -> str:
        try:
            resp = reader.country(ip)
            return resp.country.name or "Unknown"
        except (geoip2.errors.AddressNotFoundError, ValueError):
            return "Unknown"

    return lookup


def parse_line(
    raw: str,
    country_lookup: CountryLookup,
) -> LogSummary | None:
    """Parse one raw Apache Combined log line into a LogSummary.

    Returns None when any stage (log parsing, UA resolution, geo lookup) fails.
    """
    try:
        parsed = _line_parser(raw.strip())
    except Exception:
        logger.warning("Failed to parse log line: %s", raw)
        return None

    try:
        ua_string = parsed.get("request_header_user_agent", "")
        ua_result = parse_ua(ua_string)
        os_family = ua_result.os.family if ua_result.os else "Other"
        browser_family = ua_result.user_agent.family if ua_result.user_agent else "Other"
    except Exception:
        logger.warning("Failed to parse user-agent: %s", ua_string)
        os_family = "Other"
        browser_family = "Other"

    try:
        ip = parsed["remote_host"]
        country = country_lookup(ip)
    except Exception:
        logger.warning("Failed geo lookup for IP: %s", parsed.get("remote_host", "?"))
        return None

    return LogSummary(ip=ip, os=os_family, browser=browser_family, country=country)
