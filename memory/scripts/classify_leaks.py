#!/usr/bin/env python3
"""
Apply the asan-leak-triage false-positive catalog to the aggregated
signatures produced by parse_asan.py, and print the Valid/Suppressed/
Uncertain breakdown in the shape the skill's report needs.

Usage:
    python3 classify_leaks.py agg.json [--extra-rules rules.json]

`agg.json` is the output of parse_asan.py.

`--extra-rules` optionally points at a JSON file with the same shape as
BUILTIN_RULES below (a list of rule dicts) for entries pulled from a
supplied Confluence page that aren't in the built-in catalog yet. Extra
rules are checked with the same precedence as built-in ones (id order,
first exact match wins).

Each signature (one row per distinct normalized allocation stack) is
classified as:
  - suppressed: an "exact" rule match -- cite the rule id in the report's
    "False-Positive Rules Applied" section, do not list in Valid Leaks.
  - uncertain: a rule's identifying tokens are present but not at the
    actual allocation site (frames[1]), or the rule is documented as
    conditional in the catalog -- do not suppress, do not count as valid
    either; list in "Uncertainty and Data Quality" for manual review.
  - valid: no rule matched at all -- goes in the Valid Leaks table.

This encodes the same judgment calls made during manual triage (e.g. a
`AllocateMemory<SLOT_GLOBALS>`/`on_powerup_game_init` stack only matches
TXL-6779 when the file is basexnull.cpp, not some other call site; a
`startup_task`/`startup.cpp` rule only applies when that frame IS the
allocation site, not merely an ancestor several frames up a stack whose
real allocator is something else entirely).
"""
import argparse
import json
import sys

