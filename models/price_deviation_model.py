"""
models/price_deviation_model.py

The price-tier deviation model — PropVenture's core differentiator.

Problem framing (see PRD.md section 7):
    Builders have very few historical projects each (often 3-10), so a
    plain per-builder regression would overfit badly. This is a
    hierarchical / partial-pooling problem: predict expected price from a
    market-wide model, then adjust per-builder using an estimate that is
    "shrunk" toward zero when that builder has little history, and allowed
    to move more freely when they have a lot.

Pipeline:
    1. MarketPriceModel — LightGBM quantile regression (10th/50th/90th
       percentile) trained across ALL builders in a locality. Captures the
       general relationship between price and features (construction
       stage, carpet-to-saleable ratio, floor, amenities, possession
       timeline).
    2. BuilderShrinkage — empirical-Bayes-style adjustment: for each
       builder, compute how far their historical prices sit from what the
       market model predicts, then shrink that adjustment toward zero
       based on how many historical projects they have.
    3. PriceDeviationModel — combines both, decides whether a project's
       actual price falls meaningfully below its (builder-adjusted)
       expected range, and only flags it when confidence clears a
       threshold.

Every flag produced here MUST still pass through
security.validate_output.validate_price_deviation_claim before being
persisted — this module produces the statistical signal, it does not
have the final word on what gets written to the database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv

try:
    import lightgbm as lgb
except Exception:  # noqa: BLE001 — deliberately broad: covers missing package
    # (ImportError) AND installed-but-broken native library (OSError, e.g.
    # missing libomp on some macOS setups). Either way, fall back to the
    # simple quantile heuristic rather than crashing.
    lgb = None

try:
    import shap
except Exception:  # noqa: BLE001 — same reasoning as the lightgbm import above
    shap = None

from sklearn.model_selection import GroupKFold

load_dotenv()

# Below this many rows, LightGBM has nothing meaningful to learn from —
# fall back to a simple global-quantile estimate instead of a garbage model.
MIN_ROWS_FOR_MARKET_MODEL = 30

# Below this many historical projects, a builder's shrinkage adjustment
# is capped near zero regardless of how extreme their observed residual is.
SHRINKAGE_K = 5  # empirical Bayes pseudo-count; higher = more conservative

# A flag only fires when actual price is at least this far below the
# builder-adjusted expected range AND confidence clears CONFIDENCE_THRESHOLD.
DEVIATION_THRESHOLD = -0.10  # -10%
CONFIDENCE_THRESHOLD = 0.4

FEATURE_COLUMNS = [
    "carpet_area_sqft",
    "months_to_possession",
    "is_ready_to_move",
    "amenity_count",
    "floor_number",
]


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def build_features(df: pd.DataFrame, reference_date: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """Turn raw project rows into the model's feature set.

    Expects columns: carpet_area_sqft, price_inr, construction_stage,
    possession_date, amenities (list-like), floor_number (optional),
    builder_id, locality, rera_certificate_no.

    Missing optional fields degrade gracefully (filled with a neutral
    default) rather than raising — real listing data is messy and this
    pipeline needs to run against partial data.
    """
    out = df.copy()
    reference_date = reference_date or pd.Timestamp.now()

    out["possession_date"] = pd.to_datetime(out.get("possession_date"), errors="coerce")
    out["months_to_possession"] = (
        (out["possession_date"] - reference_date).dt.days / 30.44
    ).clip(lower=0).fillna(0)

    out["is_ready_to_move"] = (
        out.get("construction_stage", "").fillna("").str.lower() == "ready_to_move"
    ).astype(int)

    if "amenities" in out.columns:
        out["amenity_count"] = out["amenities"].apply(
            lambda a: len(a) if isinstance(a, (list, tuple)) else 0
        )
    else:
        out["amenity_count"] = 0

    if "floor_number" in out.columns:
        parsed = pd.to_numeric(out["floor_number"], errors="coerce")
        fill_value = parsed.median()
        if pd.isna(fill_value):
            fill_value = 5.0  # neutral default when the column exists but is entirely empty
        out["floor_number"] = parsed.fillna(fill_value)
    else:
        out["floor_number"] = 5.0  # neutral default when the column is absent entirely

    out["carpet_area_sqft"] = pd.to_numeric(out.get("carpet_area_sqft"), errors="coerce")
    out["price_inr"] = pd.to_numeric(out.get("price_inr"), errors="coerce")

    return out


# ---------------------------------------------------------------------------
# Market-wide quantile model
# ---------------------------------------------------------------------------

class MarketPriceModel:
    """LightGBM quantile regression predicting expected price at the
    10th / 50th / 90th percentile, from features alone (no builder identity).
    Falls back to simple empirical quantiles of price/sqft when there isn't
    enough data to fit a real model — this keeps the pipeline usable during
    early development without pretending a 5-row dataset can support a
    trained model.
    """

    QUANTILES = {"low": 0.1, "mid": 0.5, "high": 0.9}

    def __init__(self):
        self.models: dict[str, "lgb.LGBMRegressor"] = {}
        self.fallback_ppsf: Optional[dict[str, float]] = None
        self.is_fallback = False

    def fit(self, df: pd.DataFrame) -> "MarketPriceModel":
        data = df.dropna(subset=["price_inr", "carpet_area_sqft"] + [
            c for c in FEATURE_COLUMNS if c != "carpet_area_sqft"
        ])

        if len(data) < MIN_ROWS_FOR_MARKET_MODEL or lgb is None:
            self.is_fallback = True
            ppsf = data["price_inr"] / data["carpet_area_sqft"]
            self.fallback_ppsf = {
                "low": ppsf.quantile(0.1) if len(ppsf) else np.nan,
                "mid": ppsf.quantile(0.5) if len(ppsf) else np.nan,
                "high": ppsf.quantile(0.9) if len(ppsf) else np.nan,
            }
            print(
                f"[price_deviation_model] only {len(data)} rows (<{MIN_ROWS_FOR_MARKET_MODEL}) "
                "or lightgbm unavailable — using simple price/sqft quantiles as fallback. "
                "Collect more data before trusting flags from this model."
            )
            return self

        X = data[FEATURE_COLUMNS]
        y = data["price_inr"]

        for name, alpha in self.QUANTILES.items():
            model = lgb.LGBMRegressor(
                objective="quantile",
                alpha=alpha,
                n_estimators=200,
                num_leaves=15,
                min_child_samples=5,
                verbosity=-1,
            )
            model.fit(X, y)
            self.models[name] = model

        return self

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        if self.is_fallback:
            sqft = X["carpet_area_sqft"]
            return pd.DataFrame({
                "expected_low": sqft * self.fallback_ppsf["low"],
                "expected_mid": sqft * self.fallback_ppsf["mid"],
                "expected_high": sqft * self.fallback_ppsf["high"],
            }, index=X.index)

        preds = {}
        for name, model in self.models.items():
            preds[f"expected_{name}"] = model.predict(X[FEATURE_COLUMNS])
        return pd.DataFrame(preds, index=X.index)


# ---------------------------------------------------------------------------
# Builder-level shrinkage (the hierarchical / partial-pooling piece)
# ---------------------------------------------------------------------------

@dataclass
class BuilderAdjustment:
    builder_id: str
    n_projects: int
    raw_residual_pct: float      # mean (actual - expected_mid) / expected_mid, unshrunk
    shrunk_adjustment_pct: float  # after empirical Bayes shrinkage toward 0
    confidence: float             # 0-1, based on n_projects


class BuilderShrinkage:
    """Computes, per builder, how much their historical pricing deviates
    from the market model's prediction — then shrinks that estimate toward
    zero when the builder has few historical projects.

    Formula: shrunk = raw_residual * (n / (n + K))
    This is a standard empirical-Bayes-style shrinkage estimator: as
    n -> infinity, shrunk -> raw (we trust the builder's own pattern fully);
    as n -> 0, shrunk -> 0 (we fall back to the market-wide expectation).
    """

    def __init__(self, k: int = SHRINKAGE_K):
        self.k = k
        self.adjustments: dict[str, BuilderAdjustment] = {}

    def fit(self, df: pd.DataFrame, market_predictions: pd.DataFrame) -> "BuilderShrinkage":
        merged = df.join(market_predictions)
        merged["residual_pct"] = (
            (merged["price_inr"] - merged["expected_mid"]) / merged["expected_mid"]
        )

        for builder_id, group in merged.groupby("builder_id"):
            n = len(group)
            raw = group["residual_pct"].mean()
            shrink_weight = n / (n + self.k)
            shrunk = raw * shrink_weight
            confidence = min(n / (self.k * 2), 1.0)  # heuristic: full confidence at 2*K projects

            self.adjustments[builder_id] = BuilderAdjustment(
                builder_id=builder_id,
                n_projects=n,
                raw_residual_pct=raw,
                shrunk_adjustment_pct=shrunk,
                confidence=confidence,
            )

        return self

    def get(self, builder_id: str) -> BuilderAdjustment:
        return self.adjustments.get(
            builder_id,
            BuilderAdjustment(builder_id, 0, 0.0, 0.0, 0.0),
        )


# ---------------------------------------------------------------------------
# Combined model
# ---------------------------------------------------------------------------

@dataclass
class DeviationResult:
    project_id: str
    builder_id: str
    actual_price: float
    expected_low: float
    expected_mid: float
    expected_high: float
    builder_adjusted_mid: float
    deviation_pct: float
    confidence: float
    flagged: bool
    reasoning: str


class PriceDeviationModel:
    """Orchestrates MarketPriceModel + BuilderShrinkage into a single
    fit/predict interface, and produces flag decisions with plain-language
    reasoning — never an assertion of fact, always an observation +
    invitation to verify (see PRD.md section 4 on framing)."""

    def __init__(self):
        self.market_model = MarketPriceModel()
        self.shrinkage = BuilderShrinkage()

    def fit(self, df: pd.DataFrame) -> "PriceDeviationModel":
        # build_features() copies the full input df, so builder_id and
        # price_inr are already present in `features` — no need to
        # re-join them from df (doing so previously caused a column-overlap
        # error since both frames share the same index and columns).
        features = build_features(df)
        self.market_model.fit(features)
        market_preds = self.market_model.predict(features)
        self.shrinkage.fit(features, market_preds)
        return self

    def predict(self, df: pd.DataFrame) -> list[DeviationResult]:
        features = build_features(df)
        market_preds = self.market_model.predict(features)

        results = []
        for idx, row in features.iterrows():
            builder_id = df.loc[idx, "builder_id"]
            adj = self.shrinkage.get(builder_id)

            expected_mid = market_preds.loc[idx, "expected_mid"]
            builder_adjusted_mid = expected_mid * (1 + adj.shrunk_adjustment_pct)
            actual = row["price_inr"]

            deviation_pct = (actual - builder_adjusted_mid) / builder_adjusted_mid if builder_adjusted_mid else 0.0

            flagged = (
                deviation_pct <= DEVIATION_THRESHOLD
                and adj.confidence >= CONFIDENCE_THRESHOLD
            )

            reasoning = self._build_reasoning(
                df.loc[idx].get("project_name", "This project"),
                deviation_pct, adj, flagged,
            )

            results.append(DeviationResult(
                project_id=df.loc[idx].get("id", str(idx)),
                builder_id=builder_id,
                actual_price=actual,
                expected_low=market_preds.loc[idx, "expected_low"],
                expected_mid=expected_mid,
                expected_high=market_preds.loc[idx, "expected_high"],
                builder_adjusted_mid=builder_adjusted_mid,
                deviation_pct=deviation_pct,
                confidence=adj.confidence,
                flagged=flagged,
                reasoning=reasoning,
            ))

        return results

    @staticmethod
    def _build_reasoning(project_name: str, deviation_pct: float, adj: BuilderAdjustment, flagged: bool) -> str:
        if not flagged:
            return "Priced within the expected range for this builder and market segment."

        pct = abs(deviation_pct) * 100
        confidence_note = (
            "based on a solid history of past projects"
            if adj.confidence >= 0.7
            else "based on limited historical data for this builder — treat with extra caution"
        )
        return (
            f"{project_name} is priced about {pct:.0f}% below what we'd expect "
            f"for this builder, {confidence_note}. This could reflect lower-spec "
            f"materials, a promotional price, or other factors not visible in "
            f"public data. Consider asking the builder for a material spec sheet "
            f"before booking."
        )


# ---------------------------------------------------------------------------
# Explainability (SHAP)
# ---------------------------------------------------------------------------

def explain_prediction(market_model: MarketPriceModel, X_row: pd.DataFrame) -> Optional[dict]:
    """Return per-feature SHAP contributions for the median-quantile model.
    Returns None if running in fallback mode (no trained tree model exists
    to explain) or if shap isn't installed.
    """
    if market_model.is_fallback or shap is None or "mid" not in market_model.models:
        return None

    explainer = shap.TreeExplainer(market_model.models["mid"])
    shap_values = explainer(X_row[FEATURE_COLUMNS])
    return dict(zip(FEATURE_COLUMNS, shap_values.values[0]))


# ---------------------------------------------------------------------------
# Leakage-safe evaluation
# ---------------------------------------------------------------------------

def pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, quantile: float) -> float:
    diff = y_true - y_pred
    return float(np.mean(np.maximum(quantile * diff, (quantile - 1) * diff)))


def evaluate_group_kfold(df: pd.DataFrame, n_splits: int = 3) -> dict:
    """Evaluate the market model with group k-fold, grouping by builder_id
    so a builder's projects never span both train and test — see PRD.md
    section 7 on avoiding leakage. Requires enough distinct builders to
    form n_splits groups; returns a warning dict if not.
    """
    features = build_features(df).dropna(subset=["price_inr"] + FEATURE_COLUMNS)
    n_builders = features["builder_id"].nunique()

    if n_builders < n_splits:
        return {
            "status": "insufficient_builders",
            "n_builders": n_builders,
            "required": n_splits,
            "note": "Need more distinct builders in the dataset before group k-fold evaluation is meaningful.",
        }

    if len(features) < MIN_ROWS_FOR_MARKET_MODEL:
        return {
            "status": "insufficient_rows",
            "n_rows": len(features),
            "required": MIN_ROWS_FOR_MARKET_MODEL,
            "note": "Not enough rows to train a real model — evaluation would just measure the fallback heuristic.",
        }

    groups = features["builder_id"]
    gkf = GroupKFold(n_splits=n_splits)
    fold_losses = {"low": [], "mid": [], "high": []}

    for train_idx, test_idx in gkf.split(features, groups=groups):
        train_df, test_df = features.iloc[train_idx], features.iloc[test_idx]
        model = MarketPriceModel().fit(train_df)
        preds = model.predict(test_df)
        y_true = test_df["price_inr"].values

        for name, q in MarketPriceModel.QUANTILES.items():
            fold_losses[name].append(pinball_loss(y_true, preds[f"expected_{name}"].values, q))

    return {
        "status": "ok",
        "n_splits": n_splits,
        "mean_pinball_loss": {k: float(np.mean(v)) for k, v in fold_losses.items()},
    }


# ---------------------------------------------------------------------------
# CLI: fetch from Supabase, fit, evaluate, print flagged projects
# ---------------------------------------------------------------------------

def _fetch_projects_df() -> pd.DataFrame:
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")

    client = create_client(url, key)
    resp = client.table("projects").select("*").execute()
    return pd.DataFrame(resp.data)


def main():
    df = _fetch_projects_df()
    print(f"[price_deviation_model] loaded {len(df)} projects from Supabase")

    if df.empty:
        print("[price_deviation_model] no data — run rera_ingest.py first")
        return

    model = PriceDeviationModel().fit(df)
    results = model.predict(df)

    print("\n--- Evaluation (group k-fold by builder) ---")
    print(evaluate_group_kfold(df))

    print("\n--- Predictions ---")
    for r in results:
        marker = "🚩" if r.flagged else "  "
        print(f"{marker} {r.project_id}: actual={r.actual_price:.0f} "
              f"expected_mid={r.builder_adjusted_mid:.0f} "
              f"deviation={r.deviation_pct:+.1%} confidence={r.confidence:.2f}")
        if r.flagged:
            print(f"     -> {r.reasoning}")


if __name__ == "__main__":
    main()
