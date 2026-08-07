"""Normaliser + blended fuzzy scorer - the rules half of matching.

This module used to also own the LLM escalation call (fuzzy first, LLM only
below 60%). That design is inverted now (CONTRACTS.md §7, §10): the LLM is
the primary matcher and this module is its guardrail and its fallback -
`core/resolve.py` orchestrates that, `core/llm.py` owns the provider calls.
`Matcher` here is pure rules: normalise, score, rank, and a threshold-only
`decide()` used whenever the LLM is unavailable or its answer is rejected.
The LLM never mints anything and never sees this file - it only ever sees
the shortlist `rank()`/`rank_groups()` produce.
"""
import re

try:
    from rapidfuzz import fuzz, process
    HAVE_FUZZ = True
except ImportError:                                     # pragma: no cover
    HAVE_FUZZ = False
    import difflib

# ---------------------------------------------------------------- vocabulary
UNIT_CANON = {
    "ML": "ML", "MILLILITRE": "ML", "MILLILITER": "ML", "MILILITRE": "ML",
    "L": "LTR", "LT": "LTR", "LTR": "LTR", "LITRE": "LTR", "LITER": "LTR", "LTRS": "LTR",
    "G": "GM", "GM": "GM", "GMS": "GM", "GRAM": "GM", "GRAMS": "GM", "GRM": "GM",
    "KG": "KG", "KGS": "KG", "KILO": "KG", "KILOGRAM": "KG", "KGM": "KG",
    "MM": "MM", "CM": "CM", "MTR": "MTR", "M": "MTR", "METER": "MTR", "METRE": "MTR",
    "NOS": "NOS", "NO": "NOS", "PCS": "NOS", "PC": "NOS", "PIECE": "NOS",
    "PIECES": "NOS", "UNIT": "NOS", "UNITS": "NOS", "EA": "NOS", "EACH": "NOS",
    "PKT": "PKT", "PACKET": "PKT", "PACK": "PKT", "BOX": "BOX", "SET": "SET",
    "ROLL": "ROLL", "AH": "AH", "MAH": "MAH", "KWH": "KWH", "V": "V", "W": "W",
    "INCH": "IN", "IN": "IN", '"': "IN",
}

NOISE = {
    "QTY", "QUANTITY", "PACK", "PACKING", "PACKED", "APPROX", "ASSORTED",
    "MAKE", "BRAND", "MODEL", "TYPE", "GRADE", "ITEM", "MATERIAL", "SUPPLY",
    "SUPPLYING", "PROVIDING", "NEW", "GOOD", "QUALITY", "STD", "STANDARD",
    "WITH", "WITHOUT", "FOR", "AND", "THE", "OF", "IN", "AS", "PER", "NOS",
    "INR", "RS", "MRP", "INCL", "EXCL", "GST", "TAX", "HSN", "SAC", "TOTAL",
}

# domain synonyms - one canonical token so "Mseal" and "M Seal" collapse
SYNONYM = {
    "MSEAL": "M SEAL", "MSEALL": "M SEAL",
    "SS": "STAINLESS STEEL", "MS": "MILD STEEL", "GI": "GALVANISED IRON",
    "PPE": "SAFETY", "HANDGLOVE": "HAND GLOVES", "GLOVE": "GLOVES",
    "HELMET": "SAFETY HELMET", "SPECS": "SPECTACLES",
    "LIION": "LI-ION", "LIION": "LI-ION", "LITHIUMION": "LI-ION",
    "NCM": "NMC", "LFP": "LFP", "LIFEPO4": "LFP", "LCO": "LCO",
    "CYLINDRICAL": "CYLINDRICAL", "CYL": "CYLINDRICAL",
    "EOL": "END OF LIFE", "BLACKMASS": "BLACK MASS",
    "HOUSEKEEPING": "HOUSE KEEPING", "STATIONARY": "STATIONERY",
    "XEROX": "PHOTOCOPY", "AIRFRESHENER": "AIR FRESHENER",
    "COPPER": "COPPER", "CU": "COPPER", "AL": "ALUMINIUM",
    "ALUMINUM": "ALUMINIUM", "ALUMINIUM": "ALUMINIUM",
    "CCTV": "CCTV", "HDD": "HARD DISK", "HARDDISK": "HARD DISK",
    "SSD": "SOLID STATE DRIVE", "UPS": "UPS", "MCB": "MCB",
}


def normalize(text):
    """Aggressive, order-preserving normalisation used for every comparison."""
    if not text:
        return ""
    t = str(text).upper()
    t = t.replace("&", " AND ")
    t = re.sub(r"[^A-Z0-9./\-\s]", " ", t)
    # 200ml -> 200 ML ;  5*6 -> 5 X 6
    t = re.sub(r"(\d)\s*[*xX]\s*(\d)", r"\1 X \2", t)
    t = re.sub(r"(?<=\d)(?=[A-Z])", " ", t)
    t = re.sub(r"(?<=[A-Z])(?=\d)", " ", t)
    toks = []
    for w in t.split():
        w = w.strip("./-")
        if not w:
            continue
        w = SYNONYM.get(w, w)
        w = UNIT_CANON.get(w, w)
        if w in NOISE:
            continue
        # 0200 -> 200 but keep pure code-ish tokens
        if w.isdigit():
            w = str(int(w))
        toks.append(w)
    return " ".join(toks)


