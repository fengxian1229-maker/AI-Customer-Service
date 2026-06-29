from typing import Any, Protocol


class BackendProvider(Protocol):
    def query_turnover_requirement(self, account_or_phone: str) -> dict[str, Any]:
        ...

    def query_player_user(self, account_or_phone: str) -> dict[str, Any] | None:
        ...

    def query_deposit(self, username: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
        ...

    def query_player_contribution(self, username: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
        ...
