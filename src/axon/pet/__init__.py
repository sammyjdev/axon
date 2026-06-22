"""AXON pet — visual companion that mirrors workspace activity.

Wired as ``axon familiar`` in the main CLI (``src/axon/__main__.py``).
Run via::

    axon familiar            # live, Ctrl+C to exit
    axon familiar --frames N # bounded N-tick run (CI-safe)
    python -m axon.pet       # direct module invocation

Activity source: tails ``data_root/trace/records.jsonl`` via
``axon.observability.trace_store.TraceStore`` (dec-119). No TTY scanning.
"""
