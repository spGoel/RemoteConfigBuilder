---
name: asan-leak-triage
description: "Analyze AddressSanitizer (ASAN), LeakSanitizer (LSAN), and __lsan report files, correlate leaked objects with allocation stacks and sizes, apply a supplied Confluence false-positive list, and return only validated leaks with an auditable summary. Use when a user provides an ASAN/LSAN leak report and false-positive documentation."
argument-hint: "[ASAN/LSAN report path or pasted report] [false-positive Confluence export, URL, or pasted entries]"
user-invocable: true
---

# ASAN Leak Triage

## Outcome

Produce a report of valid memory leaks from an ASAN/LSAN report after excluding documented false positives. Every retained leak must include its object count, total leaked size, allocation stack, and the reason it was retained. Do not claim a leak is valid merely because it is not recognized; distinguish validated, uncertain, and suppressed findings during analysis, then return only validated findings in the primary report.

## Inputs

- The complete ASAN/LSAN report, either as pasted text or a readable local file.
- A false-positive source is optional. The built-in catalog below is applied automatically. A Confluence page URL, exported HTML/PDF/text, or pasted page content may be supplied to add newer rules.

If the ASAN/LSAN report is missing, ask for it before analysis. If an optional Confluence URL cannot be accessed, continue with the built-in catalog and state that the external source was unavailable. Never invent additional rules from memory.

## Built-in False-Positive Catalog

This catalog is transcribed from the supplied PDF, “Details of Memory Leaks (Linux)”. Apply a rule only when the report matches the identifying issue ID, function, file, module, or distinctive stack signature. These rules are baseline suppressions and do not require the user to upload the PDF again.

