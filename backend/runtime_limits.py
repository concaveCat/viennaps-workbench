"""Runtime limits derived from resources visible to this Python process."""

from __future__ import annotations

import os


def cpu_thread_limit() -> dict[str, int | str]:
    """Return the number of logical CPUs available to this process and its source."""
    affinity_getter = getattr(os, "sched_getaffinity", None)
    if affinity_getter is not None:
        try:
            affinity = affinity_getter(0)
        except OSError:
            affinity = None
        if affinity:
            return {"maximum": max(1, len(affinity)), "limitSource": "process CPU affinity"}

    return {"maximum": max(1, os.cpu_count() or 1), "limitSource": "os.cpu_count fallback"}


def available_cpu_threads() -> int:
    return int(cpu_thread_limit()["maximum"])
