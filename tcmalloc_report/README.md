# tcMalloc Report Analyzer

Standalone browser for tcMalloc `.heap` snapshots generated below
`build/host/scratch/.logs/mem_profiles`.

In Remote EGM mode it finds `tcMalloc_profiler.sh`, runs it with the selected
heap number as `-endnum`, and downloads the generated PDF. Run `run.bat` or
`py -3 main.py`. Remote access requires Paramiko and uses the project-standard
`mk7/mk7` SSH account. Generated PDFs and settings are stored under
`%USERPROFILE%\.tcmalloc_report_viewer`.
