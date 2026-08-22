# Memory Profiler

Standalone Robot meter monitoring and historical charting tab.

## Run independently

Run `run.bat`, or:

```powershell
py -3 main.py
```

Local CSV mode uses only the Python standard library. Remote EGM mode also
requires Paramiko:

```powershell
py -3 -m pip install -r requirements.txt
```

Profiler settings and persistent samples are stored independently in:

```text
%USERPROFILE%\.robot_memory_profiler\
```

The shared top-level `launcher.py` only mounts `MemoryProfilingTab` from this
folder; the profiler does not import files from the other tool folders.
