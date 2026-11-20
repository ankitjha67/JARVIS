"""Flight search tools: build search URLs for Google Flights and Skyscanner."""

from __future__ import annotations

import asyncio
import webbrowser
from datetime import datetime
from urllib.parse import quote_plus

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


class FlightSearchArgs(Args):
    origin: str = Field(..., min_length=1, max_length=100, description="Departure city or airport code")
    destination: str = Field(..., min_length=1, max_length=100, description="Arrival city or airport code")
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="Departure date YYYY-MM-DD")
    return_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Return date YYYY-MM-DD for round trip")


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError as exc:
        raise ToolError(f"Invalid date '{s}': use YYYY-MM-DD format.") from exc


def _build_urls(a: FlightSearchArgs) -> dict:
    dep = _parse_date(a.date)
    if dep < datetime.now():
        raise ToolError("Departure date is in the past.")
    ret = _parse_date(a.return_date) if a.return_date else None
    if ret and ret < dep:
        raise ToolError("Return date is before departure date.")

    origin = a.origin.strip()
    destination = a.destination.strip()
    dep_fmt = dep.strftime("%B %d, %Y")

    query = f"Flights from {origin} to {destination} on {dep_fmt}"
    if ret:
        query += f" returning {ret.strftime('%B %d, %Y')}"

    google_url = f"https://www.google.com/travel/flights?q={quote_plus(query)}"

    dep_sky = dep.strftime("%y%m%d")
    skyscanner_url = f"https://www.skyscanner.com/transport/flights/{quote_plus(origin.lower())}/{quote_plus(destination.lower())}/{dep_sky}/"
    if ret:
        skyscanner_url += f"{ret.strftime('%y%m%d')}/"

    result: dict = {
        "trip": "round_trip" if ret else "one_way",
        "origin": origin,
        "destination": destination,
        "departure": a.date,
        "google_flights": google_url,
        "skyscanner": skyscanner_url,
    }
    if a.return_date:
        result["return"] = a.return_date
    return result


@tool("flight_search", "Search for flights between two cities. Returns Google Flights and Skyscanner links with trip details.",
      FlightSearchArgs, Tier.T0,
      summary=lambda a: f"Search flights {a.origin} → {a.destination} on {a.date}")
async def flight_search(a: FlightSearchArgs) -> dict:
    return await asyncio.to_thread(_build_urls, a)


class FlightOpenArgs(Args):
    url: str = Field(..., min_length=10, max_length=2000, description="Flight search URL to open in browser")


@tool("flight_open", "Open a flight search URL in the default browser.",
      FlightOpenArgs, Tier.T1, sensitive=True,
      summary=lambda a: "Open flight search in browser")
async def flight_open(a: FlightOpenArgs) -> dict:
    if not a.url.startswith(("https://www.google.com/travel/flights", "https://www.skyscanner.com/")):
        raise ToolError("Only Google Flights and Skyscanner URLs are allowed.")
    await asyncio.to_thread(webbrowser.open, a.url)
    return {"opened": a.url}
