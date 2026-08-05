from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


class OrderSellerReadPort(Protocol):
    def get_order(self, order_id: str) -> Mapping[str, str]: ...

    def get_items(self, order_id: str) -> Sequence[Mapping[str, str]]: ...

    def is_known_seller(self, seller_id: str) -> bool: ...


class PaymentReadPort(Protocol):
    def get_payments(self, order_id: str) -> Sequence[Mapping[str, str]]: ...


class PolicyProposer(Protocol):
    status: str

    def load(self) -> "PolicyProposer": ...

    def propose_policy(self, handoffs: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]: ...

    def snapshot(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


class TraceSink(Protocol):
    run_id: str

    def emit(
        self,
        case_id: str | None,
        agent: str,
        event: str,
        payload: Any | None = None,
        *,
        from_agent: str | None = None,
        to_agent: str | None = None,
    ) -> None: ...

    def flush(self, paths: Sequence[Path]) -> None: ...

