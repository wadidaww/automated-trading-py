---
name: hot-path-performance
description: Latency budget, coding rules and benchmarking method for the tick-to-order hot path (Futu push → DataStage → SignalStage → RiskStage → order send). Load before optimizing or adding code on that path, or when reviewing a change for latency regressions.
---

# Hot-path performance

## Honest target
End-to-end latency is dominated by OpenD, Futu's servers and the exchange (milliseconds to tens
of milliseconds), and `place_order` is limited to roughly 15 per 30s. That makes this a
**low-latency intraday** system, not co-located HFT. What we control:

| Segment | Budget (p99) |
|---|---|
| SDK callback → event in decision loop | < 50µs (handoff only) |
| Feature update + model + pre-trade risk | < 100µs |
| Decision → `place_order` call issued | < 200µs |
| Logging, metrics, audit | off-path (0 on hot path) |

## Rules
- **No pandas or pydantic per tick.** Use numpy ring buffers, `__slots__` dataclasses or tuples.
  Pydantic stays at config and API boundaries.
- **Incremental O(1) indicators:** Welford mean and variance, EMA, Wilder RSI, rolling sums. Do
  not recompute over the window.
- **No I/O awaits in the decision loop.** Portfolio, positions and open orders come from an
  in-memory book updated by push handlers. Orders are handed to an executor task or thread.
- **Conflate stale ticks.** Keep a per-symbol latest-value slot plus a dirty set. Never let a
  backlog of old quotes drive decisions.
- **Logging:** none per tick. Per order, use a structured event pushed to a
  `QueueHandler`/background writer (orjson). Cache loggers.
- **Prometheus:** pre-bind `.labels(...)` children at init. Use histograms for latency.
- **GC:** call `gc.freeze()` after warm-up and tune `gc.set_threshold`. Avoid per-tick
  allocation.
- **Event loop:** use `uvloop` in production.
- **numba `@njit`** is for kernels over arrays. Profile first. Rust/pyo3 is justified only for
  replacing SDK protobuf decoding.

## Measuring
- Put `time.perf_counter_ns()` stamps on the event: `t_recv` (callback), `t_decide`, `t_sent`.
  Export them as histograms.
- Microbenchmarks go in `benchmarks/` with `pytest-benchmark` or `pyperf`. Report p50, p99 and
  p99.9 over at least 100k synthetic ticks.
- Profile with `py-spy record --native` or `scalene`. Attach the flamegraph to the PR when
  claiming a speedup.
- Every incremental kernel has a property test (hypothesis) against the vectorised reference
  implementation (tolerance 1e-9).
