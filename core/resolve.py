"""The resolver: rules first, LLM as the primary matcher, rules as its
guardrail and its fallback (CONTRACTS.md §7, §10 - this direction is locked;
see agents/AGENT_C_MATCHING.md for the "why").

    line of text
         |
         v
      RULES run first, always - normalise, score, rank, and collect the
      constraints (which slots this group declares, which values are legal,
      is a vendor allowed at all)
         |
         +-- exact hit in phase 1? --> STOP. No LLM call. Nothing to judge.
         |
         v
      LLM decides, group first, then each slot - given the line, the
      shortlist, and the constraints. Only ever chooses from what the rules
      handed it, or answers "none".
         |
         v
      RULES check the answer - outside the shortlist? illegal in that slot?
      a vendor on a group that declares none? --> REJECT, the deterministic
      result stands.
         |
         v
      result, stamped matched_by: exact | llm | rules | operator

If the LLM is rate-limited, out of quota, slow, misconfigured or
unreachable, the deterministic result is used and stamped matched_by:
"rules" - the fallback is always there. When `match.mode` is "fuzzy", or no
API key is configured, the LLM is skipped entirely and this degrades to
exactly the old fuzzy-only tool, which is also the default state and must be
a complete experience on its own.

Nothing is written to the database in resolve(); commit() is the one
explicit step that runs after a human clicks Submit.
"""
import json

from . import codes as C
from . import llm as L
from .db import now, log
from .matcher import normalize

SLOTS = (1, 2, 3, 4)


