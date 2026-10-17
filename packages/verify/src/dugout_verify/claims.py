"""Atomic claims: split a draft into sentences and give each a type that decides how it is checked.

  stat      cites at least one `{{stat:id}}`  -> Stat Auditor (re-run, cross-source) + entity/metric binding
  injury    availability / fitness claim      -> Fact-Check against official injury data and news (fresh only)
  transfer  signing, bid, fee, contract claim -> Fact-Check against news (needs >= 2 independent sources)
  result    match result claim                -> Fact-Check
  attributed "According to <outlet>, ..."     -> that outlet's own text must entail the reported content
  analysis  recommendation or judgement       -> not fact-checked; published labelled as analysis
  fact      any other factual statement       -> Fact-Check

Domain keywords win over hedging: "X could miss Sunday with a hamstring injury" is an injury claim, not analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dugout_verify.ledger import PLACEHOLDER

INJURY = re.compile(
    r"\b(injur\w*|hamstring|knee|ankle|thigh|calf|groin|achilles|muscle|suspen\w*|banned|ban|doubt\w*|"
    r"fitness|ruled out|sidelined|unavailable|out for|knock|illness|surgery)\b",
    re.I,
)
TRANSFER = re.compile(
    r"\b(sign\w*|transfers?|transferred|bid|fee|loan\w*|deal|contract\w*|joined|joins|move to|moved to|linked|"
    r"release clause|agreed|talks|offer|swoop|target\w*)\b",
    re.I,
)
RESULT = re.compile(
    r"\b(beat|beaten|lost to|drew|draw with|won against|defeat\w*|victory over|thrash\w*)\b", re.I
)
ANALYSIS = re.compile(
    r"\b(should|could|would|might|may|recommend\w*|suggest\w*|we think|in our view|consider\w*|ideal|"
    r"best option|priority|need\w*|ought|arguably|appears|seems|projected|model expects|model rates|"
    r"our model|the model|we expect|we estimate|estimat\w*|forecast\w*|we project|predicted|"
    r"our analysis|dream target|realistic target|hidden gem)\b",
    re.I,
)
# Sentence boundary: ., ! or ? followed by whitespace and an upper-case letter, quote or placeholder.
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[\"'“A-Z{])")


ATTRIBUTED = re.compile(
    r"^(?:according to|as reported by|per) (?P<a>[A-Z][\w&'. -]{1,40}?), (?P<rest>.+)$"
    r"|^(?P<b>[A-Z][\w&'. -]{1,40}?) (?:reports?|reported|says|said|writes|wrote) (?:that )?(?P<rest2>.+)$",
    re.I,
)


def attribution(sentence: str) -> tuple[str, str] | None:
    """(outlet, reported content) for "According to X, ..." / "X reports that ..." sentences."""
    m = ATTRIBUTED.match(sentence.strip())
    if not m:
        return None
    outlet = m.group("a") or m.group("b")
    rest = m.group("rest") or m.group("rest2")
    return outlet.strip(), rest[0].upper() + rest[1:]


@dataclass
class Claim:
    index: int
    text: str  # template text (placeholders intact)
    type: str
    stat_ids: list[str]


def split_sentences(text: str) -> list[str]:
    # protect placeholders (formats like |.1f contain dots) and common abbreviations from the splitter
    holes: list[str] = []

    def hide(m: re.Match[str]) -> str:
        holes.append(m.group(0))
        return f"\x00{len(holes) - 1}\x00"

    masked = PLACEHOLDER.sub(hide, text)
    masked = re.sub(
        r"\b(vs|v|St|Mr|Dr|Jr|Sr|No|approx|e\.g|i\.e)\.", lambda m: m.group(0)[:-1] + "\x01", masked
    )
    # prose has no literal numbers, so "1." is part of a club name ("1. FC Köln", "1. Fußballclub Heidenheim 1846")
    masked = re.sub(r"(?<![\w.])(\d)\.(?=\s)", "\\1\x01", masked)
    parts = [p.strip() for p in _BOUNDARY.split(masked.replace("\n", " ")) if p.strip()]
    restore = re.compile("\x00(\\d+)\x00")
    return [restore.sub(lambda m: holes[int(m.group(1))], p).replace("\x01", ".") for p in parts]


def classify(sentence: str) -> str:
    if PLACEHOLDER.search(sentence):
        return "stat"
    if attribution(sentence):
        return "attributed"
    if INJURY.search(sentence):
        return "injury"
    if TRANSFER.search(sentence):
        return "transfer"
    if RESULT.search(sentence):
        return "result"
    if ANALYSIS.search(sentence):
        return "analysis"
    return "fact"


def secondary_type(sentence: str, own_words: list[str] | None = None) -> str | None:
    """A stat sentence can also carry a news claim ("X, out with a knee injury, has scored ...").
    `own_words`: regexes describing the cited metrics themselves (a contract-expiry stat may say "contract"),
    removed first so the stat's own wording does not count as an extra claim."""
    bare = PLACEHOLDER.sub(" ", sentence)
    for w in own_words or []:
        bare = re.sub(w, " ", bare, flags=re.I)
    if INJURY.search(bare):
        return "injury"
    if TRANSFER.search(bare) and not ANALYSIS.search(bare):
        return "transfer"
    return None


def claims(text: str) -> list[Claim]:
    out = []
    for i, s in enumerate(split_sentences(text)):
        out.append(Claim(i, s, classify(s), [m.group(1) for m in PLACEHOLDER.finditer(s)]))
    return out
