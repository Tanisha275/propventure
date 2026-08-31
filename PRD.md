# PropVenture — Product Requirements Document

## 1. Vision

PropVenture is a multi-agent AI research assistant for Indian home buyers — starting with the Mira Road / greater Mumbai market — that acts like a genuine, unbiased friend rather than a listings platform funded by builder ads. It shows every project that fits a buyer's budget, ranked by fit and credibility, never by who paid more, and surfaces the specific things builders don't publicly disclose: track record, complaint history, and a data-driven flag when a project is priced suspiciously below a builder's usual tier.

## 2. Problem statement

- Existing platforms (99acres, MagicBricks, Housing.com) rank by ad spend, not buyer fit — pay-to-rank listings bury better-fit, lower-marketing-budget projects.
- Renders and marketing images don't reflect delivered quality; buyers discover material/finish shortfalls 1–2 years after possession.
- Builder reputation is used as a heuristic ("they're a known name, must be good") even when a specific project quietly cuts costs relative to that builder's usual standard.
- RERA data (the most reliable public source) exists but is scattered, slow to search, and not cross-referenced against pricing or reviews anywhere.

## 3. Target user & core use case

A buyer with a fixed budget and area in mind (e.g. "2BHK in Mira Road, ₹1cr") wants: every matching project, sorted by fit, with a transparent credibility signal and an explicit flag when something needs a closer look — plus the reasoning behind that flag, not just a score.

## 4. USP (what makes this different)

1. **No paid placement** — rank is budget-fit + credibility only, stated explicitly in the UI.
2. **Price-tier deviation signal** — a hierarchical/quantile regression model compares a project's price to what the same builder's own pricing history predicts, flagging meaningful negative deviation as "worth asking about" rather than asserting a quality claim outright.
3. **Explainability by default** — every score ships with the reasoning behind it (SHAP-style feature attribution, plain-language summary), not a black-box number.
4. **Builder accountability, not accusation** — flags are framed as data-backed observations with an invitation to verify (spec sheet, RERA record), never as defamatory claims. Builders who publish specs get that data pulled in directly, overriding the inference.

## 5. MVP feature scope

**In scope for MVP:**
- Budget/location/BHK search → structured filter query against stored listings (deterministic, not LLM-driven matching)
- Builder credibility score from MahaRERA public data (possession delays, complaint counts) + review/news sentiment as a secondary signal
- Price-tier deviation model (hierarchical/partial-pooling + quantile regression) with confidence-gated flagging
- Credibility card UI: project name, price, credibility score + reasoning, price-deviation flag when applicable
- User accounts (Supabase Auth) with saved searches / wishlist, protected by Row Level Security
- Security/guardrail layer: input sanitization for all scraped content, output validation against structured data before any score is written
- MLOps observability: MLflow for model tracking, Evidently for drift monitoring

**Explicitly out of scope for MVP (future work):**
- Price negotiation / lower-price-finding features
- Prompt injection defense demo — separate standalone portfolio artifact, built after PropVenture MVP is stable
- Mortgage/loan eligibility tools
- Multi-city expansion beyond greater Mumbai
- Native mobile app (web-first via Streamlit)

## 6. Architecture summary

Three data source categories (MahaRERA, listing sites, reviews/news) feed a multi-agent core — search agent, credibility agent, price-deviation model — coordinated by an orchestrator that validates each agent's output against structured ground truth before anything is written to Supabase (Postgres + RLS). An MLOps layer (MLflow + Evidently) observes the pipeline without sitting in the live request path. The Streamlit frontend reads only scored, stored results — it never calls agents or the LLM directly. Full diagram covered separately; see project README for the current version.

## 7. ML approach (price-tier deviation model)

- **Problem framing**: hierarchical/partial-pooling regression — market-wide price model (LightGBM) plus a builder-level shrinkage adjustment, so builders with few historical projects don't get falsely confident deviation estimates.
- **Features**: construction stage, carpet-to-saleable ratio, floor, amenities tier, micro-location, possession timeline, builder registration age.
- **Output**: quantile prediction (10th/50th/90th percentile expected price), not a single point estimate — deviation is flagged only when actual price falls outside the expected range *and* confidence (based on builder's historical data volume) clears a threshold.
- **Evaluation**: group k-fold by builder + time-based split to avoid leakage. Backtest flagged projects against real-world outcomes (later complaints, negative reviews) to compute precision of the signal — this is the model's core validation metric.
- **Explainability**: SHAP values per prediction, surfaced in the UI as the "why" behind each flag.

## 8. Security & privacy

- Prompt injection defense: all scraped content treated as untrusted data, wrapped and isolated in prompts, validated on output against structured facts before persisting.
- Supabase Auth for login (no custom auth), rate-limited attempts, generic error messaging (no account enumeration), RLS on every user-data table.
- Data minimization aligned with India's DPDP Act 2023 — collect only budget range, saved searches, contact info; avoid sensitive financial data unless a feature explicitly requires it.
- Secrets via environment variables only, never committed; API keys rotated if ever exposed.

## 9. Tech stack

Python, Streamlit (frontend), Supabase (Postgres + Auth + RLS), `google-genai` (Gemini), LightGBM (price model), MLflow + Evidently (observability), deployed across free tiers on Streamlit Cloud / Railway / Render.

## 10. Design reference

See `design.md` — Swiss-style grid and typography, blue/white/red palette (blue = interactive/brand, red = reserved exclusively for credibility risk flags), interactivity carries the modern/GenZ feel rather than decoration.

## 11. Success criteria (for portfolio purposes)

- End-to-end working demo: real budget query → real MahaRERA-sourced credibility scores → at least one genuinely flagged project with a defensible reasoning trace.
- Documented backtest precision for the price-deviation signal, even if the number is modest — the validation methodology matters more than a high score.
- Clear write-up of the trust-first positioning and the specific gaps in existing proptech it addresses.
