"""All user-facing bot text lives in pulse/bot/texts_hi.py."""
import ast
import re

from pulse.bot import texts_hi
from pulse.config import REPO_ROOT

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
PULSE = REPO_ROOT / "pulse"
# Matching vocabulary, never shown to users: Hindi number words and Devanagari folding tables.
VOCABULARY_FILES = {"pulse/ledger/parse_amount.py", "pulse/common/text.py"}


def test_no_devanagari_outside_texts_hi():
    """Any Hindi in code (even in comments) outside texts_hi.py is a string that escaped the catalogue."""
    offenders = []
    for path in PULSE.rglob("*.py"):
        if path.name == "texts_hi.py" or path.relative_to(REPO_ROOT).as_posix() in VOCABULARY_FILES:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DEVANAGARI.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{n}")
    assert offenders == []


def test_bot_code_sends_no_literal_strings():
    """Every text= / send_message / edit_message_text argument in pulse/bot is a name, never a literal string."""
    offenders = []
    for path in (PULSE / "bot").glob("*.py"):
        if path.name == "texts_hi.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if name in ("send_message", "edit_message_text", "answer_callback_query", "send", "edit_or_send"):
                    for arg in list(node.args) + [k.value for k in node.keywords]:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.strip():
                            offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_catalogue_is_hindi():
    simple = {k: v for k, v in vars(texts_hi).items() if k.isupper() and isinstance(v, str)}
    english_ok = {"CANCEL_WORDS", "PII_JOIN", "SUBHEAD_SEP", "ME_ENTRY", "ADVANCE_LINE", "ME_TEAM_ENTRY", "LM_TOP_ENTRY", "ITEMS_JOIN", "ME_REPORT_ENTRY", "ALERT_RESTRICTED", "ALERT_PLACE_TIME", "DIGEST_LINE", "DIGEST_SPEND_LINE"}   # separators and format-only strings
    for key, value in simple.items():
        if key not in english_ok:
            assert DEVANAGARI.search(value), key