# --------------------------------------------------------------- dictionary
def load_groups(con):
    rows = con.execute("""
        SELECT g.id, g.name, g.code3, g.uom, g.labels, g.status,
               s.id AS sub_id, s.name AS sub_name, s.code2 AS sub_code,
               h.id AS head_id, h.name AS head_name, h.code2 AS head_code
        FROM grp g JOIN subhead s ON s.id=g.subhead_id JOIN head h ON h.id=s.head_id
        WHERE g.status='active'""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["labels"] = json.loads(d["labels"] or "{}")
        d["prefix"] = d["head_code"] + d["sub_code"]
        out.append(d)
    return out


def group_aliases(con, gid):
    return [r["term"] for r in con.execute(
        "SELECT term FROM alias WHERE scope='group' AND ref_id=?", (gid,))]


_EXEMPLARS = {"stamp": None, "data": {}}


def group_exemplars(con, gid):
    """Names of items already living in a group.

    Matching an invoice line against these as well as the group name is what
    lets 'Mseal epoxy sealant' find the group that already holds M-Seal,
    rather than latching onto a group literally called 'Seal'. Weighted below
    the group's own name so one odd item cannot hijack a group.
    """
    stamp = con.execute("SELECT COUNT(*) c FROM item").fetchone()["c"]
    if _EXEMPLARS["stamp"] != stamp:
        _EXEMPLARS["stamp"], _EXEMPLARS["data"] = stamp, {}
    if gid not in _EXEMPLARS["data"]:
        _EXEMPLARS["data"][gid] = [r["name"] for r in con.execute(
            "SELECT name FROM item WHERE grp_id=? AND name IS NOT NULL LIMIT 8", (gid,))]
    return _EXEMPLARS["data"][gid]


def specvals(con, gid, slot):
    return [dict(r) for r in con.execute(
        "SELECT * FROM specval WHERE grp_id=? AND slot=? ORDER BY code2", (gid, slot))]


def one_sub(con, sid):
    r = con.execute("""SELECT s.*, h.name AS head_name, h.code2 AS head_code
                       FROM subhead s JOIN head h ON h.id=s.head_id WHERE s.id=?""", (sid,)).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ phase 1
def phase1_exists(con, matcher, text, name=None):
    """Does this item already have a code? Deterministic, always runs first,
    never calls an LLM. Phase 1 stops at 90, not 60 - wrongly accepting an
    existing code is worse than creating a duplicate (a duplicate is visible
    and mergeable; a wrong acceptance silently books stock against the wrong
    item)."""
    trace = {"phase": 1, "checked": [], "hit": None}
    probe = f"{name or ''} {text or ''}".strip()

    # 1a - an explicit well-formed code somewhere in the text
    for tok in probe.replace(",", " ").split():
        t = tok.strip().upper()
        if C.is_wellformed(t):
            r = con.execute("SELECT code,name,status,frozen FROM item WHERE code=?", (t,)).fetchone()
            if r:
                trace["hit"] = {"how": "explicit code", "code": r["code"],
                                "name": r["name"], "source": "item master", "score": 100}
                return trace
            r = con.execute("SELECT code,name FROM erp_item WHERE code=?", (t,)).fetchone()
            if r:
                trace["hit"] = {"how": "explicit code", "code": r["code"],
                                "name": r["name"], "source": "live ERPNext", "score": 100}
                return trace
    trace["checked"].append("explicit code in text")

    n = normalize(probe)
    if not n:
        return trace

    # 1b - exact normalised name, master then live ERP
    for table, label in (("item", "item master"), ("erp_item", "live ERPNext")):
        r = con.execute(f"SELECT code,name FROM {table} WHERE name_norm=? LIMIT 1", (n,)).fetchone()
        if r:
            trace["hit"] = {"how": "identical after normalisation", "code": r["code"],
                            "name": r["name"], "source": label, "score": 100}
            return trace
    trace["checked"].append("exact normalised name")

    # 1c - semantic near-match over both sets (rules only - this is not the
    # LLM step; it is what stops "Odonil Lavendar Air Freshner 48gm" landing
    # on a fresh code when "Air Freshener - Odonil Lavender 48 g" already
    # exists)
    cands, seen = [], set()
    for r in con.execute(
            "SELECT code,name,name_norm,'item master' AS src FROM item WHERE name IS NOT NULL "
            "UNION ALL SELECT code,name,name_norm,'live ERPNext' FROM erp_item WHERE name IS NOT NULL"):
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        cands.append(dict(r))
    ranked = matcher.rank(probe, cands, key=lambda c: [c["name"]], limit=5)
    trace["checked"].append(f"semantic scan of {len(cands)} existing items")
    if ranked:
        top, score = ranked[0]
        trace["near"] = [{"code": c["code"], "name": c["name"], "source": c["src"],
                          "score": s} for c, s in ranked if s >= 55]
        if score >= 90:
            trace["hit"] = {"how": "semantic match", "code": top["code"],
                            "name": top["name"], "source": top["src"], "score": score}
    return trace


# ------------------------------------------------------ rules: shortlist + constraints
# A product's HSN or a service's SAC code (same column on an Indian invoice,
# same field here - "HSN/SAC") is a controlled vocabulary: two lines coded
# 995446 are the same category of thing even when their description text
# reads nothing alike, which happens constantly across vendors and is
# exactly where name-only matching struggles most (services especially -
# "Earth work Excavation for Foundation" vs some other contractor's wording
# for the same line of work share no useful tokens at all). This is
# corroborating evidence alongside the name score, never a replacement for
# it - a code can span many genuinely different items, so a bare code match
# with no name resemblance stays a weak signal, not an override.
HSN_MATCH_BONUS = 15


def _groups_with_hsn(con, hsn):
    """grp_id -> count of existing items already carrying this exact
    HSN/SAC code, used as a scoring boost in _candidate_groups. Not a
    ranking signal on its own - see the note above."""
    if not hsn:
        return {}
    rows = con.execute(
        "SELECT grp_id, COUNT(*) c FROM item WHERE hsn=? AND grp_id IS NOT NULL "
        "GROUP BY grp_id", (hsn,)).fetchall()
    return {r["grp_id"]: r["c"] for r in rows}


def _candidate_groups(con, matcher, text, hints, limit=5, hsn=None):
    """The group shortlist, rules-only. `hints.group_id` is the operator
    overriding the machine outright - it collapses the shortlist to that one
    choice and nothing else is asked about it."""
    hints = hints or {}
    groups = load_groups(con)
    if hints.get("group_id"):
        g = next((x for x in groups if x["id"] == int(hints["group_id"])), None)
        if g:
            return [{"group": g, "score": 100, "forced": True}]

    def gkey(g):
        out = [(g["name"], 1.0)] + [(a, 1.0) for a in group_aliases(con, g["id"])]
        out += [(n, 0.90) for n in group_exemplars(con, g["id"])]
        return out

    # Score every group by name first (matcher.rank already does this
    # internally regardless of `limit` - it only slices the result at the
    # end - so asking for the full list back costs nothing extra), then
    # apply the HSN/SAC bonus before taking the top `limit`, so a group an
    # HSN match found isn't lost to the cut just because its name score
    # alone wasn't competitive.
    ranked = matcher.rank(text, groups, key=gkey, limit=len(groups))
    hsn_groups = _groups_with_hsn(con, hsn)
    if hsn_groups:
        ranked = sorted(
            ((g, min(100, s + HSN_MATCH_BONUS) if g["id"] in hsn_groups else s)
             for g, s in ranked),
            key=lambda x: -x[1])
    ranked = ranked[:limit]
    return [{"group": g, "score": s, "forced": False} for g, s in ranked]


def _find_specval(pool, forced):
    """Look a hint-forced value up in the FULL pool (not just the top-ranked
    slice) by id or by exact text - the operator may pick anything that
    already exists, not only what fuzzy-scoring would have surfaced."""
    if not forced or not pool:
        return None
    fn = normalize(str(forced))
    for p in pool:
        if str(p["id"]) == str(forced) or normalize(p["value"]) == fn:
            return p
    return None


def _slot_options(con, matcher, text, group, slot, hints=None, limit=6):
    hints = hints or {}
    label = (group.get("labels") or {}).get(str(slot))
    if not label:
        return None
    pool = specvals(con, group["id"], slot)
    forced = _find_specval(pool, hints.get(f"s{slot}"))
    ranked = matcher.rank(text, pool, key=lambda p: [p["value"]], contains_ok=True, limit=limit) if pool else []
    options = [{"value": p["value"], "code": p["code2"], "id": p["id"], "score": s} for p, s in ranked]
    forced_idx = None
    if forced:
        forced_idx = next((i for i, o in enumerate(options) if o["id"] == forced["id"]), None)
        if forced_idx is None:
            options.append({"value": forced["value"], "code": forced["code2"],
                            "id": forced["id"], "score": 100})
            forced_idx = len(options) - 1
    return {"slot": slot, "label": label, "options": options, "forced_idx": forced_idx}


def _vendor_options(con, matcher, vendor_text, group, hints=None, limit=6):
    hints = hints or {}
    label = (group.get("labels") or {}).get("vendor")
    if not label:
        return None
    pool = specvals(con, group["id"], 5)
    forced = _find_specval(pool, hints.get("vendor_value"))
    ranked = (matcher.rank(vendor_text, pool, key=lambda p: [p["value"]], contains_ok=True, limit=limit)
              if (pool and vendor_text) else [])
    options = [{"value": p["value"], "code": p["code2"], "id": p["id"], "score": s} for p, s in ranked]
    forced_idx = None
    if forced:
        forced_idx = next((i for i, o in enumerate(options) if o["id"] == forced["id"]), None)
        if forced_idx is None:
            options.append({"value": forced["value"], "code": forced["code2"],
                            "id": forced["id"], "score": 100})
            forced_idx = len(options) - 1
    return {"label": label, "options": options, "forced_idx": forced_idx}


def _build_line_context(con, matcher, payload):
    """Everything the rules can determine about one line, with no LLM call:
    the phase-1 exact-hit check, and - only if that misses - the group
    shortlist plus, per shortlisted group, its declared slots' value pools
    and its vendor pool. This IS the shortlist-and-constraints handed to the
    LLM; nothing the LLM sees is invented here."""
    text = payload.get("text") or ""
    name = payload.get("name") or ""
    hints = payload.get("hints") or {}
    vendor_text = payload.get("vendor")
    probe = f"{name} {text}".strip()

    p1 = phase1_exists(con, matcher, text, name)
    lc = {"payload": payload, "probe": probe, "hints": hints, "vendor_text": vendor_text,
          "phase1": p1, "candidates": [], "skip_llm": False}
    if p1["hit"] and not hints.get("force_new"):
        lc["skip_llm"] = True
        return lc

    ranked_groups = _candidate_groups(con, matcher, hints.get("group_text") or probe, hints,
                                      hsn=payload.get("hsn"))
    candidates = []
    for cg in ranked_groups:
        g = cg["group"]
        slots = [so for so in (_slot_options(con, matcher, probe, g, s, hints) for s in SLOTS) if so]
        vend = _vendor_options(con, matcher, vendor_text, g, hints)
        candidates.append({"group": g, "score": cg["score"], "forced": cg.get("forced", False),
                            "slots": slots, "vendor": vend})
    lc["candidates"] = candidates

    # a fully operator-specified line (group forced, every declared slot and
    # any named vendor forced too) needs no LLM opinion at all - this matters
    # for the UI, which re-resolves on every dropdown change
    if candidates:
        top = candidates[0]
        if (len(candidates) == 1 and top["forced"]
                and all(so.get("forced_idx") is not None for so in top["slots"])
                and (not top.get("vendor") or top["vendor"].get("forced_idx") is not None
                     or not vendor_text)):
            lc["skip_llm"] = True
    return lc


# ------------------------------------------------------------- rules decision
def _empty_decision():
    return {"group_idx": None, "group_mb": "rules", "slots": {}, "slot_mb": {},
            "vendor_idx": None, "vendor_mb": None}


def _rules_decision(lc, threshold):
    """What the rules alone would pick - the deterministic fallback used
    whenever the LLM is off, fails, or is vetoed, at the granularity of the
    whole line."""
    cands = lc["candidates"]
    if not cands:
        return _empty_decision()
    top = cands[0]
    if top["forced"]:
        group_idx, group_mb = 0, "operator"
    elif top["score"] >= threshold:
        group_idx, group_mb = 0, "rules"
    else:
        group_idx, group_mb = None, "rules"

    slots, slot_mb, vendor_idx, vendor_mb = {}, {}, None, None
    if group_idx is not None:
        for so in top["slots"]:
            skey = str(so["slot"])
            if so.get("forced_idx") is not None:
                slots[skey], slot_mb[skey] = so["forced_idx"], "operator"
            elif so["options"] and so["options"][0]["score"] >= threshold:
                slots[skey], slot_mb[skey] = 0, "rules"
        if top["vendor"]:
            if top["vendor"].get("forced_idx") is not None:
                vendor_idx, vendor_mb = top["vendor"]["forced_idx"], "operator"
            elif top["vendor"]["options"] and top["vendor"]["options"][0]["score"] >= threshold:
                vendor_idx, vendor_mb = 0, "rules"
    return {"group_idx": group_idx, "group_mb": group_mb, "slots": slots, "slot_mb": slot_mb,
            "vendor_idx": vendor_idx, "vendor_mb": vendor_mb}


def _apply_forced(lc, decision):
    """Operator overrides always win, whatever the LLM or the rules said."""
    cands = lc["candidates"]
    gi = decision.get("group_idx")
    if gi is None or gi >= len(cands):
        return decision
    cand = cands[gi]
    if cand.get("forced"):
        decision["group_mb"] = "operator"
    for so in cand["slots"]:
        if so.get("forced_idx") is not None:
            decision["slots"][str(so["slot"])] = so["forced_idx"]
            decision["slot_mb"][str(so["slot"])] = "operator"
    if cand.get("vendor") and cand["vendor"].get("forced_idx") is not None:
        decision["vendor_idx"] = cand["vendor"]["forced_idx"]
        decision["vendor_mb"] = "operator"
    return decision


# ------------------------------------------------------------- LLM: prompt
def _format_candidates(candidates):
    lines = []
    for gi, c in enumerate(candidates):
        g = c["group"]
        lines.append(f'    group[{gi}] "{g["name"]}" (rule score {c["score"]})')
        for so in c["slots"]:
            if so.get("forced_idx") is not None:
                continue                                # operator already fixed this one
            opts = "; ".join(f'{oi}={o["value"]!r}' for oi, o in enumerate(so["options"]))
            lines.append(f'      slot {so["slot"]} "{so["label"]}" options: {opts or "(none known)"}')
        v = c.get("vendor")
        if v and v.get("forced_idx") is None:
            opts = "; ".join(f'{oi}={o["value"]!r}' for oi, o in enumerate(v["options"]))
            lines.append(f'      vendor "{v["label"]}" options: {opts or "(none known)"}')
    return "\n".join(lines)


def _build_batch_prompt(items):
    """items: [(line_index, line_context), ...] - everything needing a
    decision, from one invoice, in one prompt. This is the "20 lines, one
    call" requirement: however many lines and candidates there are, this
    produces exactly one request."""
    head = (
        "You are matching purchase-invoice lines to an existing item dictionary "
        "for a mining / battery-recycling company. For EACH line decide which "
        "existing item GROUP it belongs to (or none), then - only for the group "
        "you chose - decide the value of each listed specification slot and, if "
        "one is listed, the vendor.\n\n"
        "Rules: choose ONLY from the numbered options given for that line - "
        "never invent a group, a value, or a code, and never answer with a "
        "number that was not offered for that line. A different size, grade, "
        "chemistry or brand is NOT the same item - do not force a match just "
        "because the words are similar. Use null wherever nothing on the list "
        "is right.\n\n"
        "Reply with STRICT JSON only - no prose, no markdown fences - in exactly "
        'this shape: {"lines": [{"line": <n>, "group": <index or null>, '
        '"slots": {"<slot number>": <index or null>, ...}, '
        '"vendor": <index or null>}, ...]}\n\n'
    )
    body = []
    for idx, lc in items:
        body.append(f'Line {idx}: "{lc["probe"]}"')
        if lc["candidates"]:
            body.append(_format_candidates(lc["candidates"]))
        else:
            body.append("    (rules found no candidate groups for this line - answer group: null)")
        body.append("")
    return head + "\n".join(body)


def _shortlist_signature(lc):
    """What the cache key is bound to, besides the normalised text: the
    rules' shortlist. If the dictionary changes, the signature changes, the
    key misses, and a stale cached answer is never served."""
    cands = []
    for c in lc["candidates"]:
        cands.append({
            "g": c["group"]["id"],
            "slots": [(so["slot"], [o["id"] for o in so["options"]]) for so in c["slots"]],
            "vendor": [o["id"] for o in c["vendor"]["options"]] if c.get("vendor") else None,
        })
    return {"candidates": cands, "vendor_text": normalize(lc.get("vendor_text") or "")}


def _apply_answer(lc, ans, threshold):
    """Validate one line's LLM answer against ITS OWN shortlist - the veto.
    Anything outside the shortlist, or illegal for the chosen group's slot,
    is rejected and the rules' opinion stands for that field. A slot the LLM
    simply did not mention falls back to the rules individually, so the
    guardrail applies field by field, not just line by line."""
    cands = lc["candidates"]
    rules = _rules_decision(lc, threshold)
    if not isinstance(ans, dict) or not cands:
        return rules

    gi = ans.get("group")
    if gi is None:
        return {"group_idx": None, "group_mb": "llm", "slots": {}, "slot_mb": {},
                "vendor_idx": None, "vendor_mb": None}
    if not isinstance(gi, int) or not (0 <= gi < len(cands)):
        return rules                                       # outside the shortlist -> veto

    chosen = cands[gi]
    slots_ans = ans.get("slots") if isinstance(ans.get("slots"), dict) else {}
    slots_out, slot_mb, ok = {}, {}, True
    for so in chosen["slots"]:
        skey = str(so["slot"])
        if skey in slots_ans:
            si = slots_ans.get(skey)
            if si is None:
                slot_mb[skey] = "llm"                       # LLM explicitly says: no match
                continue
            if not isinstance(si, int) or not (0 <= si < len(so["options"])):
                ok = False
                break
            slots_out[skey], slot_mb[skey] = si, "llm"
        elif so["options"] and so["options"][0]["score"] >= threshold:
            slots_out[skey], slot_mb[skey] = 0, "rules"     # LLM didn't address it - rules opine alone

    vend_idx, vend_mb = None, None
    if ok and chosen.get("vendor"):
        if "vendor" in ans:
            vi = ans.get("vendor")
            if vi is None:
                vend_mb = "llm"
            elif not isinstance(vi, int) or not (0 <= vi < len(chosen["vendor"]["options"])):
                ok = False
            else:
                vend_idx, vend_mb = vi, "llm"
        elif chosen["vendor"]["options"] and chosen["vendor"]["options"][0]["score"] >= threshold:
            vend_idx, vend_mb = 0, "rules"

    if not ok:
        return rules                                        # illegal slot/vendor index -> veto
    return {"group_idx": gi, "group_mb": "llm", "slots": slots_out, "slot_mb": slot_mb,
            "vendor_idx": vend_idx, "vendor_mb": vend_mb}


# ----------------------------------------------------------------- assembly
def _existing_result(lc):
    return {"input": lc["payload"], "phase1": lc["phase1"], "phase2": None, "phase3": None,
            "action": "existing", "code": lc["phase1"]["hit"]["code"], "blockers": [],
            "matched_by": "exact"}


def _assemble_result(con, matcher, lc, decision):
    """Turn a rules-shortlist + a (possibly LLM, possibly vetoed-back-to-
    rules) decision into the same proposal shape resolve() has always
    returned, so commit() and the existing screen keep working, with
    matched_by recorded at every level: the line, the group, each slot, and
    the vendor."""
    payload, hints = lc["payload"], lc["hints"]
    cands = lc["candidates"]
    gi = decision["group_idx"]
    group = cands[gi]["group"] if (gi is not None and cands) else None

    out = {"input": payload, "phase1": lc["phase1"], "phase2": None, "phase3": None,
           "code": None, "action": None, "blockers": [], "matched_by": decision["group_mb"]}

    # ---------------------------------------------------------------- phase2
    p2 = {"phase": 2, "steps": [], "group": group}
    alts = [{"name": c["group"]["name"], "prefix": c["group"]["prefix"],
             "code3": c["group"]["code3"], "id": c["group"]["id"], "score": c["score"]}
            for c in cands]
    p2["steps"].append({
        "level": "group", "status": "match" if group else "new", "layer": decision["group_mb"],
        "matched_by": decision["group_mb"], "value": group["name"] if group else None,
        "score": cands[gi]["score"] if (gi is not None and cands) else (cands[0]["score"] if cands else 0),
        "alternatives": alts})
    if not group:
        if hints.get("subhead_id"):
            sub = one_sub(con, int(hints["subhead_id"]))
        else:
            sub = None
            for c in cands[:5]:
                cand_sub = one_sub(con, c["group"]["sub_id"])
                if cand_sub:
                    sub = sub or cand_sub
                    p2.setdefault("suggested_subheads", []).append(
                        {"id": cand_sub["id"], "name": cand_sub["name"],
                         "head_name": cand_sub["head_name"],
                         "prefix": cand_sub["head_code"] + cand_sub["code2"],
                         "because": c["group"]["name"], "score": c["score"]})
        if sub:
            p2["subhead"] = sub
            p2["head"] = {"id": sub["head_id"], "name": sub["head_name"], "code2": sub["head_code"]}
            p2["steps"].append({
                "level": "home",
                "status": "suggested" if not hints.get("subhead_id") else "chosen by operator",
                "layer": "neighbourhood",
                "value": f"{sub['head_name']} / {sub['name']}", "score": 0})
    out["phase2"] = p2

    # ---------------------------------------------------------------- phase3
    p3 = {"phase": 3, "slots": [], "vendor": None}
    if group:
        cand = cands[gi]
        for so in cand["slots"]:
            skey = str(so["slot"])
            si = decision["slots"].get(skey)
            # no entry means nobody actually settled this one - not the
            # operator, not the LLM, not the rules' own threshold - so it
            # must not inherit the group's (possibly operator/llm) label
            mb = decision["slot_mb"].get(skey, "rules")
            options = [{"value": o["value"], "code": o["code"], "id": o["id"]} for o in so["options"]]
            if si is not None:
                opt = so["options"][si]
                p3["slots"].append({"slot": so["slot"], "label": so["label"], "status": "match",
                                    "layer": mb, "matched_by": mb, "value": opt["value"],
                                    "code": opt["code"], "specval_id": opt["id"],
                                    "score": opt["score"], "options": options})
            else:
                p3["slots"].append({"slot": so["slot"], "label": so["label"], "status": "undetermined",
                                    "layer": mb, "matched_by": mb, "value": None, "code": None,
                                    "options": options})
        vend_pool = cand.get("vendor")
        if vend_pool:
            vi, vmb = decision["vendor_idx"], decision["vendor_mb"] or "rules"
            options = [{"value": o["value"], "code": o["code"], "id": o["id"]} for o in vend_pool["options"]]
            if vi is not None:
                opt = vend_pool["options"][vi]
                p3["vendor"] = {"label": vend_pool["label"], "status": "match", "layer": vmb,
                                "matched_by": vmb, "value": opt["value"], "code": opt["code"],
                                "specval_id": opt["id"], "score": opt["score"], "options": options}
            elif lc["vendor_text"]:
                p3["vendor"] = {"label": vend_pool["label"], "status": "undetermined", "layer": vmb,
                                "matched_by": vmb, "value": None, "code": None, "options": options}
            else:
                p3["vendor"] = {"label": vend_pool["label"], "status": "not named on the invoice line",
                                "value": None, "code": None}
    else:
        labels = hints.get("new_group_labels") or {}
        for i in range(1, 5):
            lbl = labels.get(str(i))
            val = hints.get(f"s{i}")
            p3["slots"].append({"slot": i, "label": lbl, "status": "match" if val else "undetermined",
                                "layer": "manual", "matched_by": "manual", "value": val, "code": None, "options": []})
        vlbl = labels.get("vendor")
        vval = hints.get("vendor_text")
        if vlbl or vval:
            p3["vendor"] = {"label": vlbl or "Vendor", "status": "match" if vval else "undetermined",
                            "layer": "manual", "matched_by": "manual", "value": vval, "code": None, "options": []}

    out["phase3"] = p3

    # -------------------------------------------------------- assemble code
    if group:
        head2, sub2, grp3, new_group = group["head_code"], group["sub_code"], group["code3"], False
    else:
        head, sub = p2.get("head"), p2.get("subhead")
        head2 = head["code2"] if head else "??"
        sub2 = sub["code2"] if sub else "??"
        grp3, new_group = "???", True
        if not sub:
            out["blockers"].append(
                "Nothing close enough to suggest a home - pick the head and sub-head.")
        else:
            # C.next_group_code dropped (group_name, matcher) - numbering is
            # queue-claim, lowest-first now, no semantic test (CONTRACTS §4).
            grp3, reused = C.next_group_code(con, sub["id"])
            out["group_number_reused_from"] = reused
        if not hints.get("new_group_name"):
            out["blockers"].append("Name the new item group and confirm where it belongs.")

    # A slot the group declares but whose value we could not pin down is a
    # question for the operator - never an auto-allocated number. Skipping
    # this is how two unrelated invoice lines end up sharing one code.
    slots, pending_new = [], []
    for s in p3.get("slots", []):
        if s.get("code"):
            slots.append(s["code"])
            continue
        if not s.get("label"):
            slots.append(None)
            continue
        if s.get("value"):
            nxt = C.next_spec_code(con, group["id"], s["slot"]) if group else "01"
            slots.append(nxt)
            pending_new.append({"slot": s["slot"], "code": nxt, "value": s["value"]})
            s["proposed_code"] = nxt
        else:
            slots.append(None)

    vend = None
    v = p3.get("vendor")
    if v and v.get("value"):
        vend = v.get("code")
        if not vend and group:
            vend = C.next_spec_code(con, group["id"], 5)
            v["proposed_code"] = vend

    out["new_spec_values"] = pending_new
    out["new_group"] = new_group
    if "?" not in (head2 + sub2 + grp3):
        code = C.assemble(head2, sub2, grp3, slots, vend)
        out["code"] = code
        out["action"] = "create"
        out["segments"] = {"head": head2, "sub": sub2, "group": grp3, "specs": slots, "vendor": vend}
        if not C.code_is_free(con, code):
            out["blockers"].append(
                f"{code} is already issued - the spec combination is not unique. "
                "Adjust a spec value or pick the existing item.")
    return out


# ------------------------------------------------------------------ driver
def resolve_batch(con, matcher, payloads, user=None):
    """The LLM-first orchestrator. Rules always build the shortlist and hold
    the veto; an entire invoice is submitted to the LLM as ONE call, not one
    per line - exact phase-1 hits and cache hits never reach that call at
    all. Returns a list of proposals, one per payload, in order.
    """
    cfg = {"llm": getattr(matcher, "llm", {}) or {}, "match_threshold": matcher.threshold}
    threshold = L.get_threshold(con, cfg)

    results = [None] * len(payloads)
    contexts = []
    for i, payload in enumerate(payloads):
        lc = _build_line_context(con, matcher, payload)
        if lc["skip_llm"]:
            if lc["phase1"]["hit"] and not (lc["hints"].get("force_new")):
                results[i] = _existing_result(lc)
            else:
                decision = _apply_forced(lc, _rules_decision(lc, threshold))
                results[i] = _assemble_result(con, matcher, lc, decision)
            continue
        contexts.append((i, lc))

    if not contexts:
        return results

    use_llm = L.enabled(con, cfg)
    if not use_llm:
        mode = L.get_mode(con, cfg)
        if mode != "llm":
            print(f"[llm] fallback reason=no_key note=match.mode is '{mode}' - LLM not engaged")
        else:
            print("[llm] fallback reason=no_key note=no provider/key configured in settings")

    to_ask = []
    for i, lc in contexts:
        if not use_llm or not lc["candidates"]:
            decision = _apply_forced(lc, _rules_decision(lc, threshold))
            results[i] = _assemble_result(con, matcher, lc, decision)
            continue
        key = L.cache_key(normalize(lc["probe"]), _shortlist_signature(lc))
        cached = L.cache_get(con, key)
        if cached is not None:
            decision = _apply_forced(lc, _apply_answer(lc, cached, threshold))
            results[i] = _assemble_result(con, matcher, lc, decision)
            continue
        to_ask.append((i, lc, key))

    if to_ask:
        prompt = _build_batch_prompt([(i, lc) for i, lc, _ in to_ask])
        parsed, provider, reason = L.ask_json(con, prompt, cfg)
        answers = {}
        if parsed is not None:
            raw_lines = parsed.get("lines") if isinstance(parsed, dict) else parsed
            if isinstance(raw_lines, list):
                for item in raw_lines:
                    if isinstance(item, dict) and isinstance(item.get("line"), int):
                        answers[item["line"]] = item
        if parsed is None:
            print(f"[llm] fallback reason={reason} provider={provider} - "
                  f"{len(to_ask)} line(s) decided by rules instead")
        for i, lc, key in to_ask:
            ans = answers.get(i)
            if ans is not None:
                L.cache_put(con, key, lc["probe"], ans, provider)
                decision = _apply_answer(lc, ans, threshold)
            else:
                decision = _rules_decision(lc, threshold)
            decision = _apply_forced(lc, decision)
            results[i] = _assemble_result(con, matcher, lc, decision)

    return results


def resolve(con, matcher, payload, user=None):
    """Single-line convenience wrapper around resolve_batch() - kept because
    routes/create.py's existing /api/resolve endpoint calls it this way.
    Batch callers should use resolve_batch() directly so a whole invoice
    still costs one LLM call rather than one per line."""
    return resolve_batch(con, matcher, [payload], user=user)[0]


# ------------------------------------------------------------------- commit
def commit(con, matcher, proposal, user, push_erp=False, erp=None):
    """Persist a resolved proposal. Only called after the operator clicks
    Submit. Unchanged by the LLM-first rework: it only ever reads the group /
    slots / vendor a proposal already settled on, never re-decides anything,
    and never calls the LLM."""
    p2, p3 = proposal.get("phase2") or {}, proposal.get("phase3") or {}
    payload = proposal.get("input") or {}
    hints = payload.get("hints") or {}
    group = p2.get("group")

    if not group:
        head = p2.get("head")
        sub = p2.get("subhead")
        if not head:
            hname = hints.get("new_head_name")
            code2 = C.mint_head_code(con, hname)
            cur = con.execute("INSERT INTO head(name,code2) VALUES(?,?)", (hname, code2))
            head = {"id": cur.lastrowid, "name": hname, "code2": code2}
            log(con, user, "create-head", hname, {"code2": code2})
        if not sub:
            sname = hints.get("new_subhead_name")
            code2 = C.mint_subhead_code(con, head["id"], sname)
            cur = con.execute("INSERT INTO subhead(head_id,name,code2) VALUES(?,?,?)",
                              (head["id"], sname, code2))
            sub = {"id": cur.lastrowid, "name": sname, "code2": code2}
            log(con, user, "create-subhead", sname, {"code2": code2, "head": head["name"]})
        gname = hints.get("new_group_name")
        labels = hints.get("new_group_labels") or {}
        # C.claim_group_code is the concurrency-safe allocate+insert (Task 3):
        # BEGIN IMMEDIATE, re-check lowest-free inside it, retry once on
        # SQLITE_BUSY - so two operators committing in the same second never
        # receive the same group number.
        r = C.claim_group_code(con, sub["id"], gname, payload.get("uom"), labels)
        gid, code3, reused = r["id"], r["code3"], r["freed_from"]
        log(con, user, "create-group", gname,
            {"code3": code3, "subhead": sub["name"], "reused_vacancy": reused})
        group = {"id": gid, "name": gname, "code3": code3,
                 "head_code": head["code2"], "sub_code": sub["code2"], "labels": labels}

    # spec values, minting any that are new
    slot_codes, slot_ids = [], {}
    for s in p3.get("slots", []):
        if not s.get("label"):
            slot_codes.append(None)
            continue
        sid, scode = s.get("specval_id"), s.get("code")
        if not sid:
            val = s.get("chosen_value") or s.get("value") or hints.get(f"s{s['slot']}_new")
            if not val:
                slot_codes.append(None)
                continue
            scode = s.get("proposed_code") or C.next_spec_code(con, group["id"], s["slot"])
            cur = con.execute(
                "INSERT OR IGNORE INTO specval(grp_id,slot,value,code2) VALUES(?,?,?,?)",
                (group["id"], s["slot"], val, scode))
            sid = cur.lastrowid or con.execute(
                "SELECT id FROM specval WHERE grp_id=? AND slot=? AND value=?",
                (group["id"], s["slot"], val)).fetchone()["id"]
            log(con, user, "create-specval", val,
                {"group": group["name"], "slot": s["slot"], "code": scode})
        slot_codes.append(scode)
        slot_ids[s["slot"]] = sid

    vend_code, vend_id = None, None
    v = p3.get("vendor")
    if v and v.get("value"):
        vend_id, vend_code = v.get("specval_id"), v.get("code")
        if not vend_id:
            vend_code = v.get("proposed_code") or C.next_spec_code(con, group["id"], 5)
            cur = con.execute(
                "INSERT OR IGNORE INTO specval(grp_id,slot,value,code2) VALUES(?,5,?,?)",
                (group["id"], v["value"], vend_code))
            vend_id = cur.lastrowid or con.execute(
                "SELECT id FROM specval WHERE grp_id=? AND slot=5 AND value=?",
                (group["id"], v["value"])).fetchone()["id"]
            log(con, user, "create-vendor", v["value"],
                {"group": group["name"], "code": vend_code})

    code = C.assemble(group["head_code"], group["sub_code"], group["code3"], slot_codes, vend_code)
    if not C.code_is_free(con, code):
        raise ValueError(f"{code} already exists - refusing to overwrite")

    name = payload.get("name") or payload.get("text") or ""
    con.execute("""INSERT INTO item(code,name,name_norm,description,grp_id,s1,s2,s3,s4,vend,
                     uom,alt_uom,hsn,tax,origin,status,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'app','confirmed',?,?,?)""",
                (code, name, normalize(name), payload.get("description") or payload.get("text"),
                 group["id"], slot_ids.get(1), slot_ids.get(2), slot_ids.get(3), slot_ids.get(4),
                 vend_id, payload.get("uom"), payload.get("uom"), payload.get("hsn"),
                 payload.get("tax"), user, now(), now()))
    item_id = con.execute("SELECT id FROM item WHERE code=?", (code,)).fetchone()["id"]
    con.execute("INSERT OR REPLACE INTO code_ledger(code,item_id,state,ts,note) VALUES(?,?,?,?,?)",
                (code, item_id, "issued", now(), f"issued by {user}"))
    log(con, user, "issue-code", code, {"item": name, "group": group["name"]})

    result = {"code": code, "item_id": item_id, "erp": None}
    if push_erp and erp:
        result["erp"] = erp.create_item(code, name, group["name"], payload.get("uom") or "Nos",
                                        payload.get("hsn"), tax_template=payload.get("tax"))
        if result["erp"].get("ok"):
            con.execute("UPDATE item SET status='in_erp', erp_synced_at=?, frozen=1 WHERE id=?",
                        (now(), item_id))
            log(con, user, "erp-create", code, result["erp"])
    con.commit()
    return result
