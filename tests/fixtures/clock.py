from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass
class Clock:
    current: datetime = field(default_factory=lambda: datetime.now(UTC))

    def now(self):
        return self.current

    def advance(self, **kwargs):
        self.current += timedelta(**kwargs)
