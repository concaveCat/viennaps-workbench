"""Runtime limits shared by the API process and the bounded worker.

All deployment limits are configured with ``WORKBENCH_*`` environment
variables. Invalid values fall back to the documented default with a logged
warning; no limit has an artificial upper bound.
"""

from __future__ import annotations

import logging
import os
from typing import Any

LOGGER = logging.getLogger(__name__)

MIB = 1024 * 1024
GIB = 1024 * MIB

#: Per-run artifact/archive cap.
ARTIFACT_LIMIT_ENV = "WORKBENCH_MAX_ARTIFACT_MIB"
DEFAULT_ARTIFACT_LIMIT_MIB = 4096
MIN_ARTIFACT_LIMIT_MIB = 64

#: Total application-data budget.
MAX_DATA_ENV = "WORKBENCH_MAX_DATA_GIB"
DEFAULT_MAX_DATA_GIB = 10
MIN_MAX_DATA_GIB = 1

#: Free-disk floor before accepting new data.
MIN_FREE_ENV = "WORKBENCH_MIN_FREE_GIB"
DEFAULT_MIN_FREE_GIB = 20
MIN_MIN_FREE_GIB = 0

#: Soft RSS polling watch for a supervised worker (not a cgroup limit).
RSS_LIMIT_ENV = "WORKBENCH_RSS_LIMIT_MIB"
DEFAULT_RSS_LIMIT_MIB = 8192
MIN_RSS_LIMIT_MIB = 512

#: Per-run combined stdout/stderr cap.
MAX_LOG_ENV = "WORKBENCH_MAX_LOG_MIB"
DEFAULT_MAX_LOG_MIB = 4
MIN_MAX_LOG_MIB = 1

# Invalid values are warned about once per distinct (env, value) pair so a
# misconfigured long-running service does not log on every request.
_WARNED_INVALID: set[tuple[str, str]] = set()


def _read_int(env: str, default: int, minimum: int) -> tuple[int, str, str | None]:
    """Return ``(value, source, raw)``; any positive value above the minimum is valid."""
    raw = os.environ.get(env)
    if raw is None:
        return default, "default", None
    text = raw.strip()
    if text:
        try:
            value = int(text, 10)
        except ValueError:
            value = None
        if value is not None and value >= minimum:
            return value, "env", raw
    if (env, raw) not in _WARNED_INVALID:
        _WARNED_INVALID.add((env, raw))
        LOGGER.warning(
            "%s=%r is invalid (expected an integer >= %d); using the default %d",
            env, raw, minimum, default,
        )
    return default, "default", raw


def artifact_limit_mib() -> int:
    """Return the configured per-run artifact cap in MiB (default 4096, minimum 64)."""
    return _read_int(ARTIFACT_LIMIT_ENV, DEFAULT_ARTIFACT_LIMIT_MIB, MIN_ARTIFACT_LIMIT_MIB)[0]


def artifact_limit_bytes() -> int:
    """Return the configured per-run artifact cap in bytes."""
    return artifact_limit_mib() * MIB


def artifact_limit_label() -> str:
    """Return the configured per-run artifact cap for user-facing messages."""
    return f"{artifact_limit_mib()} MiB"


def max_data_gib() -> int:
    """Return the total application-data budget in GiB (default 10, minimum 1)."""
    return _read_int(MAX_DATA_ENV, DEFAULT_MAX_DATA_GIB, MIN_MAX_DATA_GIB)[0]


def max_data_bytes() -> int:
    return max_data_gib() * GIB


def min_free_gib() -> int:
    """Return the free-disk floor in GiB (default 20, minimum 0)."""
    return _read_int(MIN_FREE_ENV, DEFAULT_MIN_FREE_GIB, MIN_MIN_FREE_GIB)[0]


def min_free_bytes() -> int:
    return min_free_gib() * GIB


def rss_limit_mib() -> int:
    """Return the soft supervised-worker RSS watch in MiB (default 8192, minimum 512)."""
    return _read_int(RSS_LIMIT_ENV, DEFAULT_RSS_LIMIT_MIB, MIN_RSS_LIMIT_MIB)[0]


def max_log_mib() -> int:
    """Return the per-run combined log cap in MiB (default 4, minimum 1)."""
    return _read_int(MAX_LOG_ENV, DEFAULT_MAX_LOG_MIB, MIN_MAX_LOG_MIB)[0]


def max_log_bytes() -> int:
    return max_log_mib() * MIB


def artifact_limit_status() -> dict[str, Any]:
    """Return the effective artifact cap and the metadata behind it."""
    value, source, raw = _read_int(ARTIFACT_LIMIT_ENV, DEFAULT_ARTIFACT_LIMIT_MIB,
                                   MIN_ARTIFACT_LIMIT_MIB)
    return {
        "artifactLimitBytes": value * MIB,
        "artifactLimitMiB": value,
        "artifactLimitSource": source,
        "artifactLimitEnvironmentVariable": ARTIFACT_LIMIT_ENV,
        "artifactLimitEnvironmentValue": raw,
        "artifactLimitDefaultMiB": DEFAULT_ARTIFACT_LIMIT_MIB,
        "artifactLimitMinimumMiB": MIN_ARTIFACT_LIMIT_MIB,
        "artifactLimitMaximumMiB": None,
    }