| Source entry | Match signature | Classification and rationale |
|---|---|---|
| TXL-2716 | `gdk_pid_init`, `GDK_PIDObjects.cpp:247-250` | Suppress: initialization runs once and objects live for process lifetime. |
| TXL-2834 | `startup_task` startup allocation in `startup.cpp` | Suppress: startup-only allocation intentionally remains for program lifetime. |
| TXL-2836 | `irr::gui::CListControl::addWideStaticDisplayItem`, `CListControl.cpp` | Suppress: deallocation is handled by existing cleanup. |
| TXL-2894 | `safely_update_comb_info_cstr`, host string utilities, dual-screen cabinet startup | Suppress: host loads once; non-recurring startup case. |
| TXL-2687 | `CConfigReader::_parseNode`, `CConfigReader.cpp:458`, `MenuItem` in `auditMenuOptions` | Suppress: `ClearOptions()` and the destructor release the allocation. |
| TXL-2756 / TXL-2463 | `CreateCProgressiveSAPSource`, `sap_progressive_source.cpp:198` | Suppress: intentional SAP progressive allocation retained by design. |
| TXL-3288 | ASAN heap-buffer-overflow at `gdkslot.cpp:634`, `log_inputs`, `memcpy` of aligned trace-state data | Suppress: documented sanitizer false positive in the supplied catalog; this is not a leak. |
| TXL-2463 | `read_game_info`, `common.cpp:2795/2801/2813/2818`, `libxml2` | Suppress: game name/version allocations are retained for process lifetime. |
| TXL-4294 | `ComponentManager::getComponent`, `ComponentInfo`, `m_Comp_map` | Suppress: static singleton owns the map for process lifetime. |
| TXL-5999 | Valgrind `dlopen`/`ServiceLocatorImpl`/glibc allocation on 32-bit platform | Suppress: documented glibc/Valgrind issue. |
| TXL-5998 | `EdgeLightManagerV2::getPlatformControlledStripIDs` / edge-light manager | Suppress: catalog identifies this as the same known Valgrind issue as TXL-5999. |
| TXL-6000 | `soap_init`, `G2SHostProc`, `g2sengine`, SOAP stream/MHT allocation | Suppress: `soap_done` cleanup releases `d_stream` and MHT state. |
| TXL-6776 | `ProgressiveBaseX.cpp:117`, `BaseX::_utilities::allocate_copy<char>`, stack-use-after-scope | Suppress: documented false positive in the supplied catalog. |
| TXL-6779 | `AllocateMemory<SLOT_GLOBALS>`, `on_powerup_game_init`, `basexnull.cpp` | Suppress: documented false positive in the supplied catalog. |
| OZGAMEKBP-4254 | Untraceable allocations reported from `libIrrlicht`, `libvorbis`, `libnvidia`, or `libogg` | Suppress: catalog classifies these external-library-only reports as false positives. |
| OZGAMEKBJ-611 | `udev_device_get_sysattr_value`, `udev_device_new_from_syspath`, `opendir`, `host/common/os/gds_hid/udm.cpp` | Suppress: returned strings are borrowed and `udev_device_unref` cleanup is documented as handled. |
| TXL-7079 | `usbdisp_init`, `_createDC`, `_creatememoryDC` | Suppress only when the report matches the catalog's required process-lifetime allocation or documented cleanup case. |
| TXL-7129 | `create_payout_screen`, `basehelp.cpp`, `_createscene` | Suppress: catalog says cleanup exists although sanitizer reports a direct leak. |
| TXL-7079 / TXL-2836 | `CListControl::addWideStaticDisplayItem` from audit-menu lockup paths | Suppress: same Irrlicht cleanup false positive documented under TXL-2836. |
| TXL-6823 | ASAN stack-buffer-overflow with custom stack unwind, `gos_create_thread`/`GameContextServer` | Suppress: catalog classifies this sanitizer report as a false positive; this is not a leak. |
| TXL-8227 | `EdgeLightManagerV2` constructor/`getInstance` | Suppress: edge-light object intentionally exists for software lifetime. |
| TXL-8252 | Windows VLD allocation in `HelixLighting.DLL` or sanitized code, deletion inside non-sanitized Irrlicht | Suppress: cross-module deallocation is invisible to the sanitizer. |
| TXL-8675 | `IrrPromptInit`, `PromptEventReceiver`, `irr_prompt.cpp:344` | Suppress: receiver is managed by process-lifetime `IrrlichtDevice` objects. |
| TXL-8493 | `Gen7G2SMain::add_g2sevent`, `g2s_addEvent`, `GameState` | Suppress: G2S engine owns and deletes the allocation. |
| TXL-9678 | ASAN attempting double-free involving `gdk_get_game_info` and `read_game_info` | Suppress: catalog identifies two different static structures and addresses, not a double-free. |
| TXL-11176 / TXL-17894 | `ProgressiveBaseX`, `generate_turnover_link_option_records`, `allocate_copy`, lines `722`, `735`, or `1099` | Suppress: one-time legacy LP/audit allocation retained for game lifetime. |
| GGNF-LSBJPS-1636 | `G2SATIDataHandlerProc::send_atiDataInfo`, `oStr`, `form_sendMsg` | Suppress: ownership is transferred to the G2S message path and cleanup handles success/failure. |
| TXL-10728 | `qcom3::Server`, `CRYPTO_zalloc`, `libqcom3_qle_container.so` | Suppress: catalog attributes the allocation to external Boost/library behavior, not platform code. |
| TXL-17895 | Windows `GetOpenFileNameW`, `COMDLG32.dll`, `combase.dll`, `clbcatq.dll` | Suppress: allocation originates in Windows common-dialog internals. |
| TXL-17896 | Windows `SHFileOperation`, `urlmon.dll`, `Windows.Storage.dll`, `combase.dll` | Suppress: allocation originates in Windows system components; GDK only triggers the path. |

Do not suppress an otherwise matching finding when the report contradicts the catalog entry, for example by showing repeated growth, a different ownership path, or a project allocation instead of the documented external/runtime allocation. The catalog's “suppress” rules apply to the matching false-positive records only, not to every report containing a broad library or function name.

## Project-Confirmed False Positives (not from the PDF catalog)

Some findings get confirmed as false positives directly by the project team in chat, without a formal Confluence/ticket reference. These are kept in `scripts/classify_leaks.py` as `PROJECT_CONFIRMED_RULES`, separate from `BUILTIN_RULES`, so the report can always show whether a suppression came from the official catalog or from a chat confirmation. Current entries:

