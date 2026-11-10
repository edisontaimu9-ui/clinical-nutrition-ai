"""Spend and rate caps (NFR-SEC-10, SECURITY.md 7.3 item 9). Checked BEFORE every hosted call, using a worst-case estimate."""
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional

from .types import GatewayError


class BudgetExceeded(GatewayError):
    code = "budget_exceeded"

    def __init__(self, detail: str = "", retry_after_s: float = 0.0):
        super().__init__(detail)
        self.retry_after_s = float(retry_after_s)      # > 0 only for per-minute limits that clear by waiting


@dataclass(frozen=True)
class Budget:
    max_cost_per_day: float            # in the configured currency units
    max_calls_per_minute: int
    max_output_tokens: int
    price_in_per_1k: float
    price_out_per_1k: float
    alert_fraction: float = 0.8
    max_tokens_per_minute: int = 0     # 0 = not enforced. Free-tier limits are hard limits; reservations are worst case.
    max_calls_per_day: int = 0
    max_tokens_per_day: int = 0


class SpendTracker:
    def __init__(self, budget: Budget, clock: Callable[[], float] = time.time, on_alert: Optional[Callable[[str], None]] = None):
        self.b, self.clock, self.on_alert = budget, clock, on_alert
        self._day, self._spent = None, 0.0
        self._calls: Deque[float] = deque()
        self._tok: Deque = deque()          # (time, reserved tokens) within the last minute
        self._day_calls, self._day_tokens = 0, 0
        self._alerted = False

    def _roll(self):
        now = self.clock()
        day = int(now // 86400)
        if day != self._day:
            self._day, self._spent, self._alerted, self._day_calls, self._day_tokens = day, 0.0, False, 0, 0
        while self._calls and now - self._calls[0] >= 60:
            self._calls.popleft()
        while self._tok and now - self._tok[0][0] >= 60:
            self._tok.popleft()

    def preload(self, day: int, calls: int, tokens: int) -> None:
        """Restore today's usage after a restart (the provider's daily limit does not reset when our process does)."""
        self._roll()
        if day == self._day:
            self._day_calls, self._day_tokens = max(0, int(calls)), max(0, int(tokens))

    def usage(self):
        self._roll()
        return self._day, self._day_calls, self._day_tokens

    def cost(self, in_tokens: int, out_tokens: int) -> float:
        return in_tokens / 1000 * self.b.price_in_per_1k + out_tokens / 1000 * self.b.price_out_per_1k

    def reserve(self, est_input_tokens: int, max_output_tokens: int) -> None:
        """Refuse before spending. Raises BudgetExceeded; records the call time on success."""
        self._roll()
        if max_output_tokens > self.b.max_output_tokens:
            raise BudgetExceeded("requested output exceeds the per-request token cap")
        if len(self._calls) >= self.b.max_calls_per_minute:
            raise BudgetExceeded("rate cap reached", 60 - (self.clock() - self._calls[0]) + 1)
        need = est_input_tokens + max_output_tokens
        if self.b.max_calls_per_day and self._day_calls >= self.b.max_calls_per_day:
            raise BudgetExceeded("daily call cap reached")
        if self.b.max_tokens_per_day and self._day_tokens + need > self.b.max_tokens_per_day:
            raise BudgetExceeded("daily token cap would be exceeded")
        if self.b.max_tokens_per_minute:
            if need > self.b.max_tokens_per_minute:
                raise BudgetExceeded("a single request exceeds the per-minute token cap")
            used = sum(t for _, t in self._tok)
            if used + need > self.b.max_tokens_per_minute:
                raise BudgetExceeded("per-minute token cap reached", 60 - (self.clock() - self._tok[0][0]) + 1)
        worst = self.cost(est_input_tokens, max_output_tokens)
        if self._spent + worst > self.b.max_cost_per_day:
            raise BudgetExceeded("daily spend cap would be exceeded")
        self._calls.append(self.clock())
        self._tok.append((self.clock(), need))
        self._day_calls += 1

    def record(self, in_tokens: int, out_tokens: int) -> float:
        self._roll()
        c = self.cost(in_tokens, out_tokens)
        self._spent += c
        self._day_tokens += in_tokens + out_tokens
        if self.on_alert and not self._alerted and self._spent >= self.b.alert_fraction * self.b.max_cost_per_day:
            self._alerted = True
            self.on_alert(f"spend is at {self._spent / self.b.max_cost_per_day:.0%} of the daily cap")
        return c

    @property
    def spent_today(self) -> float:
        self._roll()
        return self._spent