def deployment_limit_status() -> dict[str, Any]:
    """Return every effective deployment limit and its configuration metadata."""
    status = artifact_limit_status()
    data, data_source, data_raw = _read_int(MAX_DATA_ENV, DEFAULT_MAX_DATA_GIB, MIN_MAX_DATA_GIB)
    free, free_source, free_raw = _read_int(MIN_FREE_ENV, DEFAULT_MIN_FREE_GIB, MIN_MIN_FREE_GIB)
    rss, rss_source, rss_raw = _read_int(RSS_LIMIT_ENV, DEFAULT_RSS_LIMIT_MIB, MIN_RSS_LIMIT_MIB)
    log, log_source, log_raw = _read_int(MAX_LOG_ENV, DEFAULT_MAX_LOG_MIB, MIN_MAX_LOG_MIB)
    status.update({
        "maximumDataBytes": data * GIB,
        "maximumDataGiB": data,
        "maximumDataSource": data_source,
        "maximumDataEnvironmentVariable": MAX_DATA_ENV,
        "maximumDataEnvironmentValue": data_raw,
        "maximumDataDefaultGiB": DEFAULT_MAX_DATA_GIB,
        "maximumDataMinimumGiB": MIN_MAX_DATA_GIB,
        "maximumDataMaximumGiB": None,
        "minimumFreeBytes": free * GIB,
        "minimumFreeGiB": free,
        "minimumFreeSource": free_source,
        "minimumFreeEnvironmentVariable": MIN_FREE_ENV,
        "minimumFreeEnvironmentValue": free_raw,
        "minimumFreeDefaultGiB": DEFAULT_MIN_FREE_GIB,
        "minimumFreeMinimumGiB": MIN_MIN_FREE_GIB,
        "minimumFreeMaximumGiB": None,
        "rssLimitBytes": rss * MIB,
        "rssLimitMiB": rss,
        "rssLimitSource": rss_source,
        "rssLimitEnvironmentVariable": RSS_LIMIT_ENV,
        "rssLimitEnvironmentValue": rss_raw,
        "rssLimitDefaultMiB": DEFAULT_RSS_LIMIT_MIB,
        "rssLimitMinimumMiB": MIN_RSS_LIMIT_MIB,
        "rssLimitMaximumMiB": None,
        "maximumLogBytes": log * MIB,
        "maximumLogMiB": log,
        "maximumLogSource": log_source,
        "maximumLogEnvironmentVariable": MAX_LOG_ENV,
        "maximumLogEnvironmentValue": log_raw,
        "maximumLogDefaultMiB": DEFAULT_MAX_LOG_MIB,
        "maximumLogMinimumMiB": MIN_MAX_LOG_MIB,
        "maximumLogMaximumMiB": None,
    })
    return status


def artifact_limit_startup_message() -> str:
    """Return the one-line startup summary for the effective artifact cap."""
    value, source, raw = _read_int(ARTIFACT_LIMIT_ENV, DEFAULT_ARTIFACT_LIMIT_MIB,
                                   MIN_ARTIFACT_LIMIT_MIB)
    if source == "env":
        detail = f"{ARTIFACT_LIMIT_ENV}={value}"
    elif raw is not None:
        detail = f"{ARTIFACT_LIMIT_ENV}={raw!r} ignored"
    else:
        detail = ARTIFACT_LIMIT_ENV
    return (f"artifact cap: {value} MiB ({detail}, "
            f"default {DEFAULT_ARTIFACT_LIMIT_MIB}, "
            f"min {MIN_ARTIFACT_LIMIT_MIB} MiB, unbounded)")


def deployment_limits_startup_message() -> str:
    """Return the one-line startup summary for the remaining deployment limits."""
    data, data_source, data_raw = _read_int(MAX_DATA_ENV, DEFAULT_MAX_DATA_GIB, MIN_MAX_DATA_GIB)
    free, free_source, free_raw = _read_int(MIN_FREE_ENV, DEFAULT_MIN_FREE_GIB, MIN_MIN_FREE_GIB)
    rss, rss_source, rss_raw = _read_int(RSS_LIMIT_ENV, DEFAULT_RSS_LIMIT_MIB, MIN_RSS_LIMIT_MIB)
    log, log_source, log_raw = _read_int(MAX_LOG_ENV, DEFAULT_MAX_LOG_MIB, MIN_MAX_LOG_MIB)

    def detail(env: str, value: str, source: str, raw: str | None) -> str:
        if source == "env":
            return f"{env}={value}"
        if raw is not None:
            return f"{env}={raw!r} ignored"
        return env

    return ("deployment limits: "
            f"data budget {data} GiB ({detail(MAX_DATA_ENV, str(data), data_source, data_raw)}); "
            f"free floor {free} GiB ({detail(MIN_FREE_ENV, str(free), free_source, free_raw)}); "
            f"RSS watch {rss} MiB ({detail(RSS_LIMIT_ENV, str(rss), rss_source, rss_raw)}); "
            f"log cap {log} MiB ({detail(MAX_LOG_ENV, str(log), log_source, log_raw)})")


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