# scope="any"  -> all tokens must appear anywhere in frames[1:] (excludes the
#                 interceptor frame #0, which is always malloc/calloc/realloc
#                 boilerplate and never identifies anything).
# scope="site" -> all tokens must appear within frames[1] alone (the direct
#                 allocation call site). Use this for rules whose identifying
#                 text is a generic-sounding function/file that could
#                 legitimately appear several frames up an unrelated stack
#                 (e.g. startup_task/startup.cpp, which is an ancestor of
#                 nearly everything that runs at boot).
# force_ambiguous=True -> the catalog itself documents this rule as
#                 conditional ("suppress only when...") -- a token match
#                 should never auto-suppress, only flag for manual review.
BUILTIN_RULES = [
    {"id": "TXL-2716", "scope": "any",
     "tokens": ["gdk_pid_init", "GDK_PIDObjects.cpp"],
     "note": "Initialization runs once; objects live for process lifetime."},
    {"id": "TXL-2834", "scope": "site",
     "tokens": ["startup_task", "startup.cpp"],
     "note": "Startup-only allocation intentionally retained for program lifetime. Site-scoped: only applies when startup_task/startup.cpp IS the allocating frame, not merely an ancestor."},
    {"id": "TXL-2836", "scope": "any",
     "tokens": ["CListControl::addWideStaticDisplayItem", "CListControl.cpp"],
     "note": "Deallocation handled by existing cleanup (also covers the TXL-7079/TXL-2836 audit-menu-lockup variant)."},
    {"id": "TXL-2894", "scope": "any",
     "tokens": ["safely_update_comb_info_cstr"],
     "note": "Host loads once; non-recurring startup case."},
    {"id": "TXL-2687", "scope": "any",
     "tokens": ["CConfigReader::_parseNode", "CConfigReader.cpp:458"],
     "note": "ClearOptions() and the destructor release the allocation."},
    {"id": "TXL-2756/TXL-2463", "scope": "any",
     "tokens": ["CreateCProgressiveSAPSource", "sap_progressive_source.cpp"],
     "note": "Intentional SAP progressive allocation retained by design."},
    {"id": "TXL-2463", "scope": "any",
     "tokens": ["read_game_info", "common.cpp"],
     "note": "Game name/version allocations retained for process lifetime (libxml2-backed)."},
    {"id": "TXL-4294", "scope": "any",
     "tokens": ["ComponentManager::getComponent"],
     "note": "Static singleton owns the map for process lifetime."},
    {"id": "TXL-5998", "scope": "any",
     "tokens": ["EdgeLightManagerV2::getPlatformControlledStripIDs"],
     "note": "Same documented Valgrind issue as TXL-5999."},
    {"id": "TXL-6000", "scope": "any",
     "tokens": ["soap_init", "G2SHostProc"],
     "note": "soap_done cleanup releases d_stream and MHT state."},
    {"id": "TXL-6776", "scope": "any",
     "tokens": ["ProgressiveBaseX.cpp:117", "allocate_copy"],
     "note": "Documented false positive (stack-use-after-scope)."},
    {"id": "TXL-6779", "scope": "any",
     "tokens": ["AllocateMemory<SLOT_GLOBALS>", "on_powerup_game_init", "basexnull.cpp"],
     "note": "Documented false positive -- requires basexnull.cpp specifically; a different call site (e.g. gamemain.cpp) is NOT this rule."},
    {"id": "OZGAMEKBJ-611", "scope": "any",
     "tokens": ["udev_device_get_sysattr_value", "udev_device_new_from_syspath"],
     "note": "Returned strings are borrowed; udev_device_unref cleanup documented as handled."},
    {"id": "TXL-7079", "scope": "any", "force_ambiguous": True,
     "tokens": ["usbdisp_init", "_createDC"],
     "note": "Catalog: suppress only when it matches the documented process-lifetime/cleanup case -- always needs manual confirmation."},
    {"id": "TXL-7129", "scope": "any",
     "tokens": ["create_payout_screen", "basehelp.cpp"],
     "note": "Catalog says cleanup exists although sanitizer reports a direct leak."},
    {"id": "TXL-6823", "scope": "any",
     "tokens": ["gos_create_thread", "GameContextServer"],
     "note": "Sanitizer report classified as false positive (stack-buffer-overflow context, not a real leak)."},
    {"id": "TXL-8227", "scope": "any",
     "tokens": ["EdgeLightManagerV2"],
     "note": "Edge-light object intentionally exists for software lifetime."},
    {"id": "TXL-8252", "scope": "any",
     "tokens": ["HelixLighting.DLL"],
     "note": "Cross-module deallocation invisible to the sanitizer (Windows VLD)."},
    {"id": "TXL-8675", "scope": "any",
     "tokens": ["IrrPromptInit", "irr_prompt.cpp"],
     "note": "Receiver managed by process-lifetime IrrlichtDevice objects."},
    {"id": "TXL-8493", "scope": "any",
     "tokens": ["Gen7G2SMain::add_g2sevent", "g2s_addEvent"],
     "note": "G2S engine owns and deletes the allocation."},
    {"id": "TXL-9678", "scope": "any",
     "tokens": ["gdk_get_game_info", "read_game_info"],
     "note": "Two different static structures/addresses, not a double-free."},
    {"id": "TXL-11176/TXL-17894", "scope": "any",
     "tokens": ["ProgressiveBaseX", "allocate_copy"],
     "note": "One-time legacy LP/audit allocation retained for game lifetime."},
    {"id": "GGNF-LSBJPS-1636", "scope": "any",
     "tokens": ["G2SATIDataHandlerProc::send_atiDataInfo", "form_sendMsg"],
     "note": "Ownership transferred to the G2S message path; cleanup handles success/failure."},
    {"id": "TXL-10728", "scope": "any",
     "tokens": ["qcom3::Server", "CRYPTO_zalloc"],
     "note": "Attributed to external Boost/library behavior, not platform code."},
    {"id": "TXL-17895", "scope": "any",
     "tokens": ["GetOpenFileNameW", "COMDLG32.dll"],
     "note": "Allocation originates in Windows common-dialog internals."},
    {"id": "TXL-17896", "scope": "any",
     "tokens": ["SHFileOperation", "urlmon.dll"],
     "note": "Allocation originates in Windows system components."},
]

# Suppressions confirmed directly by the project team in chat, NOT sourced
# from the "Details of Memory Leaks (Linux)" PDF. Kept in a separate list
# (rather than folded into BUILTIN_RULES) so the report's "False-Positive
# Rules Applied" section can always show whether a suppression traces back
# to the official catalog or to an ad-hoc chat confirmation -- the latter
# should ideally be added to the real Confluence catalog for traceability
# by engineers without this session's context.
PROJECT_CONFIRMED_RULES = [
    {"id": "CONFIRMED-2026-08-31-1", "scope": "any",
     "tokens": ["AllocateMemory<SLOT_GLOBALS>", "on_powerup_game_init"],
     "note": "User-confirmed false positive (chat, 2026-08-31, no ticket/Confluence reference given): "
             "one-time SLOT_GLOBALS allocation at game init, analogous rationale to TXL-6779 but not "
             "restricted to the basexnull.cpp call site -- also covers the gamemain.cpp on_powerup_game_init path."},
    {"id": "CONFIRMED-2026-08-31-2", "scope": "any",
     "tokens": ["gos_getmem", "_avlmalloc", "CreateSysProp"],
     "note": "User-confirmed false positive (chat, 2026-08-31, no ticket/Confluence reference given): "
             "one-time AVL/video-screen default-property allocations made once per process via "
             "CreateSysProp/_avlmalloc/gos_getmem during startup (SetAvlDefaults and "
             "setup_dual_video_screens call sites), retained for process lifetime."},
    {"id": "CONFIRMED-2026-09-01-1", "scope": "external",
     "tokens": ["libvorbisfile.so"],
     "note": "User-confirmed false positive (chat, 2026-09-01, no ticket/Confluence "
             "reference given): suppress only external-library-only libvorbisfile stacks."},
]

