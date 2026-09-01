#!/usr/bin/env python3
"""
Stream-parse a large ASAN/LeakSanitizer report and aggregate leak records by
normalized allocation-stack signature.

Why this exists: raw ASAN/LSAN reports from repeated/batch runs can be
hundreds of MB and contain hundreds of thousands of individual leak blocks
that are mostly repeats of a small number of distinct allocation sites
(one instance per process run). Reading the file as text does not scale;
this script streams it once, groups identical stacks, and writes a compact
JSON summary for classify_leaks.py to consume.

Usage:
    python3 parse_asan.py <report.txt> [--out agg.json]

Output JSON shape:
{
  "stats": {
    "reports_started": int,        # count of "...ERROR: LeakSanitizer..." headers seen
    "reports_completed": int,      # count of reports that reached a SUMMARY: line
    "reports_dropped_incomplete": int,
    "dropped_bytes": int,          # leak bytes belonging to dropped incomplete report(s)
    "dropped_objects": int,
    "summary_total_bytes": int,    # sum of the "SUMMARY: ... N byte(s) leaked" lines
    "summary_total_objects": int
  },
  "signatures": [
    {"category": "Direct"|"Indirect", "bytes": int, "objects": int,
     "occurrences": int, "frames": [str, ...]},
    ...
  ]
}

Each "frames" list keeps frame #0 (the malloc/calloc/realloc interceptor)
followed by the allocation call site and its callers, with the raw
"#N 0xADDRESS" prefix stripped so that identical call sites collapse to one
signature even when ASLR/addresses differ across runs. frames[1] is the
direct allocation site; frames[2:] are callers going outward.

A report whose final leak block or final SUMMARY line is cut off mid-line
(e.g. a batch run that was captured while still writing, or hit a size/time
limit) is dropped entirely rather than partially merged into the
aggregates -- see reports_dropped_incomplete / dropped_bytes above. This
mirrors project guidance: an incomplete trailing report should be ignored,
not folded in with a footnote.
"""
import argparse
import json
import re
import sys

LEAK_START_RE = re.compile(r'^(Direct|Indirect) leak of (\d+) byte\(s\) in (\d+) object\(s\) allocated from:$')
FRAME_RE = re.compile(r'^\s*#(\d+)\s+0x[0-9a-fA-F]+\s+(.*)$')
SUMMARY_RE = re.compile(r'^SUMMARY: AddressSanitizer: (\d+) byte\(s\) leaked in (\d+) allocation\(s\)\.')


def parse(path):
    agg = {}  # (category, frames_tuple) -> {"objects", "bytes", "occurrences", "frames"}
    reports_started = 0
    reports_completed = 0
    summary_total_bytes = 0
    summary_total_objects = 0

    # blocks collected for the report currently being read, committed on SUMMARY
    pending_report_blocks = []

    cur_category = None
    cur_bytes = None
    cur_objects = None
    cur_frames = []
    in_block = False

    def finalize_current_block():
        nonlocal cur_category, cur_bytes, cur_objects, cur_frames, in_block
        if cur_category is not None:
            pending_report_blocks.append((cur_category, tuple(cur_frames), cur_bytes, cur_objects))
        cur_category = None
        cur_bytes = None
        cur_objects = None
        cur_frames = []
        in_block = False

    def commit_pending_report():
        nonlocal pending_report_blocks
        for category, frames, nbytes, nobjects in pending_report_blocks:
            key = (category, frames)
            entry = agg.get(key)
            if entry is None:
                entry = {"objects": 0, "bytes": 0, "occurrences": 0, "frames": list(frames)}
                agg[key] = entry
            entry["objects"] += nobjects
            entry["bytes"] += nbytes
            entry["occurrences"] += 1
        pending_report_blocks = []

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')

            if 'ERROR: LeakSanitizer' in line:
                # A new report is starting. Anything still pending from a
                # previous report that never reached its SUMMARY line is
                # abandoned here (should not normally happen -- reports are
                # SUMMARY-terminated -- but guards against odd concatenations).
                finalize_current_block()
                pending_report_blocks = []
                reports_started += 1
                continue

            sm = SUMMARY_RE.match(line)
            if sm:
                finalize_current_block()
                commit_pending_report()
                summary_total_bytes += int(sm.group(1))
                summary_total_objects += int(sm.group(2))
                reports_completed += 1
                continue

            m = LEAK_START_RE.match(line)
            if m:
                finalize_current_block()
                cur_category = m.group(1)
                cur_bytes = int(m.group(2))
                cur_objects = int(m.group(3))
                cur_frames = []
                in_block = True
                continue

            if in_block:
                fm = FRAME_RE.match(line)
                if fm:
                    cur_frames.append(fm.group(2).strip())
                    continue
                else:
                    finalize_current_block()
                    continue

        # EOF reached
        finalize_current_block()

    reports_dropped = reports_started - reports_completed
    dropped_bytes = sum(b for _, _, b, _ in pending_report_blocks)
    dropped_objects = sum(o for _, _, _, o in pending_report_blocks)

    signatures = []
    for (category, frames), stats in agg.items():
        signatures.append({
            "category": category,
            "objects": stats["objects"],
            "bytes": stats["bytes"],
            "occurrences": stats["occurrences"],
            "frames": stats["frames"],
        })
    signatures.sort(key=lambda x: -x["bytes"])

    return {
        "stats": {
            "reports_started": reports_started,
            "reports_completed": reports_completed,
            "reports_dropped_incomplete": max(reports_dropped, 0),
            "dropped_bytes": dropped_bytes,
            "dropped_objects": dropped_objects,
            "summary_total_bytes": summary_total_bytes,
            "summary_total_objects": summary_total_objects,
        },
        "signatures": signatures,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", help="Path to the ASAN/LSAN report text file")
    ap.add_argument("--out", default="agg.json", help="Output JSON path (default: agg.json)")
    args = ap.parse_args()

    result = parse(args.report)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)

    s = result["stats"]
    print(f"reports_started:            {s['reports_started']}")
    print(f"reports_completed:          {s['reports_completed']}")
    print(f"reports_dropped_incomplete: {s['reports_dropped_incomplete']}", file=sys.stderr if s['reports_dropped_incomplete'] else sys.stdout)
    if s["reports_dropped_incomplete"]:
        print(f"  -> dropped {s['dropped_bytes']} byte(s) / {s['dropped_objects']} object(s) from the incomplete trailing report(s); excluded from aggregates.")
    print(f"unique signatures: {len(result['signatures'])}")
    print(f"summary_total_bytes (sum of SUMMARY lines): {s['summary_total_bytes']}")
    print(f"summary_total_objects (sum of SUMMARY lines): {s['summary_total_objects']}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
