"""Search-local path accounting for experimental execution."""

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True, eq=False)
class PathReservation:
    state_id: str
    count: int


@dataclass(frozen=True, slots=True)
class PathUsage:
    state_id: str
    simulated: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    reserved: int = 0
    active: int = 0
    released_after_failure: int = 0
    released_on_cancel: int = 0


@dataclass(frozen=True, slots=True)
class PathBudgetStatus:
    baseline_id: str
    states: tuple[PathUsage, ...]
    capacity: int

    @property
    def baseline_paths(self) -> int:
        return next(
            row.simulated for row in self.states if row.state_id == self.baseline_id
        )

    @property
    def hypothetical_paths(self) -> int:
        return self.total_paths - self.baseline_paths

    @property
    def total_paths(self) -> int:
        return sum(row.simulated for row in self.states)

    @property
    def remaining(self) -> int:
        return (
            self.capacity - self.total_paths - sum(row.reserved for row in self.states)
        )


class PathBudget:
    """Reserve unsimulated paths, charge entry to season execution, then settle."""

    def __init__(
        self,
        baseline_id: str,
        state_ids: tuple[str, ...],
        capacity: int,
        state_cap: int,
    ):
        if (
            baseline_id not in state_ids
            or len(state_ids) != len(set(state_ids))
            or any(
                type(value) is not int or value <= 0 for value in (capacity, state_cap)
            )
        ):
            raise ValueError("invalid path budget family or capacity")
        self._baseline_id = baseline_id
        self._capacity = capacity
        self._state_cap = state_cap
        self._usage = {state: PathUsage(state) for state in sorted(state_ids)}
        self._pending: dict[str, PathReservation] = {}

    @property
    def status(self) -> PathBudgetStatus:
        return PathBudgetStatus(
            self._baseline_id, tuple(self._usage.values()), self._capacity
        )

    def reserve(self, state_id: str, count: int) -> PathReservation:
        if state_id not in self._usage or state_id in self._pending:
            raise ValueError("unknown state or state already has reserved work")
        if type(count) is not int or count <= 0:
            raise ValueError("reservation count must be positive")
        row = self._usage[state_id]
        if count > self.status.remaining or row.simulated + count > self._state_cap:
            raise ValueError("simulation path budget exhausted")
        token = PathReservation(state_id, count)
        self._pending[state_id] = token
        self._usage[state_id] = replace(row, reserved=count)
        return token

    def _row(self, token: PathReservation) -> PathUsage:
        if self._pending.get(token.state_id) is not token:
            raise ValueError("unknown or settled path reservation")
        return self._usage[token.state_id]

    def start(self, token: PathReservation) -> None:
        row = self._row(token)
        if row.reserved == 0:
            raise ValueError("reservation has no unstarted paths")
        self._usage[token.state_id] = replace(
            row,
            simulated=row.simulated + 1,
            reserved=row.reserved - 1,
            active=row.active + 1,
        )

    def settle(
        self,
        token: PathReservation,
        *,
        completed: int,
        failed: int = 0,
        cancelled: int = 0,
        release: str | None = None,
    ) -> None:
        row = self._row(token)
        if (
            any(
                type(value) is not int or value < 0
                for value in (completed, failed, cancelled)
            )
            or completed + failed + cancelled != row.active
            or release not in (None, "failed", "cancelled")
            or (row.reserved and release is None)
        ):
            raise ValueError(
                "terminal counts must reconcile with charged and reserved work"
            )
        self._usage[token.state_id] = replace(
            row,
            completed=row.completed + completed,
            failed=row.failed + failed,
            cancelled=row.cancelled + cancelled,
            reserved=0,
            active=0,
            released_after_failure=row.released_after_failure
            + (row.reserved if release == "failed" else 0),
            released_on_cancel=row.released_on_cancel
            + (row.reserved if release == "cancelled" else 0),
        )
        del self._pending[token.state_id]
