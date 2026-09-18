"""
agents/price_model_agent.py

Thin orchestration wrapper around models.price_deviation_model. Unlike the
credibility agent, this one is NOT LLM-based — the price-deviation model is
already a trained statistical model (hierarchical shrinkage + quantile
regression), and its reasoning text is template-generated, not free-form
LLM output. There's nothing here for a prompt injection to target, which is
by design (see PRD.md: "resist the urge to make everything AI-powered").

This module's real job is enforcing the second gate: even though the model
itself already requires a real numeric deviation before flagging (see
DeviationResult / PriceDeviationModel._build_reasoning), every flag is
still passed through security.validate_output.validate_price_deviation_claim
before being considered final. This is defense in depth — if the model's
internal threshold logic ever has a bug, this second check is an
independent gate that only looks at the raw numbers, not the model's own
flagged/not-flagged decision.
"""

from __future__ import annotations

import os
import sys

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.price_deviation_model import PriceDeviationModel, DeviationResult
from security.validate_output import validate_price_deviation_claim

load_dotenv()


def get_client():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    return create_client(url, key)


def _fetch_priced_projects() -> pd.DataFrame:
    """Only projects with both price_inr and carpet_area_sqft populated are
    usable — see the enrich_price_data.py compliance/coverage notes for why
    most of the 624 Mira Road rows don't qualify yet.
    """
    client = get_client()
    rows = client.table("projects").select(
        "id, project_name, price_inr, carpet_area_sqft, bhk_config, "
        "construction_stage, possession_date, amenities, builder_id"
    ).not_.is_("price_inr", "null").not_.is_("carpet_area_sqft", "null").execute().data
    return pd.DataFrame(rows)


def run_price_deviation_check(min_rows_warning: int = 30) -> list[DeviationResult]:
    """Fits the model against every priced project and returns only the
    flags that pass BOTH the model's own confidence/threshold logic AND
    the independent validate_price_deviation_claim() numeric check.
    """
    df = _fetch_priced_projects()
    print(f"[price_model_agent] {len(df)} projects have usable price+area data")

    if len(df) < min_rows_warning:
        print(
            f"[price_model_agent] WARNING: fewer than {min_rows_warning} priced rows — "
            "the model will run in fallback mode (simple price/sqft quantiles) and any "
            "flags produced should be treated as low-confidence. See PriceDeviationModel "
            "docstring for the fallback behavior."
        )

    if df.empty:
        return []

    model = PriceDeviationModel().fit(df)
    results = model.predict(df)

    validated_flags = []
    for r in results:
        if not r.flagged:
            continue

        check = validate_price_deviation_claim(
            reasoning_text=r.reasoning,
            actual_price=r.actual_price,
            expected_price_mid=r.builder_adjusted_mid,
        )
        if check.approved:
            validated_flags.append(r)
        else:
            print(
                f"[price_model_agent] flag REJECTED by independent validation for "
                f"project {r.project_id}: {check.notes}"
            )

    print(f"[price_model_agent] {len(validated_flags)} flags passed both gates (of {len(results)} projects checked)")
    return validated_flags


if __name__ == "__main__":
    flags = run_price_deviation_check()
    for f in flags:
        print(f"\n🚩 project_id={f.project_id}")
        print(f"   actual={f.actual_price:.0f}  expected_mid={f.builder_adjusted_mid:.0f}  "
              f"deviation={f.deviation_pct:+.1%}  confidence={f.confidence:.2f}")
        print(f"   {f.reasoning}")
