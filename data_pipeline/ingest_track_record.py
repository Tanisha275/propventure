"""
data_pipeline/ingest_track_record.py

Loads builder_track_record rows (complaint_count, cases_count,
possession_delay_months) produced by convert_kaggle_rera.py into Supabase.

Must be run AFTER rera_ingest.py for the same projects — it looks up each
project's UUID by rera_certificate_no, so the project row needs to exist
already.
"""

from __future__ import annotations

import os
import sys

import pandas as pd
from dotenv import load_dotenv

load_dotenv()


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


def upsert_track_record(client, df: pd.DataFrame, dry_run: bool = False) -> dict:
    inserted, updated, skipped, not_found = 0, 0, 0, 0

    for _, row in df.iterrows():
        cert_no = str(row["rera_certificate_no"]).strip()
        try:
            project = (
                client.table("projects")
                .select("id, builder_id")
                .eq("rera_certificate_no", cert_no)
                .execute()
            )
            if not project.data:
                # Expected for any project not yet ingested via rera_ingest.py
                # (e.g. if you only ingested a subset). Not an error.
                not_found += 1
                continue

            project_id = project.data[0]["id"]
            builder_id = project.data[0]["builder_id"]

            payload = {
                "builder_id": builder_id,
                "project_id": project_id,
                "possession_delay_months": int(row["possession_delay_months"]),
                "complaint_count": int(row["complaint_count"]) + int(row["cases_count"]),
                "complaint_summary": None,  # reserved for future qualitative summaries
            }

            if dry_run:
                inserted += 1
                continue

            existing = (
                client.table("builder_track_record")
                .select("id")
                .eq("project_id", project_id)
                .execute()
            )
            if existing.data:
                client.table("builder_track_record").update(payload).eq(
                    "id", existing.data[0]["id"]
                ).execute()
                updated += 1
            else:
                client.table("builder_track_record").insert(payload).execute()
                inserted += 1

        except Exception as e:  # noqa: BLE001
            print(f"[ingest_track_record] skipped row ({cert_no}): {e}")
            skipped += 1

    return {"inserted": inserted, "updated": updated, "skipped": skipped, "project_not_found": not_found}


def main(csv_path: str, dry_run: bool = False):
    df = load_from_csv(csv_path)
    print(f"[ingest_track_record] {len(df)} rows loaded from {csv_path}")

    if dry_run:
        print(f"[ingest_track_record] --dry-run: would attempt {len(df)} upserts (no Supabase write)")
        return

    client = get_client()
    summary = upsert_track_record(client, df)
    print(f"[ingest_track_record] done: {summary}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ingest_track_record.py <path_to_csv> [--dry-run]")
        sys.exit(1)
    main(sys.argv[1], dry_run="--dry-run" in sys.argv)
