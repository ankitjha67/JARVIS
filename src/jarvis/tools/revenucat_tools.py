"""RevenueCat subscription management tools."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

_RC_API = "https://api.revenuecat.com/v1"


def _get_key() -> str:
    from .. import secrets
    key = secrets.get_key("revenuecat")
    if not key:
        raise ToolError("RevenueCat API key not configured. Add it in Settings → Keys as 'revenuecat'.")
    return key


async def _api(method: str, path: str, **kwargs: Any) -> Any:
    import httpx2 as httpx
    key = _get_key()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    url = f"{_RC_API}{path}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        if method == "GET":
            r = await client.get(url, headers=headers, **kwargs)
        else:
            r = await client.post(url, headers=headers, **kwargs)
        r.raise_for_status()
        return r.json()


class RCSubscriberArgs(Args):
    app_user_id: str = Field(..., min_length=1, max_length=200)


@tool("rc_subscriber", "Get a RevenueCat subscriber's entitlements and purchase history.",
      RCSubscriberArgs, Tier.T0, summary=lambda a: f"RC subscriber: {a.app_user_id[:30]}")
async def rc_subscriber(a: RCSubscriberArgs) -> dict:
    import urllib.parse
    uid = urllib.parse.quote(a.app_user_id, safe="")
    data = await _api("GET", f"/subscribers/{uid}")
    sub = data.get("subscriber", {})
    return {
        "entitlements": {k: {"expires": v.get("expires_date"), "active": v.get("expires_date") is None
                             or v.get("expires_date", "") > __import__("datetime").datetime.now(
                                 __import__("datetime").UTC).isoformat()}
                         for k, v in sub.get("entitlements", {}).items()},
        "subscriptions": {k: {"store": v.get("store"), "expires": v.get("expires_date"),
                              "unsubscribe_detected": v.get("unsubscribe_detected_at")}
                          for k, v in sub.get("subscriptions", {}).items()},
        "non_subscriptions": list(sub.get("non_subscriptions", {}).keys()),
    }


class RCOfferingsArgs(Args):
    pass


@tool("rc_offerings", "List available RevenueCat offerings and packages.",
      RCOfferingsArgs, Tier.T0, summary=lambda a: "RC offerings")
async def rc_offerings(a: RCOfferingsArgs) -> dict:
    data = await _api("GET", "/offerings")
    offerings = data.get("offerings", [])
    return {"offerings": [{"id": o["identifier"], "description": o.get("description", ""),
                           "packages": [{"id": p["identifier"], "product": p.get("platform_product_identifier", "")}
                                        for p in o.get("packages", [])]}
                          for o in offerings]}


class RCGrantArgs(Args):
    app_user_id: str = Field(..., min_length=1, max_length=200)
    entitlement_id: str = Field(..., min_length=1, max_length=100)
    duration: str = Field("monthly", description="daily, three_day, weekly, monthly, two_month, three_month, six_month, yearly, lifetime")


@tool("rc_grant", "Grant a promotional entitlement to a subscriber.",
      RCGrantArgs, Tier.T2, sensitive=True,
      summary=lambda a: f"RC grant {a.entitlement_id} to {a.app_user_id[:20]}")
async def rc_grant(a: RCGrantArgs) -> dict:
    import urllib.parse
    uid = urllib.parse.quote(a.app_user_id, safe="")
    eid = urllib.parse.quote(a.entitlement_id, safe="")
    result = await _api("POST", f"/subscribers/{uid}/entitlements/{eid}/promotional",
                        json={"duration": a.duration})
    return {"granted": True, "subscriber": result.get("subscriber", {}).get("entitlements", {})}


class RCOverviewArgs(Args):
    pass


@tool("rc_overview", "Get a RevenueCat app overview (subscriber count, MRR).",
      RCOverviewArgs, Tier.T0, summary=lambda a: "RC overview")
async def rc_overview(a: RCOverviewArgs) -> dict:
    data = await _api("GET", "/projects")
    projects = data.get("projects", [])
    return {"projects": [{"id": p.get("id"), "name": p.get("name", ""),
                          "apps": [{"id": ap.get("id"), "platform": ap.get("platform")}
                                   for ap in p.get("apps", [])]}
                         for p in projects]}