# OZGAMEKBP-4254 is structural rather than a token match: suppress when every
# frame past the interceptor is either unresolved ("<unknown module>") or
# resolves into one of these external libraries, AND at least one frame
# names one of them (so a fully-unknown, unattributed stack is NOT silently
# suppressed -- that would be validity without evidence).
#
# The PDF-derived list remains exact. libvorbisfile.so is handled separately
# by the project-confirmed external-only rule above.
EXTERNAL_FAMILY_RULE = {
    "id": "OZGAMEKBP-4254",
    "libs": ["libIrrlicht.so", "libvorbis.so", "libnvidia", "libogg"],
    "note": "Untraceable / external-library-only report; catalog classifies as false positive.",
}


def load_rules(extra_rules_path):
    rules = list(BUILTIN_RULES) + list(PROJECT_CONFIRMED_RULES)
    if extra_rules_path:
        with open(extra_rules_path, "r", encoding="utf-8") as f:
            rules.extend(json.load(f))
    return rules


def is_external_family_match(frames, libs):
    body = frames[1:]
    if not body:
        return False
    saw_lib = False
    for fr in body:
        if fr.strip() == "(<unknown module>)":
            continue
        if any(lib in fr for lib in libs):
            saw_lib = True
            continue
        return False
    return saw_lib


def match_rule(rule, frames):
    """Returns (verdict, missing_tokens) where verdict is one of
    "exact" / "ambiguous" / None (no match at all).

    scope="site" evaluates tokens ONLY against frames[1] (the actual
    allocation call site) -- a rule like TXL-2834 ("startup_task" /
    "startup.cpp") would otherwise match almost every stack in a codebase
    where startup_task is the top-level dispatcher for boot-time init,
    since those words appear as an ANCESTOR frame of nearly everything.
    If neither token is present at the real allocation site, this is not
    a match at all (not even ambiguous) -- a shared ancestor frame doesn't
    make two unrelated allocations related.

    scope="any" evaluates tokens across the whole stack (frames[1:]),
    appropriate for rules whose identifying tokens are specific function/
    file names that may legitimately span two adjacent frames (e.g. a
    function name at frame 1 and its containing file at frame 2).

    For either scope, matching all-but-one token (when the rule has 2+
    tokens) is reported as "ambiguous" -- a near-miss worth a human's
    attention (e.g. TXL-6779's function names match but the file doesn't)
    -- rather than silently either suppressing or ignoring it.
    """
    tokens = rule["tokens"]
    if rule["scope"] == "external":
        verdict = "exact" if is_external_family_match(frames, tokens) else None
        return verdict, []
    site = frames[1] if len(frames) > 1 else ""
    whole = " | ".join(frames[1:])
    scope_text = site if rule["scope"] == "site" else whole

    matched = [t for t in tokens if t in scope_text]
    missing = [t for t in tokens if t not in scope_text]
    n = len(tokens)

    if rule.get("force_ambiguous"):
        if len(matched) == n:
            return "ambiguous", []
        return None, []

    if len(matched) == n:
        return "exact", []
    if n >= 2 and len(matched) == n - 1:
        return "ambiguous", missing
    return None, []


def classify(entry, rules):
    frames = entry["frames"]
    if is_external_family_match(frames, EXTERNAL_FAMILY_RULE["libs"]):
        return "suppressed", EXTERNAL_FAMILY_RULE["id"], EXTERNAL_FAMILY_RULE["note"]
    ambiguous_hit = None
    ambiguous_missing = []
    for rule in rules:
        verdict, missing = match_rule(rule, frames)
        if verdict == "exact":
            return "suppressed", rule["id"], rule["note"]
        if verdict == "ambiguous" and ambiguous_hit is None:
            ambiguous_hit, ambiguous_missing = rule, missing
    if ambiguous_hit:
        note = ambiguous_hit["note"]
        if ambiguous_missing:
            note += f" [near-miss: matched all identifying tokens except {ambiguous_missing!r}]"
        return "uncertain", ambiguous_hit["id"], note
    return "valid", None, None


