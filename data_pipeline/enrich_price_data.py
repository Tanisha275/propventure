"""
data_pipeline/enrich_price_data.py

Fills in price_inr, carpet_area_sqft, and bhk_config on EXISTING project rows
(already ingested via rera_ingest.py) using a manually-compiled price
snapshot CSV — see price_snapshot_batch1.csv for the format and provenance
notes.

Why this exists: the bulk MahaRERA dataset (convert_kaggle_rera.py) has no
per-unit pricing at all — that data was gathered by reading public listing
pages via web search (reading indexed search snippets, not automated
scraping of the site itself — see project conversation for the compliance
reasoning). This script attaches that price data to the correct existing
project row rather than creating duplicates.

Matching strategy, in priority order:
  1. Exact match on rera_certificate_no, when the snapshot has one — this
     is unambiguous and preferred whenever available. ONLY this method
     writes to the database automatically.
  2. Fuzzy match on (builder_name, project_name) when no RERA number is
     given — uses difflib, and is NEVER auto-applied. A real false-positive
     was caught during testing ("RNA NG Paradise" fuzzy-matched to
     "RNA NG Bliss" at 0.77 similarity, purely from a shared builder-name
     prefix pattern this developer uses across many projects) — so every
     fuzzy candidate is written to a *_needs_review.csv file for manual
     confirmation instead of being trusted automatically.

Area-type honesty: the snapshot's `area_type` column (carpet / saleable /
built_up) is preserved in the match report. Only 'carpet' rows are applied
directly to carpet_area_sqft by default — saleable/built-up rows require
an explicit --allow-non-carpet flag, since mixing area types silently
would corrupt price/sqft comparisons for the price-deviation model.
"""

from __future__ import annotations

import difflib
import os
import sys

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

FUZZY_MATCH_THRESHOLD = 0.72  # informational only now — fuzzy matches are never auto-applied regardless of score


def get_client():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    return create_client(url, key)


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def find_match(client, row: pd.Series, all_projects: list[dict]):
    """Returns (matched_project_or_None, method_description)."""
    cert_no = str(row.get("rera_certificate_no", "")).strip()

    if cert_no and cert_no.lower() != "nan":
        exact = [p for p in all_projects if p["rera_certificate_no"] == cert_no]
        if exact:
            return exact[0], "exact_rera_match"
        return None, "rera_no_given_but_not_found_in_db"

    best, best_score = None, 0.0
    for p in all_projects:
        score = (
            _similarity(row["builder_name"], p["builder_name"]) * 0.4
            + _similarity(row["project_name"], p["project_name"]) * 0.6
        )
        if score > best_score:
            best, best_score = p, score

    if best and best_score >= FUZZY_MATCH_THRESHOLD:
        return best, f"fuzzy_match (score={best_score:.2f})"
    return None, f"no_confident_match (best_score={best_score:.2f})"


def enrich(csv_path: str, dry_run: bool = False, allow_non_carpet: bool = False):
    df = pd.read_csv(csv_path)
    print(f"[enrich_price_data] {len(df)} price rows loaded from {csv_path}")

    client = get_client()
    # projects.builder_name doesn't exist as a column — builder identity is
    # stored via builder_id (FK to builders.id), so we need to pull the
    # related builder name through Supabase's foreign-table embed syntax
    # rather than selecting it as a plain column.
    raw_projects = client.table("projects").select(
        "id, rera_certificate_no, project_name, builders(name)"
    ).execute().data
    all_projects = [
        {
            "id": p["id"],
            "rera_certificate_no": p["rera_certificate_no"],
            "project_name": p["project_name"],
            "builder_name": (p.get("builders") or {}).get("name", ""),
        }
        for p in raw_projects
    ]
    print(f"[enrich_price_data] matching against {len(all_projects)} existing projects")

    matched, skipped_area_type, unmatched, needs_review = 0, 0, 0, 0
    review_rows = []

    for _, row in df.iterrows():
        project, method = find_match(client, row, all_projects)

        if method != "exact_rera_match":
            if project is not None:
                print(
                    f"[enrich_price_data] NEEDS REVIEW (not auto-applied): "
                    f"{row['project_name']} ({row['builder_name']}) -> "
                    f"suggested: {project['project_name']} ({project['builder_name']}) [{method}]"
                )
                review_rows.append({
                    "snapshot_project": row["project_name"],
                    "snapshot_builder": row["builder_name"],
                    "suggested_match_project": project["project_name"],
                    "suggested_match_builder": project["builder_name"],
                    "suggested_match_id": project["id"],
                    "method": method,
                    "price_inr": row.get("price_inr"),
                    "area_sqft": row.get("area_sqft"),
                    "area_type": row.get("area_type"),
                    "bhk_config": row.get("bhk_config"),
                })
                needs_review += 1
            else:
                print(f"[enrich_price_data] UNMATCHED: {row['project_name']} ({row['builder_name']}) — {method}")
                unmatched += 1
            continue

        area_type = str(row.get("area_type", "")).strip().lower()
        if area_type != "carpet" and not allow_non_carpet:
            print(
                f"[enrich_price_data] area_type={area_type} (not carpet) for "
                f"{row['project_name']} — price_inr will still update, "
                f"carpet_area_sqft left blank (re-run with --allow-non-carpet to include it anyway)"
            )
            skipped_area_type += 1
            carpet_area = None
        else:
            carpet_area = row.get("area_sqft")

        payload = {
            "price_inr": row.get("price_inr"),
            "bhk_config": row.get("bhk_config"),
        }
        if carpet_area is not None:
            payload["carpet_area_sqft"] = carpet_area

        print(f"[enrich_price_data] MATCHED (exact_rera_match): {row['project_name']} -> {project['project_name']}")

        if not dry_run:
            client.table("projects").update(payload).eq("id", project["id"]).execute()

        matched += 1

    if review_rows:
        review_path = csv_path.replace(".csv", "_needs_review.csv")
        pd.DataFrame(review_rows).to_csv(review_path, index=False)
        print(f"\n[enrich_price_data] wrote {len(review_rows)} fuzzy-match candidates to {review_path}")
        print(
            "[enrich_price_data] review that file, delete any wrong suggestions, add the "
            "correct rera_certificate_no for confirmed ones, then re-run enrich_price_data.py "
            "on a corrected CSV — fuzzy matches are never applied automatically."
        )

    print(
        f"\n[enrich_price_data] done: auto-applied={matched} "
        f"(area_type_skipped={skipped_area_type}), needs_review={needs_review}, unmatched={unmatched}"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python enrich_price_data.py <price_snapshot_csv> [--dry-run] [--allow-non-carpet]")
        sys.exit(1)
    enrich(
        sys.argv[1],
        dry_run="--dry-run" in sys.argv,
        allow_non_carpet="--allow-non-carpet" in sys.argv,
    )
