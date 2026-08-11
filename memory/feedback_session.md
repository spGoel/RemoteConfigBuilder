---
name: feedback-session
description: Working preferences and feedback observed from this session (2026-08-05)
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3b6dc701-318d-4d49-a6ee-95136003b9be
  modified: 2026-08-05T11:20:09.654Z
---

**Always update MEMORY.md after each session** — user explicitly asked: "Always update memory.md with the changes/queries asked till now."

**Why:** User wants memory to be a running log of all work done and preferences observed, so future conversations start with full context.

**How to apply:** At the end of every session (or when asked), update all relevant memory files and add new ones for new topics. Keep MEMORY.md index in sync.

---

**User provides format by example (PDF/file attachment), not by description** — when asked to produce a document in a specific format, they attach the reference PDF rather than describing it. Always read the attached file before generating.

**Why:** Observed when user said "create a confluence page for this tool in the format attached" and attached the TechnoAI-26 PDF.

**How to apply:** When user attaches a file alongside a "create X in this format" request, read the attached file first and match its structure exactly.

---

**Plan-first workflow** — user expects a planning phase with exploration agents, a written plan, and explicit approval before any code is written.

**Why:** User triggered plan mode and said "Can you plan for now. I will add suggestion in planning if any addition is needed." — indicating they want to review and potentially redirect before implementation starts.

**How to apply:** For new tool/feature requests, always enter plan mode, explore the codebase/requirements, write a detailed plan, and wait for approval before writing code.
