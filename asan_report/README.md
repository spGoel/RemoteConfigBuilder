# ASAN Report Viewer

Standalone offline analyzer for AddressSanitizer report files.

Run `run.bat` or `py -3 main.py`, choose one report with **Browse**, then click
**Start Analysis**. Selecting a file only loads its raw preview; analysis does
not begin until the button is clicked.

**Start Analysis** runs deterministic offline leak triage using the project's
built-in false-positive catalog. Results appear in the **Offline Analysis** tab
and can be saved as text. The analyzer requires no network connection, API key,
external package, or LLM. Findings are separated into candidate direct leaks,
catalog-suppressed false positives, and items that still require human or LLM
review; a candidate is not automatically validated.

Large reports are streamed through the analyzer, so the complete file is
classified without loading it all into GUI memory. The **Raw Report** tab shows
at most a 16 MiB preview; **Save Copy** still copies the complete report.
