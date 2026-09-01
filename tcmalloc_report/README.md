# tcMalloc Report Analyzer

Enter the EGM IP and the exact host path, for example
`/home/mk7/development/tcMalloc/build/host`, then click **Load Heap Files**.

The tool rejects missing paths and builds whose `.mk7conf` does not contain
`usetcmalloc=True`. It lists every `.heap` snapshot below
`scratch/.logs/mem_profiles` using filenames only. Select a file and click
**Convert to PDF** to run `tcMalloc_profiler.sh` and download the generated PDF.

Run `run.bat` or `py -3 main.py`. Remote access requires Paramiko and uses the
project-standard `mk7/mk7` SSH account. PDFs and settings are stored under
`%USERPROFILE%\.tcmalloc_report_viewer`.
