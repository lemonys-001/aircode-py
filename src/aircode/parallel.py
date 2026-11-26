from __future__ import annotations

import asyncio
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

_executor: ProcessPoolExecutor | None = None
_executor_workers = 0


def gil_enabled() -> bool:
    checker = getattr(sys, "_is_gil_enabled", None)
    if checker is None:
        return True
    try:
        return bool(checker())
    except Exception:
        return True


def cpu_workers() -> int:
    return max(1, os.cpu_count() or 1)


def get_executor() -> ProcessPoolExecutor:
    global _executor, _executor_workers
    workers = cpu_workers()
    if _executor is None or _executor_workers != workers:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
        _executor = ProcessPoolExecutor(max_workers=workers)
        _executor_workers = workers
    return _executor


async def run_cpu(function, *args):
    """Run a CPU-bound callable in a worker process, outside the GIL."""

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_executor(), function, *args)


def shutdown_executor() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


def _work(seed: int, rounds: int = 120_000) -> int:
    total = 0
    for index in range(rounds):
        total = (total + index * index + seed) % 1_000_003
    return total


def bench(rounds: int = 120_000) -> dict:
    workers = min(4, cpu_workers())
    started = time.perf_counter()
    [ _work(index, rounds) for index in range(workers) ]
    single = time.perf_counter() - started

    started = time.perf_counter()
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_work, range(workers), [rounds] * workers))
        parallel = time.perf_counter() - started
    except Exception as exc:
        return {
            "workers": workers,
            "single_seconds": round(single, 3),
            "parallel_seconds": None,
            "speedup": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "workers": workers,
        "single_seconds": round(single, 3),
        "parallel_seconds": round(parallel, 3),
        "speedup": round(single / parallel, 2) if parallel else None,
        "error": "",
    }
