"""security/sanitize.py (recreated in sandbox for testing)"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

INJECTION_PATTERNS = [
    r"ignore (all |the )?(previous|prior|above) instructions",
    r"disregard (all |the )?(previous|prior|above)",
    r"you are now (a|an) ",
    r"system prompt",
    r"</?(system|assistant|user)>",
    r"new instructions?:",
    r"act as (if )?you (are|were)",
    r"forget (everything|all|what) (you|i) (said|told)",
    r"reveal your (instructions|prompt|system message)",
]
_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]
MAX_CONTENT_LENGTH = 8000


@dataclass
class SanitizeResult:
    clean_text: str
    flagged: bool = False
    matched_patterns: list = field(default_factory=list)
    truncated: bool = False


def scan_for_injection(text: str):
    matches = [p.pattern for p in _COMPILED_PATTERNS if p.search(text)]
    return (len(matches) > 0, matches)


def sanitize(text: str, source: str = "unknown") -> SanitizeResult:
    if not text:
        return SanitizeResult(clean_text="", flagged=False)
    truncated = len(text) > MAX_CONTENT_LENGTH
    working_text = text[:MAX_CONTENT_LENGTH]
    flagged, matches = scan_for_injection(working_text)
    if flagged:
        print(f"[security.sanitize] injection pattern matched, source={source}, patterns={matches}")
    return SanitizeResult(clean_text=working_text, flagged=flagged, matched_patterns=matches, truncated=truncated)


def wrap_as_data(content: str, label: str = "scraped_content") -> str:
    safe_content = content.replace(f"</{label}>", f"&lt;/{label}&gt;")
    return f"<{label}>\n{safe_content}\n</{label}>"


def sanitize_and_wrap(text: str, source: str = "unknown", label: str = "scraped_content") -> str:
    result = sanitize(text, source=source)
    return wrap_as_data(result.clean_text, label=label)
