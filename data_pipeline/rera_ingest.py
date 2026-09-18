"""
data_pipeline/rera_ingest.py

Ingests MahaRERA project data into Supabase.

IMPORTANT — compliance note:
maharera.maharashtra.gov.in disallows automated crawling via robots.txt.
This module therefore does NOT scrape the site. It expects data to be
manually exported (browser search -> copy/export project details) into
a CSV following the schema below, then loads that CSV into Supabase.

PERFORMANCE NOTE (added after a real Supabase disk-IO warning): the
original version of this script did up to 4 separate round trips PER ROW
(select builder, insert/update builder, select project, insert/update
project) — for 624+ rows that's thousands of individual transactions, each
with its own disk write overhead. This version batches builder creation
and project upserts into chunked bulk operations instead, which cuts both
the number of round trips and the actual disk IO substantially, since a
single multi-row upsert is one transaction instead of N.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

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

CHUNK_SIZE = 500  # rows per batch request — keeps individual payloads reasonable


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


def get_client() -> "Client":
    from supabase import create_client, Client  # imported lazily so dry-run
    # mode (see main()) never requires the supabase package to be working.

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
    """Load a manually-exported MahaRERA CSV and validate its shape."""
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


def _clean(value):
    """Convert pandas NaN (a float) into a proper JSON-safe None. NaN is
    not valid JSON, so passing it straight through to Supabase's client
    causes a serialization error — this was a real bug hit while ingesting
    the bulk Kaggle-sourced rows (all missing price/carpet_area).
    """
    if isinstance(value, (list, dict)):
        return value
    if pd.isna(value):
        return None
    return value


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _resolve_builders_batch(client, builder_names: set[str]) -> dict[str, str]:
    """Resolves every distinct builder name to an id in as few round trips
    as possible: one query to fetch all builders that already exist, one
    batch insert for whatever's missing (chunked), instead of a
    select+insert pair per row.

    Naive exact-name match for now — real builder entity resolution
    (matching SPVs / group companies to a parent builder) is a known hard
    problem, see PRD.md.
    """
    name_to_id: dict[str, str] = {}

    # Supabase's .in_() filter has practical limits on very large lists —
    # chunk the lookup too, not just the insert.
    for chunk in _chunked(list(builder_names), CHUNK_SIZE):
        existing = client.table("builders").select("id, name").in_("name", chunk).execute()
        for row in existing.data:
            name_to_id[row["name"]] = row["id"]

    missing = [name for name in builder_names if name not in name_to_id]
    print(f"[rera_ingest] {len(name_to_id)} builders already exist, {len(missing)} new to create")

    for chunk in _chunked(missing, CHUNK_SIZE):
        created = client.table("builders").insert([{"name": name} for name in chunk]).execute()
        for row in created.data:
            name_to_id[row["name"]] = row["id"]

    return name_to_id


def upsert_projects(client, df: pd.DataFrame) -> dict:
    """Batched upsert by rera_certificate_no. One pass to resolve builders,
    then chunked upsert calls for projects — replaces the old per-row
    select+insert/update loop.
    """
    builder_names = set(df["builder_name"].astype(str).str.strip())
    builder_map = _resolve_builders_batch(client, builder_names)

    payloads = []
    build_errors = 0
    for _, row in df.iterrows():
        builder_name = str(row["builder_name"]).strip()
        builder_id = builder_map.get(builder_name)
        if builder_id is None:
            build_errors += 1
            continue

        payloads.append({
            "builder_id": builder_id,
            "rera_certificate_no": str(row["rera_certificate_no"]).strip(),
            "project_name": str(row["project_name"]).strip(),
            "locality": str(row["locality"]).strip(),
            "district": _clean(row.get("district")),
            "bhk_config": _clean(row.get("bhk_config")),
            "carpet_area_sqft": _clean(row.get("carpet_area_sqft")),
            "price_inr": _clean(row.get("price_inr")),
            "construction_stage": _clean(row.get("construction_stage")),
            "possession_date": _clean(row.get("possession_date")) or None,
            "rera_registration_date": _clean(row.get("rera_registration_date")) or None,
            "source": "manual_rera_export",
        })

    upserted, failed_chunks = 0, 0
    for chunk in _chunked(payloads, CHUNK_SIZE):
        try:
            client.table("projects").upsert(chunk, on_conflict="rera_certificate_no").execute()
            upserted += len(chunk)
        except Exception as e:  # noqa: BLE001 — log and continue to the next chunk
            print(f"[rera_ingest] a chunk of {len(chunk)} rows failed: {e}")
            failed_chunks += 1

    return {
        "upserted": upserted,
        "builder_resolution_errors": build_errors,
        "failed_chunks": failed_chunks,
        "total_rows": len(df),
    }


def main(csv_path: str, dry_run: bool = False):
    print(f"[rera_ingest] loading {csv_path}")
    df = load_from_csv(csv_path)
    print(f"[rera_ingest] {len(df)} valid rows after cleaning")

    if dry_run:
        print("[rera_ingest] --dry-run: skipping Supabase write, showing summary only")
        print(f"[rera_ingest] distinct builders: {df['builder_name'].nunique()}")
        print(df.groupby("builder_name").size().sort_values(ascending=False).to_string())
        return

    client = get_client()
    summary = upsert_projects(client, df)
    print(f"[rera_ingest] done: {summary}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python rera_ingest.py <path_to_csv> [--dry-run]")
        sys.exit(1)
    dry_run = "--dry-run" in sys.argv
    main(sys.argv[1], dry_run=dry_run)
