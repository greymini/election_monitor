"""Intent and complexity router (LLD 8).

One cheap Haiku call decides where a question goes:

    blocked    -> fixed refusal, no model call at all beyond this one
    smalltalk  -> Haiku, knowledge cards only
    knowledge  -> Haiku, knowledge cards only
    lookup     -> Haiku + tools   (one table, one metric)
    analysis   -> Sonnet 5 + tools
    news       -> Sonnet 5 + tools

Blocked intents are also caught by a local pattern check that runs FIRST, so an
individual-voter lookup is refused without spending a token on it, and a router
failure can never turn one into an answered question.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from chatbot.budget import record
from chatbot.llm import get_client, model_kwargs, text_of, usage_from
from common.config import get_settings
from common.logging_setup import get_logger

log = get_logger(__name__)

INTENTS = ("lookup", "analysis", "news", "knowledge", "smalltalk", "blocked")

# Refusal patterns (LLD 12). Checked locally before the model sees anything.
BLOCKED_PATTERNS = [
    # EPIC number, in either format
    r"\b[A-Z]{3}\d{7}\b",
    r"\b[A-Z]{2}/\d{2}/\d{3}/\d{6}\b",
    r"\bepic\b", r"वोटर\s*आईडी", r"मतदाता\s*पहचान",
    # individual lookups
    # a persons name is one to four words, not one
    r"(?:is|was)\s+(?:\w+\s+){1,4}(?:on|in)\s+the\s+(?:voter|electoral)\s+(?:roll|list)",
    r"(?:voter|elector)\s+(?:named|called)\s+\w+",
    r"\b(?:phone|mobile|contact)\s*(?:number|no)\b",
    r"\b(?:home|house|residential)\s*address\b",
    r"मकान\s*(?:संख्या|नंबर)",
    r"(?:किस|कौन)\s*(?:व्यक्ति|आदमी|शख्स)\s*(?:की|का)\s*जाति",
    r"\bcaste\s+of\s+(?:mr|mrs|ms|shri|smt)\b",
    r"(?:what|which)\s+caste\s+is\s+\w+",
    # bulk extraction
    r"(?:list|export|download|give me)\s+(?:all\s+)?(?:the\s+)?voters?\b",
    r"(?:मतदाता|वोटर)\s*(?:सूची|लिस्ट)\s*(?:दो|दीजिए|निकालो|भेजो)",
]
_BLOCKED_RE = [re.compile(p, re.IGNORECASE) for p in BLOCKED_PATTERNS]

REFUSAL_EN = (
    "I can't answer questions about individual voters. This system holds no "
    "individual voter records at all - rolls are parsed into per-booth counts and "
    "the names are discarded - so there is nothing to look up.\n\n"
    "I can give you booth-level figures instead: electors, additions and deletions "
    "per revision, results, or estimated community composition with its confidence."
)
REFUSAL_HI = (
    "व्यक्तिगत मतदाता के बारे में जानकारी नहीं दी जा सकती। इस सिस्टम में किसी भी "
    "मतदाता का व्यक्तिगत रिकॉर्ड रखा ही नहीं जाता - मतदाता सूची से केवल बूथवार "
    "संख्या ली जाती है और नाम हटा दिए जाते हैं।\n\n"
    "बूथ स्तर के आंकड़े उपलब्ध हैं: मतदाता संख्या, नए और हटाए गए नाम, चुनाव परिणाम, "
    "और अनुमानित सामाजिक संरचना (विश्वास स्तर सहित)।"
)

ROUTER_PROMPT = """You classify questions for an election-analysis assistant covering
Giridih assembly constituency (AC-32), Jharkhand.

Reply with ONLY a JSON object, no prose:
{"intent": "...", "lang": "hi" | "en" | "hinglish"}

intent is exactly one of:
- "blocked"   - asks about an individual voter, an individual's caste, an EPIC
                number, address, phone number, or asks to export a voter list
- "smalltalk" - greeting, thanks, or a question about what this tool can do
- "knowledge" - background or context answerable from curated notes, with no
                figures needed (alliances, why there is a bypoll, who stood)
- "lookup"    - ONE number or a short list from one table (a booth result, an
                elector count, the top N booths on one metric)
- "news"      - about recent news, issues, incidents or sentiment
- "analysis"  - comparison, swing, transfer, correlation, scenario, "why",
                or anything needing more than one query

lang is the language of the QUESTION, not the subject matter."""


@dataclass
class Route:
    intent: str
    lang: str
    model: str
    use_tools: bool
    refusal: str | None = None
    degraded: bool = False


def is_blocked(text: str) -> bool:
    return any(rx.search(text or "") for rx in _BLOCKED_RE)


def detect_language(text: str) -> str:
    devanagari = sum(1 for ch in text if "ऀ" <= ch <= "ॿ")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if devanagari and latin > devanagari:
        return "hinglish"
    return "hi" if devanagari else "en"


def refusal_text(lang: str) -> str:
    return REFUSAL_HI if lang == "hi" else REFUSAL_EN


def route(question: str, user_id: int | None = None, degrade_to_haiku: bool = False) -> Route:
    """Classify a question. Local blocklist first, then one Haiku call."""
    settings = get_settings()
    lang = detect_language(question)

    if is_blocked(question):
        log.info("question blocked locally (no model call)")
        return Route(intent="blocked", lang=lang, model="", use_tools=False,
                     refusal=refusal_text(lang))

    intent = "analysis"
    try:
        client = get_client()
        response = client.messages.create(
            model=settings.model_router,
            max_tokens=64,
            system=[{"type": "text", "text": ROUTER_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": question}],
            **model_kwargs(settings.model_router),
        )
        record(usage_from(response, settings.model_router, "router"), user_id)
        parsed = _parse_router_json(text_of(response))
        intent = parsed.get("intent", intent)
        lang = parsed.get("lang", lang)
    except Exception as exc:
        # A router failure must not open a hole: fall back to the safest
        # tool-using path rather than answering unrouted.
        log.warning("router call failed (%s) - defaulting to analysis", exc)

    if intent not in INTENTS:
        intent = "analysis"

    if intent == "blocked":
        return Route(intent="blocked", lang=lang, model="", use_tools=False,
                     refusal=refusal_text(lang))

    if intent in {"smalltalk", "knowledge"}:
        return Route(intent=intent, lang=lang, model=settings.model_lookup, use_tools=False)

    if intent == "lookup":
        return Route(intent=intent, lang=lang, model=settings.model_lookup, use_tools=True)

    # analysis / news
    if degrade_to_haiku:
        return Route(intent=intent, lang=lang, model=settings.model_lookup,
                     use_tools=True, degraded=True)
    return Route(intent=intent, lang=lang, model=settings.model_analysis, use_tools=True)


def _parse_router_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(text[start: end + 1])
    except json.JSONDecodeError:
        return {}
