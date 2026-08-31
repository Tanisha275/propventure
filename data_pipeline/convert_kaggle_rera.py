"""
data_pipeline/convert_kaggle_rera.py

Converts the bulk MahaRERA dataset (compiled and shared on Kaggle by
Jalaj Jha: kaggle.com/datasets/jhajalaj/rera-dataset-from-maharashtra-maharera)
into two CSVs matching PropVenture's schema:

  1. <output_prefix>_projects.csv       -> feeds rera_ingest.py
  2. <output_prefix>_track_record.csv   -> feeds ingest_track_record.py

Why this exists (see conversation / PRD.md compliance note): MahaRERA's own
site disallows automated crawling. This Kaggle dataset is a pre-compiled,
openly shared bulk export, so using it means PropVenture's pipeline never
touches maharera.maharashtra.gov.in programmatically at all.

Known gap: this bulk dataset does NOT include per-unit price, carpet area,
or BHK configuration (MahaRERA's registry doesn't require developers to
disclose per-unit pricing). Those columns are intentionally left blank in
the output — they need a secondary source (manual collection or a listing
site, see data_pipeline/listing_scraper.py, still to be built) before the
price-deviation model can use these rows. The credibility/track-record side
(complaints, cases, delays) IS fully populated from this dataset, though.
"""

from __future__ import annotations

import sys

import pandas as pd

# Rough pin-code -> human locality label mapping for the Mira-Bhayandar area.
# Good enough for display purposes; not authoritative.
PIN_TO_LOCALITY = {
    401107: "Mira Road East",
    401105: "Mira Road East",
    401104: "Mira Road East",
    401106: "Mira Road East",
    401101: "Bhayandar East",
    401102: "Bhayandar East",
    401103: "Bhayandar East",
    400615: "Bhayandar West",
}

STATUS_TO_STAGE = {
    "new project": "under_construction",
    "on-going project": "under_construction",
    "completed project": "ready_to_move",
}


def compute_possession_delay_months(row: pd.Series) -> float:
    """Delay = gap between the originally proposed completion date and the
    latest revised/extended date on record. 0 if no revision exists (i.e.
    project is still tracking its original timeline, or hasn't been revised
    yet) — this is a conservative estimate, not a guarantee the project
    will hit even the revised date.
    """
    proposed = pd.to_datetime(row.get("proposed_date_of_completion"), errors="coerce")
    revised = pd.to_datetime(row.get("revised_proposed_date_of_completion"), errors="coerce")
    extended = pd.to_datetime(row.get("extended_date_of_completion"), errors="coerce")

    latest = max([d for d in [revised, extended] if pd.notna(d)], default=pd.NaT)

    if pd.isna(proposed) or pd.isna(latest):
        return 0.0

    delta_months = (latest.year - proposed.year) * 12 + (latest.month - proposed.month)
    return float(max(0, delta_months))


def convert(input_csv: str, output_prefix: str) -> None:
    df = pd.read_csv(input_csv)
    print(f"[convert_kaggle_rera] loaded {len(df)} rows from {input_csv}")

    projects = pd.DataFrame({
        "rera_certificate_no": df["rera_id"],
        "project_name": df["project_name"],
        "builder_name": df["promoter_name"],
        "locality": df["location_pin_code"].map(PIN_TO_LOCALITY).fillna(df["location_pin_code"].astype(str)),
        "district": df["district"],
        "bhk_config": "",       # not available in this dataset — needs secondary source
        "carpet_area_sqft": "", # not available in this dataset — needs secondary source
        "price_inr": "",        # not available in this dataset — needs secondary source
        "construction_stage": df["project_status"].str.lower().map(STATUS_TO_STAGE).fillna("under_construction"),
        "possession_date": pd.to_datetime(
            df["extended_date_of_completion"].fillna(
                df["revised_proposed_date_of_completion"].fillna(df["proposed_date_of_completion"])
            ), errors="coerce"
        ).dt.strftime("%Y-%m-%d"),
        "rera_registration_date": "",  # not present in this dataset
    })

    track_record = pd.DataFrame({
        "rera_certificate_no": df["rera_id"],
        "builder_name": df["promoter_name"],
        "complaint_count": df["complaints_count"].fillna(0).astype(int),
        "cases_count": df["cases_count"].fillna(0).astype(int),
        "possession_delay_months": df.apply(compute_possession_delay_months, axis=1).astype(int),
    })

    projects_path = f"{output_prefix}_projects.csv"
    track_record_path = f"{output_prefix}_track_record.csv"
    projects.to_csv(projects_path, index=False)
    track_record.to_csv(track_record_path, index=False)

    print(f"[convert_kaggle_rera] wrote {len(projects)} rows -> {projects_path}")
    print(f"[convert_kaggle_rera] wrote {len(track_record)} rows -> {track_record_path}")
    print(
        "[convert_kaggle_rera] NOTE: bhk_config, carpet_area_sqft, price_inr are "
        "blank — this dataset doesn't include per-unit pricing. These rows can "
        "still be ingested (builder/credibility data will populate), but won't "
        "be usable by the price-deviation model until filled in from a "
        "secondary source."
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python convert_kaggle_rera.py <input_csv> <output_prefix>")
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2])
