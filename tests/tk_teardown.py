"""Teardown helpers for the headless CustomTkinter UI tests.

CTk.destroy() does not cancel CustomTkinter's self-rescheduling .after()
loops (ScalingTracker.check_dpi_scaling, the CTkTextbox scrollbar check, a
CTk update callback). Tcl's notifier is process-wide, so when several UI
test modules or classes share one process, those stale jobs fire during a
later root's update() and print 'invalid command name ... bgerror failed'
noise. Destroy every CTk root through destroy_root() instead.
"""


def cancel_pending_timers(root):
    """Cancel every .after() job still queued on ``root``'s Tcl interpreter.

    Only the Tcl timer is cancelled. tkinter's after_cancel() would also
    delete the job's Python command, which may still belong to a live widget
    (e.g. a CTkTextbox); that widget's own destroy() then fails with
    "can't delete Tcl command". Leaving the command lets each widget clean
    up normally."""
    for job in root.tk.call("after", "info"):
        root.tk.call("after", "cancel", job)


def destroy_root(root):
    cancel_pending_timers(root)
    root.destroy()
