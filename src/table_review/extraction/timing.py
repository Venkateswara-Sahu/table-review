"""Content-free wall timings. Nested stages are deliberately not additive."""

import time


class TimingLedger:
    def __init__(self):
        self.started = time.monotonic()
        self.stages = {}

    def call(self, name, function, *args, **kwargs):
        started = time.monotonic()
        try:
            return function(*args, **kwargs)
        finally:
            record = self.stages.setdefault(name, {"seconds": 0.0, "count": 0})
            record["seconds"] += time.monotonic() - started
            record["count"] += 1

    def snapshot(self):
        return {
            "schema": "extraction_wall_stages_v1",
            "semantics": "wall_durations_may_overlap_not_additive",
            "total_seconds": time.monotonic() - self.started,
            "stages": {name: dict(value) for name, value in self.stages.items()},
        }
