from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from .llm.base import QuotaExhausted


def pmap(fn, items, workers: int = 4):
    """Ordered parallel map. Exceptions are returned in place of results, except
    QuotaExhausted, which cancels pending work and is re-raised (the cache keeps
    everything already done, so a rerun resumes)."""
    items = list(items)
    results: list = [None] * len(items)
    if workers <= 1:
        for i, it in enumerate(items):
            try:
                results[i] = fn(it)
            except QuotaExhausted:
                raise
            except Exception as e:  # noqa: BLE001 - recorded per item
                results[i] = e
        return results
    quota: QuotaExhausted | None = None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, it): i for i, it in enumerate(items)}
        for f in as_completed(futs):
            i = futs[f]
            try:
                results[i] = f.result()
            except QuotaExhausted as e:
                quota = quota or e
                for other in futs:
                    other.cancel()
            except Exception as e:  # noqa: BLE001
                results[i] = e
    if quota:
        raise quota
    return results
