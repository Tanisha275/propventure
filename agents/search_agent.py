"""
agents/search_agent.py

Matches a buyer's search criteria against stored projects. Deliberately
NOT LLM-driven for the actual matching — see the earlier project discussion:
budget/location/BHK matching is a structured filter query, and an LLM adds
cost, latency, and unpredictability to a job a plain query already does
reliably. The only place an LLM is useful here is parsing loose free-text
("2bhk mira road under 1cr") into structured filters — that piece is
optional and isolated in parse_free_text_query().

Ranking rule (core USP, see PRD.md section 4): results are sorted by
budget fit and credibility only. Nothing here ever sorts by anything
resembling paid placement, and there is no code path that could.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class SearchFilters:
    locality: Optional[str] = None
    bhk_config: Optional[str] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None


@dataclass
class SearchResult:
    project_id: str
    project_name: str
    builder_name: str
    locality: str
    bhk_config: Optional[str]
    price_inr: Optional[float]
    carpet_area_sqft: Optional[float]
    construction_stage: Optional[str]
    budget_fit_score: float  # 0-1, 1 = exactly at budget_max, penalized further away


def get_client():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and a Supabase key must be set in .env")
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Optional: loose free-text -> structured filters (LLM-assisted)
# ---------------------------------------------------------------------------

_BHK_PATTERN = re.compile(r"(\d)\s*bhk", re.IGNORECASE)
_BUDGET_CR_PATTERN = re.compile(r"(\d+(\.\d+)?)\s*(cr|crore)", re.IGNORECASE)
_BUDGET_L_PATTERN = re.compile(r"(\d+(\.\d+)?)\s*(l|lakh|lac)", re.IGNORECASE)


def parse_free_text_query(text: str) -> SearchFilters:
    """Lightweight regex-based parser for common query shapes
    ("2bhk mira road under 1cr", "budget 85 lakh 2 bhk").

    Deliberately NOT an LLM call — this pattern space is small and a plain
    regex parser is faster, free, and fully deterministic. If query
    complexity grows significantly (natural conversational input, multiple
    constraints phrased ambiguously), swap this for an LLM-assisted parser
    that still outputs the same SearchFilters shape — the matching logic
    below doesn't change either way.
    """
    filters = SearchFilters()

    bhk_match = _BHK_PATTERN.search(text)
    if bhk_match:
        filters.bhk_config = f"{bhk_match.group(1)}BHK"

    cr_match = _BUDGET_CR_PATTERN.search(text)
    if cr_match:
        filters.budget_max = float(cr_match.group(1)) * 1_00_00_000  # 1 crore = 1,00,00,000

    l_match = _BUDGET_L_PATTERN.search(text)
    if l_match and not cr_match:
        filters.budget_max = float(l_match.group(1)) * 1_00_000  # 1 lakh = 1,00,000

    # Locality: naive — look for "mira road" specifically for now. A real
    # implementation would match against a known locality list from the DB.
    if "mira road" in text.lower():
        filters.locality = "Mira Road"

    return filters


# ---------------------------------------------------------------------------
# Core matching (deterministic)
# ---------------------------------------------------------------------------

def _budget_fit_score(price: Optional[float], budget_max: Optional[float]) -> float:
    """1.0 = at or comfortably under budget, decaying as price exceeds it.
    Projects with unknown price get a neutral-low score (0.3) rather than
    being excluded outright — missing price data shouldn't silently hide
    a project from a buyer, it should just rank lower than confirmed fits.
    """
    if price is None:
        return 0.3
    if budget_max is None:
        return 1.0
    if price <= budget_max:
        return 1.0
    overage_pct = (price - budget_max) / budget_max
    return max(0.0, 1.0 - overage_pct * 2)  # drops fast once over budget


def search(filters: SearchFilters, limit: int = 50) -> list[SearchResult]:
    """Query Supabase directly with structured filters, then rank by
    budget fit only (see module docstring — never by payment/placement).
    """
    client = get_client()
    query = client.table("projects").select(
        "id, project_name, bhk_config, price_inr, carpet_area_sqft, "
        "construction_stage, locality, builders(name)"
    )

    if filters.locality:
        query = query.ilike("locality", f"%{filters.locality}%")
    if filters.bhk_config:
        query = query.eq("bhk_config", filters.bhk_config)
    if filters.budget_min:
        query = query.gte("price_inr", filters.budget_min)

    rows = query.limit(limit * 3).execute().data  # over-fetch, then rank+trim locally

    results = []
    for r in rows:
        price = r.get("price_inr")
        results.append(SearchResult(
            project_id=r["id"],
            project_name=r["project_name"],
            builder_name=(r.get("builders") or {}).get("name", "Unknown"),
            locality=r["locality"],
            bhk_config=r.get("bhk_config"),
            price_inr=price,
            carpet_area_sqft=r.get("carpet_area_sqft"),
            construction_stage=r.get("construction_stage"),
            budget_fit_score=_budget_fit_score(price, filters.budget_max),
        ))

    results.sort(key=lambda r: r.budget_fit_score, reverse=True)
    return results[:limit]


if __name__ == "__main__":
    import sys

    query_text = " ".join(sys.argv[1:]) or "2bhk mira road under 1cr"
    filters = parse_free_text_query(query_text)
    print(f"[search_agent] parsed filters: {filters}")

    results = search(filters)
    print(f"[search_agent] {len(results)} results\n")
    for r in results[:10]:
        price_display = f"₹{r.price_inr:,.0f}" if r.price_inr else "price unknown"
        print(f"  fit={r.budget_fit_score:.2f}  {r.project_name} ({r.builder_name}) — {price_display}")
