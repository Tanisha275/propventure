"""
data_pipeline/rera_ingest.py

Ingests MahaRERA project data into Supabase.

IMPORTANT — compliance note:
maharera.maharashtra.gov.in disallows automated crawling via robots.txt.
This module therefore does NOT scrape the site. It expects data to be
manually exported (browser search -> copy/export project details) into
a CSV following the schema below, then loads that CSV into Supabase.

If MahaRERA ever offers an official API or a data-sharing agreement,
swap out `load_from_csv` for a proper API client — the rest of the
pipeline (validation, upsert) stays the same.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

REQUIRED_COLUMNS = [
    "rera_certificate_no",
    "project_name",
    "builder_name",
    "locality",
    "district",
    "bhk_config",
    "carpet_area_sqft",
    "price_inr",
    "construction_stage",
    "possession_date",
    "rera_registration_date",
]


@dataclass
class RawProjectRow:
    rera_certificate_no: str
    project_name: str
    builder_name: str
    locality: str
    district: Optional[str]
    bhk_config: Optional[str]
    carpet_area_sqft: Optional[float]
    price_inr: Optional[float]
    construction_stage: Optional[str]
    possession_date: Optional[str]
    rera_registration_date: Optional[str]


def get_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env. "
            "Use the service role key here (not anon) since ingestion writes "
            "to public tables that anon cannot write to under RLS."
        )
    return create_client(url, key)


def load_from_csv(path: str) -> pd.DataFrame:
    """Load a manually-exported MahaRERA CSV and validate its shape.

    Expected columns: see REQUIRED_COLUMNS. Rows missing a
    rera_certificate_no or project_name are dropped, since those are the
    fields we rely on for entity matching and dedup.
    """
    df = pd.read_csv(path)

    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    before = len(df)
    df = df.dropna(subset=["rera_certificate_no", "project_name", "builder_name"])
    dropped = before - len(df)
    if dropped:
        print(f"[rera_ingest] dropped {dropped} rows missing required identifiers")

    df["carpet_area_sqft"] = pd.to_numeric(df["carpet_area_sqft"], errors="coerce")
    df["price_inr"] = pd.to_numeric(df["price_inr"], errors="coerce")

    return df


def _get_or_create_builder(client: Client, builder_name: str) -> str:
    """Naive exact-name match for now.

    Real builder entity resolution (matching SPVs / group companies to a
    parent builder) is a known hard problem — see PRD.md section on the
    price-deviation model. This function is the seam where a proper
    fuzzy-matching / manual-mapping step should be inserted later.
    """
    existing = (
        client.table("builders")
        .select("id")
        .eq("name", builder_name)
        .execute()
    )
    if existing.data:
        return existing.data[0]["id"]

    created = client.table("builders").insert({"name": builder_name}).execute()
    return created.data[0]["id"]


def upsert_projects(client: Client, df: pd.DataFrame) -> dict:
    """Upsert projects by rera_certificate_no. Returns a summary dict."""
    inserted, updated, skipped = 0, 0, 0

    for _, row in df.iterrows():
        try:
            builder_id = _get_or_create_builder(client, str(row["builder_name"]).strip())

            payload = {
                "builder_id": builder_id,
                "rera_certificate_no": str(row["rera_certificate_no"]).strip(),
                "project_name": str(row["project_name"]).strip(),
                "locality": str(row["locality"]).strip(),
                "district": row.get("district"),
                "bhk_config": row.get("bhk_config"),
                "carpet_area_sqft": row.get("carpet_area_sqft"),
                "price_inr": row.get("price_inr"),
                "construction_stage": row.get("construction_stage"),
                "possession_date": row.get("possession_date") or None,
                "rera_registration_date": row.get("rera_registration_date") or None,
                "source": "manual_rera_export",
            }

            existing = (
                client.table("projects")
                .select("id")
                .eq("rera_certificate_no", payload["rera_certificate_no"])
                .execute()
            )

            if existing.data:
                client.table("projects").update(payload).eq(
                    "id", existing.data[0]["id"]
                ).execute()
                updated += 1
            else:
                client.table("projects").insert(payload).execute()
                inserted += 1

        except Exception as e:  # noqa: BLE001 — log and continue, one bad row shouldn't kill the batch
            print(f"[rera_ingest] skipped row ({row.get('rera_certificate_no')}): {e}")
            skipped += 1

    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def main(csv_path: str):
    print(f"[rera_ingest] loading {csv_path}")
    df = load_from_csv(csv_path)
    print(f"[rera_ingest] {len(df)} valid rows after cleaning")

    client = get_client()
    summary = upsert_projects(client, df)
    print(f"[rera_ingest] done: {summary}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python rera_ingest.py <path_to_csv>")
        sys.exit(1)
    main(sys.argv[1])