# ASAN Report Viewer

Standalone local/remote viewer for AddressSanitizer reports generated below
`build/host/scratch/.logs/mem_profiles`.

Run `run.bat` or `py -3 main.py`. Remote EGM access requires Paramiko and uses
the project-standard `mk7/mk7` SSH account. Settings are stored in
`%USERPROFILE%\.asan_report_viewer`.