| Rule ID | Match signature | Confirmed |
|---|---|---|
| `CONFIRMED-2026-08-31-1` | `AllocateMemory<SLOT_GLOBALS>`, `on_powerup_game_init` (any call site, not just `basexnull.cpp`) | 2026-08-31, chat, no ticket reference |
| `CONFIRMED-2026-08-31-2` | `gos_getmem`, `_avlmalloc`, `CreateSysProp` (covers both `SetAvlDefaults` and `setup_dual_video_screens` call sites) | 2026-08-31, chat, no ticket reference |

When a user confirms a new finding as a false positive in chat without a document to cite, add it here the same way: a new `CONFIRMED-<date>-<n>` entry in `PROJECT_CONFIRMED_RULES` with the identifying tokens and a note stating it's chat-confirmed with no ticket. Do not fold these into `BUILTIN_RULES` — that list's header claims PDF provenance and must stay accurate. Mention to the user that these ad-hoc confirmations should ideally be added to the real Confluence catalog so other engineers (and sessions without this chat's context) can see the same reasoning.

## Tooling for large reports

Batch/repeated-run ASAN reports (log concatenations from many process runs, hundreds of MB, hundreds of thousands of leak blocks) cannot be read as text — use the bundled scripts instead of reading the file or writing ad-hoc parsing code:

- `scripts/parse_asan.py <report> --out agg.json` — streams the file once, groups leak blocks by normalized allocation-stack signature (addresses stripped so repeated runs of the same call site collapse together), and writes the aggregate to JSON. It also detects an **incomplete trailing report** — a run whose final leak block or `SUMMARY:` line is cut off mid-line (a batch capture stopped mid-write, a size/time-limited log, etc.) — and drops that run's leak data entirely rather than folding a partial count into the aggregates. It prints `reports_started` vs `reports_completed`; a mismatch means a trailing report was dropped, and the counts/bytes dropped are printed for the record.
- `scripts/classify_leaks.py agg.json [--extra-rules rules.json]` — applies the built-in catalog (encoded as `BUILTIN_RULES` in the script, one entry per catalog row) to every aggregated signature and prints Valid / Suppressed / Uncertain groupings plus a reconciliation against the sum of the report's own `SUMMARY:` lines. Pass `--extra-rules` with a JSON file in the same shape as `BUILTIN_RULES` for any additional entries pulled from a supplied Confluence page.

Read both scripts before relying on them if the report's format differs noticeably from the standard ASAN/LSAN `Direct leak of N byte(s) in M object(s) allocated from:` + `#N 0xADDR in FUNC FILE:LINE` layout — the regexes are format-specific and may need adjusting for a differently-formatted tool or sanitizer version. For a normal-sized report (small enough to read directly), the manual procedure below still applies; the scripts exist to make the same judgment calls reproducible and to avoid re-deriving parsing/classification code for every large report.

Rule design notes (also commented in `classify_leaks.py`):
- A rule's `scope` matters: `"site"` requires the identifying tokens to be the actual allocation call frame, not merely an ancestor several frames up (e.g. `startup_task`/`startup.cpp` is the top-level boot dispatcher and is an ancestor of nearly everything that allocates at startup — matching it anywhere in the stack would over-suppress). `"any"` is for rules whose identifying names are specific enough that appearing anywhere in the stack is meaningful.
- Matching all-but-one identifying token for a rule is reported as an **uncertain near-miss** (cites which token didn't match) rather than silently suppressed or silently ignored — this is what should happen for a rule like `TXL-6779` when the function names match but the file doesn't.
- The `OZGAMEKBP-4254` external-library family match requires every frame past the interceptor to resolve into one of the named libraries (or be unresolved) with at least one frame actually naming a library — a fully-unresolved, unattributed stack is not silently suppressed, and a library name must match precisely (e.g. `libvorbis.so` must not also match the distinct `libvorbisfile.so`).

## Procedure

1. Read the entire report, including the process, sanitizer configuration, leak summary, all individual leak records, and allocation backtraces. Preserve the original report; do not modify it. For a large/batch report, run it through `scripts/parse_asan.py` and `scripts/classify_leaks.py` instead (see "Tooling for large reports" above) and use their output to populate the same Procedure steps below.
2. Parse each leak record. Capture, when present:
   - Leak category: direct, indirect, reachable, suppressed, or unknown.
   - Object count and total leaked bytes.
   - Per-object size when it can be calculated as `total bytes / object count`; label it derived.
   - The top allocation function, complete allocation stack, source file and line, module/library, and any thread or process context.
   - A stable finding identifier based on the record order and allocation stack.
3. Normalize allocation signatures for comparison without losing the original text. Compare function names, source paths, line numbers, modules, allocation APIs, and distinctive stack frames. Treat a match as exact only when the documented rule's identifying fields match; do not suppress a finding based on a generic function name alone.
4. Apply the built-in false-positive catalog to every parsed record. If external Confluence material is supplied and accessible, convert only its additional explicit entries into rules and record the matching scope, rationale, and exceptions.
5. Mark matching records as suppressed and cite the catalog entry or external source entry. If a rule is ambiguous, do not suppress the record; mark it uncertain internally and explain the ambiguity in the notes.
6. Validate remaining candidates:
   - Include direct leaks as valid when they are not suppressed and the report provides a credible allocation record.
   - Include indirect leaks only when their owning direct leak or allocation chain is also a retained valid leak; otherwise mark them uncertain rather than counting them independently as a root cause.
   - Exclude records explicitly marked suppressed by ASAN/LSAN or by the supplied false-positive source.
   - Treat reachable leaks as leaks only if the report or project-specific guidance explicitly classifies them as defects; otherwise keep them out of the validated list and mention the policy.
   - Deduplicate repeated presentations of the same allocation signature, but preserve counts and sizes from the sanitizer summary and state when aggregation occurred.
7. Reconcile totals. Sum retained object counts and bytes, separately sum suppressed bytes, and compare them with the report's summary where possible. Flag discrepancies caused by indirect-leak accounting, deduplication, or missing sizes. If a run/report in the file is incomplete — its final leak block or `SUMMARY:` line is truncated mid-line, or an `ERROR: LeakSanitizer` header is never followed by a matching `SUMMARY:` — drop that run's leak data entirely rather than merging a partial count into the totals; note the drop and its approximate size in the Uncertainty section instead.
8. Return the primary report using the format below. Do not include suppressed findings in the validated-leaks table. Include a brief suppression and uncertainty audit so the exclusion is inspectable.

## Output Format

### Valid Leaks

| ID | Category | Objects | Total bytes | Bytes/object | Allocation site | Stack summary |
|---|---|---:|---:|---:|---|---|
| `L1` | direct | 1 | 128 | 128 (derived) | `module!function (file:line)` | `frame -> frame -> ...` |

Use `None identified` when no validated leaks remain. Use `Unknown` for unavailable values; never guess sizes.

### Totals

- Valid leaked objects: `<count>`
- Valid leaked bytes: `<bytes>`
- Suppressed false-positive objects/bytes: `<count> / <bytes>`
- Uncertain objects/bytes: `<count> / <bytes>`
- Reported sanitizer totals: `<values>`
- Reconciliation: `matched`, `partial`, or `not possible`, with one short reason

### False-Positive Rules Applied

List each rule used with its source entry or quoted identifying text and the affected finding IDs. Do not reproduce irrelevant Confluence content.

### Uncertainty and Data Quality

List truncated stacks, ambiguous matches, missing sizes, indirect-leak ownership issues, inaccessible source material, and any assumptions. This section may mention findings omitted from the primary table, but must not present them as validated leaks.

## Guardrails

- The supplied Confluence material is the source of truth for false positives; do not broaden it from intuition.
- Do not report only a total. Report each retained allocation group and its sizes.
- Never silently discard a finding. Suppressed and uncertain records must be auditable by ID.
- Never infer a source line, object size, ownership relationship, or validity without evidence. Label derived arithmetic and assumptions.
- If the report is incomplete or malformed, analyze the readable portion and state exactly what prevents complete validation.
- Keep the final answer concise, but include enough stack context for an engineer to reproduce the classification.
