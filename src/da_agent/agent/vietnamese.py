"""Expand Vietnamese chat shorthand and decode unconverted Telex typing in a question.

Users type "hqua" for "hôm qua", "dthu" for "doanh thu", and, when the input method is off, raw
Telex keys such as "tieenf" for "tiền". A small model can misread these: "bua qua" (yesterday)
became a product name. Code expands what it can decide for certain. The prompt keeps the original
question next to the expansion, so a wrong expansion costs little.

A Telex word is decoded only when the result is a valid Vietnamese syllable, so product names and
English words ("Strepsils", "Cool", "check") stay as written.
"""

import re
import unicodedata

# Whole words, matched case-insensitively. Only expansions with one likely meaning: "k" is left
# alone because it also means thousand ("25k").
SHORTHAND = {
    "dthu": "doanh thu",
    "bn": "bao nhiêu",
    "bnhiu": "bao nhiêu",
    "nhiu": "nhiêu",
    "nhìu": "nhiều",
    "hnay": "hôm nay",
    "hqua": "hôm qua",
    "trc": "trước",
    "dc": "được",
    "đc": "được",
    "ko": "không",
    "z": "vậy",
    "j": "gì",
    "vs": "với",
    "sp": "sản phẩm",
    "gium": "giúp",
}
PHRASES = [
    (re.compile(r"\b(?:bua|bữa|hum|hum) qua\b", re.IGNORECASE), "hôm qua"),
    (re.compile(r"\b(?:bua|bữa|hum) nay\b", re.IGNORECASE), "hôm nay"),
]
WORD = re.compile(r"[^\W\d_]+")

TONES = {
    key: unicodedata.lookup(f"COMBINING {name}")
    for key, name in [
        ("s", "ACUTE ACCENT"),
        ("f", "GRAVE ACCENT"),
        ("r", "HOOK ABOVE"),
        ("x", "TILDE"),
        ("j", "DOT BELOW"),
    ]
}
# Applied in order: the longer keys first.
MARKS = [("uwow", "ươ"), ("uow", "ươ"), ("aa", "â"), ("ee", "ê"), ("oo", "ô"), ("aw", "ă")]
MARKS += [("ow", "ơ"), ("uw", "ư")]


def _words(text: str) -> list[str]:
    return text.split()


INITIALS = sorted(
    _words("ngh ng nh ch gh gi kh ph qu th tr b c d đ g h k l m n p r s t v x"),
    key=len,
    reverse=True,
)
VOWELS = set("aăâeêioôơuưy")
MODIFIED = set("ăâêôơư")
# English words that happen to be valid Telex, common in product names ("Gas Petrolimex").
LOANWORDS = frozenset(_words("gas max air mix box fax tax bus car bar star sir"))


def _rhymes() -> frozenset[str]:
    """Vietnamese rhymes: a vowel nucleus with an optional final consonant or glide."""
    rhymes = set(_words("a e ê i o ô ơ u ư y"))
    rhymes |= set(_words("ai ao au ay âu ây eo êu ia iu oi ôi ơi ua ui ưa ưi ưu uy oa oe uê uơ"))
    rhymes |= set(_words("iêu yêu uôi ươi ươu oai oay uây uya uyu oeo"))
    for nucleus in _words("a e ê i o ô ơ u ư ă â iê yê uô ươ oa oă uâ"):
        rhymes |= {nucleus + final for final in _words("c m n ng p t")}
    for nucleus in _words("a ê i oa uy uê"):
        rhymes |= {nucleus + "ch", nucleus + "nh"}
    rhymes |= set(_words("oen oet uyn uyt uyp uyên uyêt"))
    return frozenset(rhymes)


RHYMES = _rhymes()


def _split(syllable: str) -> tuple[str, str] | None:
    """The initial consonant and the rhyme, or None when the rhyme is not Vietnamese."""
    initial = next((i for i in INITIALS if syllable.startswith(i)), "")
    rhyme = syllable[len(initial) :]
    if initial == "gi" and not rhyme:
        initial, rhyme = "g", "i"
    return (initial, rhyme) if rhyme in RHYMES else None


def _tone_index(rhyme: str) -> int:
    """Where the tone mark goes in the rhyme, by the usual modern rules."""
    run = 0
    while run < len(rhyme) and rhyme[run] in VOWELS:
        run += 1
    modified = [i for i in range(run) if rhyme[i] in MODIFIED]
    if modified:
        return modified[-1]
    if run == 1:
        return 0
    if run < len(rhyme):
        return run - 1
    if run == 3:
        return 1
    return 1 if rhyme[:2] in ("oa", "oe", "uy", "uê") else 0


def _telex(word: str) -> str | None:
    """The decoded word, or None when it is not a Telex-typed Vietnamese syllable."""
    if not word.isascii() or word.lower() in LOANWORDS:
        return None
    lower = word.lower()
    tone = TONES.get(lower[-1]) if len(lower) > 1 else None
    base = lower[:-1] if tone else lower
    if base.startswith("dd"):
        base = "đ" + base[2:]
    for keys, letter in MARKS:
        base = base.replace(keys, letter)
    if base == lower:
        return None
    parts = _split(base)
    if parts is None:
        return None
    initial, rhyme = parts
    if tone:
        index = _tone_index(rhyme)
        rhyme = rhyme[: index + 1] + tone + rhyme[index + 1 :]
    return unicodedata.normalize("NFC", initial + rhyme)


def _word(match: re.Match[str]) -> str:
    word = match.group(0)
    return SHORTHAND.get(word.lower()) or _telex(word) or word


def expand_question(question: str) -> str:
    """The question with shorthand expanded and Telex decoded; unchanged when there is none."""
    text = question
    for pattern, replacement in PHRASES:
        text = pattern.sub(replacement, text)
    return WORD.sub(_word, text)