def _soft_jaccard(ta, tb, tol=85):
    """Jaccard over tokens, where a typo still counts as the same token.

    This is what stops a one-word ERP row called 'NMC' from scoring 100
    against 'cylindrical NMC battery pack 32700' - a subset is not a match.
    """
    if not ta or not tb:
        return 0
    shared = 0
    for t in ta:
        if t in tb:
            shared += 1
        elif HAVE_FUZZ and any(fuzz.ratio(t, u) >= tol for u in tb):
            shared += 1
    union = len(ta) + len(tb) - shared
    return int(100 * shared / union) if union else 0


def _ratio(a, b, contains_ok=False):
    """Blend three views so no single one can run away with the answer.

    token_set_ratio rewards subsets, token_sort_ratio survives typos and word
    order, soft-Jaccard punishes length mismatch. Weighted together they agree
    only when the two strings really describe the same thing.
    """
    if not a or not b:
        return 0
    ta, tb = a.split(), b.split()
    if HAVE_FUZZ:
        setr = fuzz.token_set_ratio(a, b)
        sortr = fuzz.token_sort_ratio(a, b)
    else:
        setr = sortr = int(difflib.SequenceMatcher(None, a, b).ratio() * 100)
    jac = _soft_jaccard(ta, tb)
    score = int(0.45 * setr + 0.30 * sortr + 0.25 * jac)
    # a short dictionary entry sitting whole inside a long invoice line is a
    # real signal for spec values, but never for whole items or groups
    if contains_ok and len(tb) <= 4 and b in a:
        score = max(score, 88)
    return score


class Matcher:
    """Stateless rules scorer: normalise, blend, rank, threshold-decide.

    `llm_config` is accepted only for backward compatibility with how
    `server.py` constructs this class from `config.json`'s `llm` block - it
    is no longer read here. Real LLM configuration now lives in the
    `settings` table and is read by `core/llm.py` at call time, per
    CONTRACTS.md §5 ("secrets live in settings, never in config.json").
    """

    def __init__(self, llm_config=None, threshold=60):
        self.llm = llm_config or {}
        self.threshold = threshold

    # ------------------------------------------------------------ scoring
    def similar(self, a, b):
        return _ratio(normalize(a), normalize(b))

    def rank(self, query, candidates, key=lambda c: c, limit=6, contains_ok=False):
        """candidates -> [(candidate, score)] best first.

        key() may return a plain string, a list of strings, or a list of
        (text, weight) pairs - weights let a group be matched through the
        names of items already inside it, at a discount.
        """
        q = normalize(query)
        scored = []
        for c in candidates:
            texts = key(c)
            if isinstance(texts, str):
                texts = [texts]
            best = 0
            for t in texts:
                w = 1.0
                if isinstance(t, (tuple, list)):
                    t, w = t[0], t[1]
                if not t:
                    continue
                best = max(best, int(_ratio(q, normalize(t), contains_ok) * w))
            scored.append((c, best))
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]

    def decide(self, query, candidates, key=lambda c: c, kind="value", context="",
               contains_ok=False, threshold=None):
        """Rules-only decision. Returns dict(decision, candidate, score,
        layer, alternatives). `layer` is always 'rules' here - this is the
        guardrail/fallback path, never the primary decision-maker any more.
        The primary (LLM-first) decision is made in `core/resolve.py`, which
        calls this only as the deterministic backstop: no key configured,
        `match.mode` is fuzzy, the provider failed, or its answer was
        outside the shortlist / illegal for the slot.
        """
        th = self.threshold if threshold is None else threshold
        ranked = self.rank(query, candidates, key=key, contains_ok=contains_ok)
        if not ranked:
            return {"decision": "new", "candidate": None, "score": 0,
                    "layer": "rules", "alternatives": []}
        top, score = ranked[0]
        alts = [{"value": self._label(key(c)), "score": s, "ref": c} for c, s in ranked]
        if score >= th:
            return {"decision": "match", "candidate": top, "score": score,
                    "layer": "rules", "alternatives": alts}
        return {"decision": "new", "candidate": None, "score": score,
                "layer": "rules", "alternatives": alts}

    @staticmethod
    def _label(v):
        if isinstance(v, (list, tuple)):
            return v[0] if v else ""
        return v


# --------------------------------------------------------------- convenience
def rank_groups(con, text, limit=5):
    """CONTRACTS.md §7 convenience wrapper: the rules' group shortlist,
    scored against each group's own name, its aliases, and the names of
    items already inside it at a 0.9 discount (see `resolve.group_exemplars`
    - that discount is what lets an item with no name resemblance to any
    group name still find its home through its neighbours).

    Deferred import: `resolve.py` imports this module, so importing it back
    at module load time would be circular; importing inside the function
    body only touches `resolve` once this is actually called.
    """
    from . import resolve as R
    groups = R.load_groups(con)

    def gkey(g):
        out = [(g["name"], 1.0)] + [(a, 1.0) for a in R.group_aliases(con, g["id"])]
        out += [(n, 0.90) for n in R.group_exemplars(con, g["id"])]
        return out

    m = Matcher()
    ranked = m.rank(text, groups, key=gkey, limit=limit)
    return [{"group": g, "score": s} for g, s in ranked]
