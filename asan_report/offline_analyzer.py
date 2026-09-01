"""Deterministic, offline ASAN/LeakSanitizer report triage."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple


LEAK_START_RE = re.compile(
    r"^\s*(Direct|Indirect) leak of (\d+) byte\(s\) in "
    r"(\d+) object\(s\) allocated from:\s*$"
)
FRAME_RE = re.compile(r"^\s*#(\d+)\s+0x[0-9a-fA-F]+\s+(.*)$")
SUMMARY_RE = re.compile(
    r"SUMMARY:\s*AddressSanitizer:\s*(\d+) byte(?:\(s\)|s)? leaked in\s+"
    r"(\d+) allocation(?:\(s\)|s)?\."
)


BUILTIN_RULES = [
    {"id": "TXL-2716", "scope": "any",
     "tokens": ["gdk_pid_init", "GDK_PIDObjects.cpp"],
     "note": "Initialization runs once; objects live for process lifetime."},
    {"id": "TXL-2834", "scope": "site",
     "tokens": ["startup_task", "startup.cpp"],
     "note": "Startup-only allocation intentionally remains for program lifetime."},
    {"id": "TXL-2836", "scope": "any",
     "tokens": ["CListControl::addWideStaticDisplayItem", "CListControl.cpp"],
     "note": "Deallocation is handled by existing cleanup."},
    {"id": "TXL-2894", "scope": "any",
     "tokens": ["safely_update_comb_info_cstr"],
     "note": "Host loads once; this is a non-recurring startup allocation."},
    {"id": "TXL-2687", "scope": "any",
     "tokens": ["CConfigReader::_parseNode", "CConfigReader.cpp:458"],
     "note": "ClearOptions() and the destructor release the allocation."},
    {"id": "TXL-2756/TXL-2463", "scope": "any",
     "tokens": ["CreateCProgressiveSAPSource", "sap_progressive_source.cpp"],
     "note": "Intentional SAP progressive allocation retained by design."},
    {"id": "TXL-2463", "scope": "any",
     "tokens": ["read_game_info", "common.cpp"],
     "note": "Game metadata allocations are retained for process lifetime."},
    {"id": "TXL-4294", "scope": "any",
     "tokens": ["ComponentManager::getComponent"],
     "note": "A static singleton owns the component map for process lifetime."},
    {"id": "TXL-5998", "scope": "any",
     "tokens": ["EdgeLightManagerV2::getPlatformControlledStripIDs"],
     "note": "Cataloged external-library/Valgrind false positive."},
    {"id": "TXL-6000", "scope": "any",
     "tokens": ["soap_init", "G2SHostProc"],
     "note": "soap_done releases the stream and MHT state."},
    {"id": "TXL-6776", "scope": "any",
     "tokens": ["ProgressiveBaseX.cpp:117", "allocate_copy"],
     "note": "Cataloged sanitizer false positive."},
    {"id": "TXL-6779", "scope": "any",
     "tokens": ["AllocateMemory<SLOT_GLOBALS>", "on_powerup_game_init", "basexnull.cpp"],
     "note": "Documented game-lifetime allocation; the basexnull.cpp site is required."},
    {"id": "OZGAMEKBJ-611", "scope": "any",
     "tokens": ["udev_device_get_sysattr_value", "udev_device_new_from_syspath"],
     "note": "Returned strings are borrowed and udev cleanup is handled."},
    {"id": "TXL-7079", "scope": "any", "force_ambiguous": True,
     "tokens": ["usbdisp_init", "_createDC"],
     "note": "Conditional catalog entry; the lifetime/cleanup case needs manual confirmation."},
    {"id": "TXL-7129", "scope": "any",
     "tokens": ["create_payout_screen", "basehelp.cpp"],
     "note": "Catalog states that cleanup exists for this allocation."},
    {"id": "TXL-6823", "scope": "any",
     "tokens": ["gos_create_thread", "GameContextServer"],
     "note": "Cataloged sanitizer false positive in custom stack-unwind handling."},
    {"id": "TXL-8227", "scope": "any",
     "tokens": ["EdgeLightManagerV2"],
     "note": "Edge-light singleton intentionally exists for software lifetime."},
    {"id": "TXL-8252", "scope": "any",
     "tokens": ["HelixLighting.DLL"],
     "note": "Cross-module cleanup is invisible to the sanitizer."},
    {"id": "TXL-8675", "scope": "any",
     "tokens": ["IrrPromptInit", "irr_prompt.cpp"],
     "note": "Receiver is managed by process-lifetime IrrlichtDevice objects."},
    {"id": "TXL-8493", "scope": "any",
     "tokens": ["Gen7G2SMain::add_g2sevent", "g2s_addEvent"],
     "note": "The G2S engine owns and deletes the allocation."},
    {"id": "TXL-9678", "scope": "any",
     "tokens": ["gdk_get_game_info", "read_game_info"],
     "note": "Catalog identifies different static structures, not a double-free."},
    {"id": "TXL-11176/TXL-17894", "scope": "any",
     "tokens": ["ProgressiveBaseX", "allocate_copy"],
     "note": "One-time legacy LP/audit allocation retained for game lifetime."},
    {"id": "GGNF-LSBJPS-1636", "scope": "any",
     "tokens": ["G2SATIDataHandlerProc::send_atiDataInfo", "form_sendMsg"],
     "note": "Ownership transfers to the G2S message path."},
    {"id": "TXL-10728", "scope": "any",
     "tokens": ["qcom3::Server", "CRYPTO_zalloc"],
     "note": "Catalog attributes the allocation to external library behavior."},
    {"id": "TXL-17895", "scope": "any",
     "tokens": ["GetOpenFileNameW", "COMDLG32.dll"],
     "note": "Allocation originates in Windows common-dialog internals."},
    {"id": "TXL-17896", "scope": "any",
     "tokens": ["SHFileOperation", "urlmon.dll"],
     "note": "Allocation originates in Windows system components."},
]


PROJECT_CONFIRMED_RULES = [
    {"id": "CONFIRMED-2026-08-31-1", "scope": "any",
     "tokens": ["AllocateMemory<SLOT_GLOBALS>", "on_powerup_game_init"],
     "note": "Project-confirmed process-lifetime allocation (chat, no ticket reference)."},
    {"id": "CONFIRMED-2026-08-31-2", "scope": "any",
     "tokens": ["gos_getmem", "_avlmalloc", "CreateSysProp"],
     "note": "Project-confirmed startup property allocation (chat, no ticket reference)."},
    {"id": "CONFIRMED-2026-09-01-1", "scope": "external",
     "tokens": ["libvorbisfile.so"],
     "note": "Project-confirmed libvorbisfile external-only false positive "
             "(chat, no ticket reference)."},
]


EXTERNAL_FAMILY_RULE = {
    "id": "OZGAMEKBP-4254",
    "libs": ["libIrrlicht.so", "libvorbis.so", "libnvidia", "libogg"],
    "note": "External-library-only stack cataloged as a false positive.",
}


def _empty_stats() -> dict:
    return {
        "reports_started": 0,
        "reports_completed": 0,
        "reports_dropped_incomplete": 0,
        "dropped_bytes": 0,
        "dropped_objects": 0,
        "summary_total_bytes": 0,
        "summary_total_objects": 0,
        "sanitizer_suppressions_present": False,
    }


def parse_lines(lines: Iterable[str]) -> dict:
    """Parse and aggregate complete ASAN/LSAN reports from an iterable."""
    aggregate: Dict[Tuple[str, Tuple[str, ...]], dict] = {}
    stats = _empty_stats()
    pending: List[Tuple[str, Tuple[str, ...], int, int]] = []
    category: Optional[str] = None
    block_bytes = 0
    block_objects = 0
    frames: List[str] = []
    in_block = False
    report_active = False

    def finish_block():
        nonlocal category, block_bytes, block_objects, frames, in_block
        if category is not None:
            pending.append((category, tuple(frames), block_bytes, block_objects))
        category = None
        block_bytes = 0
        block_objects = 0
        frames = []
        in_block = False

    def commit_report():
        for leak_category, leak_frames, byte_count, object_count in pending:
            key = (leak_category, leak_frames)
            entry = aggregate.setdefault(key, {
                "category": leak_category,
                "objects": 0,
                "bytes": 0,
                "occurrences": 0,
                "frames": list(leak_frames),
            })
            entry["objects"] += object_count
            entry["bytes"] += byte_count
            entry["occurrences"] += 1
        pending.clear()

    def drop_report():
        stats["reports_dropped_incomplete"] += 1
        stats["dropped_bytes"] += sum(row[2] for row in pending)
        stats["dropped_objects"] += sum(row[3] for row in pending)
        pending.clear()

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if "Suppressions used:" in line:
            stats["sanitizer_suppressions_present"] = True

        if "ERROR: LeakSanitizer" in line:
            finish_block()
            if report_active:
                drop_report()
            stats["reports_started"] += 1
            report_active = True
            continue

        summary = SUMMARY_RE.search(line)
        if summary:
            if not report_active:
                stats["reports_started"] += 1
                report_active = True
            finish_block()
            commit_report()
            stats["summary_total_bytes"] += int(summary.group(1))
            stats["summary_total_objects"] += int(summary.group(2))
            stats["reports_completed"] += 1
            report_active = False
            continue

        leak = LEAK_START_RE.match(line)
        if leak:
            if not report_active:
                stats["reports_started"] += 1
                report_active = True
            finish_block()
            category = leak.group(1)
            block_bytes = int(leak.group(2))
            block_objects = int(leak.group(3))
            frames = []
            in_block = True
            continue

        if in_block:
            frame = FRAME_RE.match(line)
            if frame:
                frames.append(frame.group(2).strip())
            else:
                finish_block()

    finish_block()
    if report_active:
        drop_report()

    signatures = sorted(aggregate.values(), key=lambda row: -row["bytes"])
    return {"stats": stats, "signatures": signatures}


def parse_text(text: str) -> dict:
    return parse_lines(text.splitlines())


def parse_file(path) -> dict:
    with open(path, "r", encoding="utf-8", errors="replace") as stream:
        return parse_lines(stream)


def _external_family_match(frames: List[str], libraries: List[str]) -> bool:
    body = frames[1:]
    if not body:
        return False
    saw_library = False
    for frame in body:
        if frame.strip() == "(<unknown module>)":
            continue
        if any(library in frame for library in libraries):
            saw_library = True
            continue
        return False
    return saw_library


def _match_rule(rule: dict, frames: List[str]):
    if rule["scope"] == "external":
        verdict = "exact" if _external_family_match(frames, rule["tokens"]) else None
        return verdict, []
    site = frames[1] if len(frames) > 1 else ""
    whole_stack = " | ".join(frames[1:])
    search_text = site if rule["scope"] == "site" else whole_stack
    missing = [token for token in rule["tokens"] if token not in search_text]

    if rule.get("force_ambiguous"):
        return ("ambiguous", []) if not missing else (None, [])
    if not missing:
        return "exact", []
    if len(rule["tokens"]) >= 2 and len(missing) == 1:
        return "ambiguous", missing
    return None, []


def _classify(entry: dict, rules: List[dict]):
    frames = entry["frames"]
    if _external_family_match(frames, EXTERNAL_FAMILY_RULE["libs"]):
        return "suppressed", EXTERNAL_FAMILY_RULE["id"], EXTERNAL_FAMILY_RULE["note"]

    ambiguous = None
    for rule in rules:
        verdict, missing = _match_rule(rule, frames)
        if verdict == "exact":
            return "suppressed", rule["id"], rule["note"]
        if verdict == "ambiguous" and ambiguous is None:
            note = rule["note"]
            if missing:
                note += f" Missing identifying token(s): {', '.join(missing)}."
            ambiguous = ("uncertain", rule["id"], note)
    if ambiguous:
        return ambiguous
    if len(frames) < 2:
        return "uncertain", "DATA-STACK", "No credible allocation site was captured."
    if entry["category"].lower() == "indirect":
        return (
            "uncertain", "INDIRECT-OWNER",
            "Indirect leak requires confirmation of its owning direct leak.",
        )
    return "candidate", None, "No configured false-positive rule matched."


def _totals(rows: List[dict]) -> dict:
    return {
        "signatures": len(rows),
        "objects": sum(row["objects"] for row in rows),
        "bytes": sum(row["bytes"] for row in rows),
    }


def analyze_parsed(parsed: dict, extra_rules: Optional[List[dict]] = None) -> dict:
    rules = list(BUILTIN_RULES) + list(PROJECT_CONFIRMED_RULES)
    if extra_rules:
        rules.extend(extra_rules)

    result = {
        "stats": parsed["stats"],
        "candidate": [],
        "suppressed": [],
        "uncertain": [],
    }
    for source in parsed["signatures"]:
        entry = dict(source)
        verdict, rule_id, note = _classify(entry, rules)
        entry["rule_id"] = rule_id
        entry["reason"] = note
        result[verdict].append(entry)

    for key in ("candidate", "suppressed", "uncertain"):
        result[key].sort(key=lambda row: -row["bytes"])
    result["totals"] = {
        key: _totals(result[key])
        for key in ("candidate", "suppressed", "uncertain")
    }

    parsed_bytes = sum(total["bytes"] for total in result["totals"].values())
    reported_bytes = parsed["stats"]["summary_total_bytes"]
    if parsed["stats"]["reports_completed"] == 0:
        reconciliation = "not possible: no complete sanitizer SUMMARY was found"
    elif parsed_bytes == reported_bytes:
        reconciliation = "matched"
    else:
        reconciliation = (
            f"partial: parsed {parsed_bytes:,} bytes versus "
            f"{reported_bytes:,} bytes reported"
        )
    result["reconciliation"] = reconciliation
    return result


def analyze_text(text: str, extra_rules: Optional[List[dict]] = None) -> dict:
    return analyze_parsed(parse_text(text), extra_rules)


def analyze_file(path, extra_rules: Optional[List[dict]] = None) -> dict:
    return analyze_parsed(parse_file(path), extra_rules)


def _human_bytes(value: int) -> str:
    if value < 1024:
        return f"{value:,} B"
    size = float(value)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        size /= 1024
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit} ({value:,} bytes)"


def _count(value: int, singular: str) -> str:
    return f"{value:,} {singular if value == 1 else singular + 's'}"


def format_analysis(result: dict) -> str:
    """Render a concise, human-readable report for the desktop UI."""
    totals = result["totals"]
    stats = result["stats"]
    candidate_count = totals["candidate"]["signatures"]
    uncertain_count = totals["uncertain"]["signatures"]
    if candidate_count:
        verdict = (
            f"RESULT: {_count(candidate_count, 'possible leak pattern')} "
            "needs review."
            if candidate_count == 1 else
            f"RESULT: {_count(candidate_count, 'possible leak pattern')} need review."
        )
    elif uncertain_count:
        verdict = (
            "RESULT: No likely direct leaks found, but "
            f"{_count(uncertain_count, 'finding')} needs manual review."
            if uncertain_count == 1 else
            "RESULT: No likely direct leaks found, but "
            f"{_count(uncertain_count, 'finding')} need manual review."
        )
    else:
        verdict = "RESULT: No actionable leaks found."

    lines = [
        "ASAN LEAK ANALYSIS",
        "=" * 78,
        verdict,
        "Candidate does not mean validated; human or LLM review is still required.",
        "",
        "REPORT SUMMARY",
        "-" * 78,
        f"Complete reports:       {stats['reports_completed']:,} of "
        f"{stats['reports_started']:,}",
        f"Possible leaks:         {_count(candidate_count, 'pattern')}, "
        f"{_count(totals['candidate']['objects'], 'object')}, "
        f"{_human_bytes(totals['candidate']['bytes'])}",
        f"Known false positives:  "
        f"{_count(totals['suppressed']['signatures'], 'pattern')}, "
        f"{_count(totals['suppressed']['objects'], 'object')}, "
        f"{_human_bytes(totals['suppressed']['bytes'])}",
        f"Needs manual review:    {_count(uncertain_count, 'pattern')}, "
        f"{_count(totals['uncertain']['objects'], 'object')}, "
        f"{_human_bytes(totals['uncertain']['bytes'])}",
        "",
        "POSSIBLE LEAKS - INVESTIGATE THESE",
        "-" * 78,
    ]
    if not result["candidate"]:
        lines.append("None found.")
    for index, entry in enumerate(result["candidate"], 1):
        per_object = entry["bytes"] / entry["objects"] if entry["objects"] else 0
        lines.extend([
            f"{index}. {entry['category']} leak - {_human_bytes(entry['bytes'])} "
            f"in {_count(entry['objects'], 'object')}",
            f"   Allocation site: "
            f"{entry['frames'][1] if len(entry['frames']) > 1 else 'Unknown'}",
            f"   Average object:  {_human_bytes(round(per_object))}",
            f"   Seen:            {_count(entry['occurrences'], 'report occurrence')}",
            "   Why listed:      No known false-positive rule matched.",
        ])
        if len(entry["frames"]) > 2:
            lines.append("   Call path:")
            lines.extend(f"      -> {frame}" for frame in entry["frames"][2:7])
        lines.append("")

    lines.extend([
        "NEEDS MANUAL REVIEW",
        "-" * 78,
    ])
    if not result["uncertain"]:
        lines.append("None.")
    for index, entry in enumerate(result["uncertain"], 1):
        lines.extend([
            f"{index}. {entry['category']} finding - {_human_bytes(entry['bytes'])} "
            f"in {_count(entry['objects'], 'object')}",
            f"   Allocation site: "
            f"{entry['frames'][1] if len(entry['frames']) > 1 else 'Unknown'}",
            f"   Review reason:   {entry['reason']}",
        ])

    lines.extend(["", "KNOWN FALSE POSITIVES - EXCLUDED", "-" * 78])
    if not result["suppressed"]:
        lines.append("None.")
    suppressed_by_rule = {}
    for entry in result["suppressed"]:
        group = suppressed_by_rule.setdefault(entry["rule_id"], {
            "signatures": 0, "objects": 0, "bytes": 0,
            "reason": entry["reason"],
        })
        group["signatures"] += 1
        group["objects"] += entry["objects"]
        group["bytes"] += entry["bytes"]
    for rule_id, group in sorted(
        suppressed_by_rule.items(), key=lambda item: -item[1]["bytes"]
    ):
        lines.extend([
            f"- {rule_id}: {_count(group['signatures'], 'pattern')}, "
            f"{_count(group['objects'], 'object')}, {_human_bytes(group['bytes'])}",
            f"  Reason: {group['reason']}",
        ])

    lines.extend([
        "",
        "DATA QUALITY",
        "-" * 78,
        "Totals check: " + (
            "Parsed findings match the sanitizer summaries."
            if result["reconciliation"] == "matched" else result["reconciliation"]
        ),
        f"Sanitizer reported: {_count(stats['summary_total_objects'], 'allocation')}, "
        f"{_human_bytes(stats['summary_total_bytes'])}",
        f"Incomplete reports ignored: "
        f"{_count(stats['reports_dropped_incomplete'], 'report')}, "
        f"{_count(stats['dropped_objects'], 'object')}, "
        f"{_human_bytes(stats['dropped_bytes'])}",
        "ASAN suppression section present: "
        + ("yes" if stats["sanitizer_suppressions_present"] else "no"),
    ])
    return "\n".join(lines) + "\n"
