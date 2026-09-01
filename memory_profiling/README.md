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

## Live TCP mode

Select **Live TCP**, keep `0.0.0.0` as the listen IP, choose a port (default
`2207`), and press **Start before Robot loads its configuration**. Configure
the Robot meter list to connect to the desktop PC's reachable IP on the same
port:

```xml
<meter-list timeout="15" units="Seconds">
    <output address="DESKTOP-IP:2207"/>
    <meter>Free-Memory</meter>
    <meter>Games-Played</meter>
</meter-list>
```

Robot is the TCP client and Memory Profiler is the listener. Allow the chosen
port through the desktop firewall. Received samples go directly into the
profiler's SQLite history; no meter CSV is created or downloaded.

Profiler settings and persistent samples are stored independently in:

```text
%USERPROFILE%\.robot_memory_profiler\
```

The shared top-level `launcher.py` only mounts `MemoryProfilingTab` from this
folder; the profiler does not import files from the other tool folders.