def fmt_frames(frames, max_frames=6):
    shown = frames[1:1 + max_frames]  # skip the interceptor frame
    return " -> ".join(shown) if shown else "(no frames captured)"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("agg_json", help="Path to the agg.json produced by parse_asan.py")
    ap.add_argument("--extra-rules", default=None, help="Optional JSON file of additional rules (same shape as BUILTIN_RULES)")
    args = ap.parse_args()

    with open(args.agg_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    rules = load_rules(args.extra_rules)
    stats = data["stats"]
    signatures = data["signatures"]

    valid, suppressed, uncertain = [], [], []
    for entry in signatures:
        verdict, rule_id, note = classify(entry, rules)
        entry["_rule_id"] = rule_id
        entry["_rule_note"] = note
        if verdict == "valid":
            valid.append(entry)
        elif verdict == "suppressed":
            suppressed.append(entry)
        else:
            uncertain.append(entry)

    valid.sort(key=lambda x: -x["bytes"])
    uncertain.sort(key=lambda x: -x["bytes"])

    def totals(rows):
        return sum(r["bytes"] for r in rows), sum(r["objects"] for r in rows)

    vb, vo = totals(valid)
    sb, so = totals(suppressed)
    ub, uo = totals(uncertain)

    print("=" * 70)
    print(f"VALID:      {len(valid):4d} signature(s)  objects={vo:>14,}  bytes={vb:>16,}")
    print(f"SUPPRESSED: {len(suppressed):4d} signature(s)  objects={so:>14,}  bytes={sb:>16,}")
    print(f"UNCERTAIN:  {len(uncertain):4d} signature(s)  objects={uo:>14,}  bytes={uo and ub:>16,}")
    print("=" * 70)

    print("\n--- VALID LEAKS (candidate table rows, L1..) ---")
    for i, e in enumerate(valid, 1):
        per_obj = e["bytes"] / e["objects"] if e["objects"] else 0
        print(f"L{i}: {e['category']:8s} objects={e['objects']:>8,} bytes={e['bytes']:>14,} "
              f"bytes/obj={per_obj:.1f} occ={e['occurrences']}")
        print(f"     site: {fmt_frames(e['frames'])}")

    print("\n--- SUPPRESSED (grouped by rule) ---")
    by_rule = {}
    for e in suppressed:
        by_rule.setdefault(e["_rule_id"], []).append(e)
    for rule_id, rows in sorted(by_rule.items(), key=lambda kv: -sum(r["bytes"] for r in kv[1])):
        b, o = totals(rows)
        print(f"{rule_id}: {len(rows)} signature(s)  objects={o:,}  bytes={b:,}")
        for e in rows[:5]:
            print(f"    - {fmt_frames(e['frames'], max_frames=2)}  (bytes={e['bytes']:,}, objects={e['objects']:,})")
        if len(rows) > 5:
            print(f"    ... and {len(rows) - 5} more signature(s) under this rule")

    print("\n--- UNCERTAIN (needs manual review, U1..) ---")
    for i, e in enumerate(uncertain, 1):
        print(f"U{i}: near-match to {e['_rule_id']} ({e['_rule_note']})")
        print(f"     objects={e['objects']:,} bytes={e['bytes']:,}")
        print(f"     site: {fmt_frames(e['frames'])}")

    print("\n--- RECONCILIATION ---")
    print(f"reports_started={stats['reports_started']} reports_completed={stats['reports_completed']} "
          f"dropped_incomplete={stats['reports_dropped_incomplete']}")
    if stats["reports_dropped_incomplete"]:
        print(f"  dropped {stats['dropped_bytes']:,} byte(s) / {stats['dropped_objects']:,} object(s) "
              f"from incomplete trailing report(s) -- excluded entirely, not merged.")
    print(f"parsed total (valid+suppressed+uncertain): bytes={vb+sb+ub:,} objects={vo+so+uo:,}")
    print(f"SUMMARY-line total (completed reports only): bytes={stats['summary_total_bytes']:,} "
          f"objects={stats['summary_total_objects']:,}")


if __name__ == "__main__":
    main()
