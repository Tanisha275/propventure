"""
data_pipeline/ingest_track_record.py

Loads builder_track_record rows (complaint_count, cases_count,
possession_delay_months) produced by convert_kaggle_rera.py into Supabase.

Must be run AFTER rera_ingest.py for the same projects — it looks up each
project's UUID by rera_certificate_no.

PERFORMANCE NOTE (same fix as rera_ingest.py): originally did one select +
one select/insert/update PER ROW. This version fetches all projects once
and batch-upserts track records in chunks — requires a unique constraint
on builder_track_record.project_id (see the ALTER TABLE statement in the
project's setup notes) so the upsert's on_conflict works correctly.
"""

from __future__ import annotations

import os
import sys

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

CHUNK_SIZE = 500


def get_client():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    return create_client(url, key)


def load_from_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = ["rera_certificate_no", "builder_name", "complaint_count", "cases_count", "possession_delay_months"]
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")
    return df.dropna(subset=["rera_certificate_no"])


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _fetch_project_lookup(client, cert_numbers: list[str]) -> dict[str, dict]:
    """One batched fetch (chunked) instead of one select per row."""
    lookup: dict[str, dict] = {}
    for chunk in _chunked(cert_numbers, CHUNK_SIZE):
        rows = (
            client.table("projects")
            .select("id, builder_id, rera_certificate_no")
            .in_("rera_certificate_no", chunk)
            .execute()
        )
        for row in rows.data:
            lookup[row["rera_certificate_no"]] = {"id": row["id"], "builder_id": row["builder_id"]}
    return lookup


def upsert_track_record(client, df: pd.DataFrame, dry_run: bool = False) -> dict:
    cert_numbers = [str(c).strip() for c in df["rera_certificate_no"]]
    project_lookup = _fetch_project_lookup(client, cert_numbers)

    payloads = []
    not_found = 0
    for _, row in df.iterrows():
        cert_no = str(row["rera_certificate_no"]).strip()
        project = project_lookup.get(cert_no)
        if project is None:
            not_found += 1
            continue

        payloads.append({
            "builder_id": project["builder_id"],
            "project_id": project["id"],
            "possession_delay_months": int(row["possession_delay_months"]),
            "complaint_count": int(row["complaint_count"]) + int(row["cases_count"]),
            "complaint_summary": None,
        })

    print(f"[ingest_track_record] {len(payloads)} matched to existing projects, {not_found} not found")

    if dry_run:
        return {"would_upsert": len(payloads), "project_not_found": not_found}

    upserted, failed_chunks = 0, 0
    for chunk in _chunked(payloads, CHUNK_SIZE):
        try:
            client.table("builder_track_record").upsert(chunk, on_conflict="project_id").execute()
            upserted += len(chunk)
        except Exception as e:  # noqa: BLE001
            print(f"[ingest_track_record] a chunk of {len(chunk)} rows failed: {e}")
            print(
                "[ingest_track_record] if this mentions a missing constraint, run: "
                "alter table builder_track_record add constraint "
                "builder_track_record_project_id_key unique (project_id);  "
                "in the Supabase SQL editor first."
            )
            failed_chunks += 1

    return {"upserted": upserted, "project_not_found": not_found, "failed_chunks": failed_chunks}


def main(csv_path: str, dry_run: bool = False):
    df = load_from_csv(csv_path)
    print(f"[ingest_track_record] {len(df)} rows loaded from {csv_path}")

    client = get_client()
    summary = upsert_track_record(client, df, dry_run=dry_run)
    print(f"[ingest_track_record] done: {summary}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ingest_track_record.py <path_to_csv> [--dry-run]")
        sys.exit(1)
    main(sys.argv[1], dry_run="--dry-run" in sys.argv)
