from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import struct
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from app.backends.config import BackendConfig


PLAYER_CONTRIBUTION_COLUMNS = ",".join(
    [
        "customerType",
        "bankLabelName",
        "clubLabelName",
        "operationLabelName",
        "masterAgentName",
        "customerName",
        "referrerName",
        "referrerAgentName",
        "fundsIn",
        "creditAdj",
        "deposit",
        "depositCounts",
        "depositDays",
        "transferIn",
        "fundsOut",
        "debitAdj",
        "withdraw",
        "withdrawCounts",
        "transferOut",
        "gameBetting",
        "validGameBetting",
        "gameWinning",
        "promotion",
        "gameRebate",
        "referralRebate",
        "agentCommission",
        "dailySalary",
        "profitSharing",
        "pnl",
        "gameDividend",
        "totalOpeningBalance",
        "totalClosingBalance",
        "loginCounts",
        "lastLoginTime",
        "lastLoginIp",
        "lastDepositTime",
        "lastBettingTime",
        "regDate",
        "registerIp",
        "firstDepositDate",
        "firstDepositAmount",
    ]
)


class BackendApiError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code} {message}".strip())
        self.code = code


class UrllibJsonTransport:
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        request = Request(url, data=body, headers=headers or {}, method=method)
        with urlopen(request, timeout=timeout_seconds) as response:
            data = response.read()
        return json.loads(data.decode("utf-8"))


class TacBackendClient:
    def __init__(self, config: BackendConfig, transport: Any | None = None) -> None:
        self.config = config
        self.transport = transport or UrllibJsonTransport()
        self.authorization = config.authorization

    def __repr__(self) -> str:
        return f"TacBackendClient(config={self.config!r})"

    def login_password(self, merchant_code: str | None = None) -> str:
        if not self.config.login_operator or not self.config.login_password:
            raise BackendApiError("FAILED_CONFIG", "backend login operator/password is required")
        merchant = merchant_code or self.config.login_merchant or self.config.merchant_code
        if not merchant:
            raise BackendApiError("FAILED_CONFIG", "backend merchant code is required")
        body = json.dumps(
            {"operatorName": self.config.login_operator, "password": self.config.login_password},
            ensure_ascii=False,
        ).encode("utf-8")
        base_url = _base_url(self.config)
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Merchant": merchant,
            "Referer": f"{base_url}/{merchant}",
            "Origin": base_url,
        }
        result = self.transport.request(
            "POST",
            _join_url(base_url, "/tac/api/login/password"),
            headers=headers,
            body=body,
            timeout_seconds=self.config.request_timeout_seconds,
        )
        token = result.get("token") if isinstance(result, dict) else None
        if not token:
            raise BackendApiError("FAILED_BACKEND_QUERY", "login response missing token")
        self.authorization = str(token)
        return self.authorization

    def login_otp(self, merchant_code: str | None = None) -> str:
        if not self.config.login_operator or not self.config.totp_secret:
            raise BackendApiError("FAILED_CONFIG", "backend login operator/totp secret is required")
        merchant = merchant_code or self.config.login_merchant or self.config.merchant_code
        if not merchant:
            raise BackendApiError("FAILED_CONFIG", "backend merchant code is required")
        code = generate_totp(self.config.totp_secret)
        body = json.dumps(
            {"operatorName": self.config.login_operator, "code": code},
            ensure_ascii=False,
        ).encode("utf-8")
        base_url = _base_url(self.config)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Merchant": merchant,
            "MerchantCode": merchant,
            "language": "zh_CN",
            "Referer": f"{base_url}/{merchant}",
            "Origin": base_url,
            "User-Agent": "Mozilla/5.0",
        }
        result = self.transport.request(
            "POST",
            _join_url(base_url, "/tac/api/login/otp"),
            headers=headers,
            body=body,
            timeout_seconds=self.config.request_timeout_seconds,
        )
        token = result.get("token") if isinstance(result, dict) else None
        if not token:
            code_value = str(result.get("errorCode") or "FAILED_BACKEND_QUERY") if isinstance(result, dict) else "FAILED_BACKEND_QUERY"
            message = str(result.get("message") or "otp login response missing token") if isinstance(result, dict) else "otp login response missing token"
            raise BackendApiError(code_value, message)
        self.authorization = str(token)
        return self.authorization

    def login(self, merchant_code: str | None = None) -> str:
        if self.config.totp_secret:
            return self.login_otp(merchant_code)
        return self.login_password(merchant_code)

    def api_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        merchant_code: str | None = None,
        _retry_invalid_token: bool = True,
    ) -> dict[str, Any]:
        if not self.authorization:
            self.login(merchant_code)
        result = self._api_get_once(path, params or {}, merchant_code)
        if result.get("errorCode") == "INVALID_TOKEN" and _retry_invalid_token:
            self.login(merchant_code)
            result = self._api_get_once(path, params or {}, merchant_code)
        return result

    def query_player_user(self, account_or_phone: str) -> dict[str, Any] | None:
        mc = self._merchant_code()
        for search_code in ("USERNAME", "MOBILE"):
            result = self.api_get(
                "/tac/api/relay/get/player-search-non-bankcard",
                {
                    "merchantCode": mc,
                    "isWildcard": "false",
                    "sortType": "desc",
                    "pageable": "true",
                    "data": account_or_phone,
                    "searchCode": search_code,
                },
                mc,
            )
            assert_backend_success(result, f"player search {search_code}")
            rows = extract_rows(result)
            exact = next((row for row in rows if _matches_identity(row, account_or_phone)), None)
            picked = exact or (rows[0] if rows else None)
            if picked:
                return {
                    "search_code": search_code,
                    "raw": picked,
                    "customer_id": picked.get("customerId") or picked.get("id") or picked.get("customerID"),
                    "customer_name": picked.get("customerName") or picked.get("username") or picked.get("loginName") or account_or_phone,
                }
        return None

    def query_deposit(self, username: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
        mc = self._merchant_code()
        result = self.api_get(
            "/tac/api/relay/get/pv2-mcs-internal-v3-player-deposit-search",
            {
                "searchDateMode": "requestTime",
                "dateFrom": f"{date_from} 00:00:00",
                "dateTo": f"{date_to} 23:59:59",
                "merchantCode": mc,
                "pageSize": 50,
                "pageNo": 1,
                "username": username,
                "sortBy": "",
                "sortOrder": "",
                "pid": "610151",
            },
            mc,
        )
        assert_backend_success(result, "deposit search")
        return extract_rows(result)

    def query_turnover_requirement(self, account_or_phone: str) -> dict[str, Any]:
        mc = self._merchant_code()
        player = self.query_player_user(account_or_phone)
        windows = build_turnover_requirement_queries(
            self.config.default_lookback_days,
            self.config.fallback_lookback_days,
        )
        if not player or not player.get("customer_id"):
            first = windows[0]
            return {
                "player_found": False,
                "customer_id": None,
                "customer_name": None,
                "active_requirements_count": 0,
                "remaining_turnover": 0,
                "required_turnover": 0,
                "valid_turnover": 0,
                "is_met": False,
                "records": [],
                "query_windows": [first],
            }

        best: dict[str, Any] | None = None
        query_windows = []
        for window in windows:
            result = self.api_get(
                "/tac/api/relay/get/mcs-player-promotion-turnover-checking-getTurnoverCheckingRecord",
                {
                    "merchantCode": mc,
                    "customerId": player["customer_id"],
                    "dateType": window["date_type"],
                    "startDate": window["start_date"],
                    "endDate": window["end_date"],
                    "pageNo": 1,
                    "pageSize": 20,
                },
                mc,
            )
            assert_backend_success(result, f"turnover requirement search {window['query_mode']}")
            records = [normalize_turnover_requirement_record(row) for row in extract_rows(result)]
            summary = summarize_turnover_requirement(account_or_phone, player, window, records)
            query_windows.append(
                {
                    **window,
                    "records_count": summary["records_count"],
                    "active_requirements_count": summary["active_requirements_count"],
                    "remaining_turnover": summary["remaining_turnover"],
                }
            )
            summary["query_windows"] = list(query_windows)
            if summary["active_requirements_count"] > 0:
                return summary
            if best is None or summary["records_count"] > best["records_count"]:
                best = summary
        return best or summarize_turnover_requirement(account_or_phone, player, windows[0], [])

    def query_player_contribution(self, username: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
        mc = self._merchant_code()
        result = self.api_get(
            "/tac/api/relay/post/ods-v2-report-player-contributionv2-search",
            {
                "subordinateNames": username,
                "subordinateMapType": "AGENT",
                "subordinateType": "DIRECT",
                "startDate": date_from,
                "endDate": date_to,
                "columnsList": PLAYER_CONTRIBUTION_COLUMNS,
                "merchantCode": mc,
                "size": 50,
                "page": 1,
                "pageable": "true",
            },
            mc,
        )
        assert_backend_success(result, "player contribution search")
        return extract_rows(result)

    def _api_get_once(self, path: str, params: dict[str, Any], merchant_code: str | None) -> dict[str, Any]:
        mc = merchant_code or self._merchant_code()
        url = _join_url(_base_url(self.config), path)
        query = urlencode(params)
        if query:
            url = f"{url}?{query}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Authorization": self.authorization or "",
            "Merchant": mc,
            "merchantCode": mc,
            "Language": "zh_CN",
            "environment": "TCG1",
            "platform": "TCG",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        return self.transport.request(
            "GET",
            url,
            headers=headers,
            body=None,
            timeout_seconds=self.config.request_timeout_seconds,
        )

    def _merchant_code(self) -> str:
        if not self.config.merchant_code:
            raise BackendApiError("FAILED_CONFIG", "backend merchant code is required")
        return self.config.merchant_code


def assert_backend_success(result: dict[str, Any], context: str = "backend API") -> dict[str, Any]:
    if result and result.get("success") is False:
        code = str(result.get("errorCode") or result.get("code") or "UNKNOWN_BACKEND_ERROR")
        message = str(result.get("message") or result.get("msg") or context)
        raise BackendApiError(code, message)
    return result


def generate_totp(secret: str, *, for_time: int | None = None, digits: int = 6, interval_seconds: int = 30) -> str:
    normalized = "".join(secret.strip().split()).upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    try:
        key = base64.b32decode(normalized + padding, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise BackendApiError("FAILED_CONFIG", "backend_totp_secret must be base32 encoded") from exc
    counter = int((for_time if for_time is not None else time.time()) // interval_seconds)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def extract_rows(result: Any) -> list[dict[str, Any]]:
    if not result:
        return []
    candidates = [
        _get(result, "value"),
        _get(result, "value", "list"),
        _get(result, "value", "data"),
        _get(result, "value", "records"),
        _get(result, "value", "rows"),
        _get(result, "value", "items"),
        _get(result, "value", "content"),
        _get(result, "value", "result"),
        _get(result, "data"),
        _get(result, "data", "list"),
        _get(result, "data", "data"),
        _get(result, "data", "records"),
        _get(result, "data", "rows"),
        _get(result, "data", "items"),
        _get(result, "data", "content"),
        _get(result, "records"),
        _get(result, "rows"),
        _get(result, "items"),
        _get(result, "content"),
        _get(result, "result"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
    queue = [node for node in (_get(result, "value"), _get(result, "data"), _get(result, "result")) if node]
    seen: set[int] = set()
    while queue:
        node = queue.pop(0)
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, list) and any(isinstance(row, dict) for row in node):
            return [row for row in node if isinstance(row, dict)]
        if isinstance(node, dict):
            queue.extend(value for value in node.values() if isinstance(value, (dict, list)))
    return []


def match_deposit(records: list[dict[str, Any]], target_amount: float | int | str | None, target_ref: str | None = None) -> dict[str, Any] | None:
    if target_amount is None:
        return None
    amount = to_money_number(target_amount)
    ref = str(target_ref or "").upper()
    for record in records:
        record_amount = to_money_number(record.get("requestAmount") or record.get("depositAmount") or record.get("amount"))
        record_ref = str(record.get("bankRef") or record.get("tpRefNo") or "").upper()
        if abs(record_amount - amount) > 0.01:
            continue
        if ref and ref not in record_ref:
            continue
        return record
    return None


def build_turnover_requirement_queries(default_days: int = 30, fallback_days: int = 90, now: datetime | None = None) -> list[dict[str, str]]:
    now = now or datetime.now()
    return [
        {"query_mode": "custom_recent", "date_type": "C", **_range_days(default_days, now)},
        {"query_mode": "custom_month", "date_type": "C", **_month_range(now)},
        {"query_mode": "last_withdrawal_window", "date_type": "W", **_last_withdrawal_window(now)},
        {"query_mode": "last_withdrawal_recent", "date_type": "W", **_range_days(default_days, now)},
        {"query_mode": "custom_lookback", "date_type": "C", **_range_days(fallback_days, now)},
    ]


def normalize_turnover_requirement_record(record: dict[str, Any]) -> dict[str, Any]:
    status = first_present(
        record,
        [
            "statusName",
            "turnoverStatusName",
            "turnoverCheckingStatusName",
            "checkingStatusName",
            "auditStatusName",
            "completeStatusName",
            "status",
            "turnoverStatus",
            "turnoverCheckingStatus",
            "checkingStatus",
            "statusI18n",
            "state",
            "auditStatus",
            "completeStatus",
        ],
    )
    remaining = to_money_number(
        first_present(
            record,
            [
                "remainingTurnover",
                "remainingFlow",
                "remainingWater",
                "remainingWaterAmount",
                "remainTurnover",
                "remainFlow",
                "remainWater",
                "remainingRollover",
                "remainRollover",
                "remainingAmount",
                "remainAmount",
                "leftTurnover",
                "leftAmount",
                "surplusTurnover",
                "remainValidBet",
                "unfinishTurnover",
                "unfinishedTurnover",
                "unCompletedTurnover",
                "uncompletedTurnover",
                "remainingBet",
                "remainBet",
                "requiredBettingRemaining",
                "turnoverBalance",
            ],
        )
    )
    required = to_money_number(
        first_present(
            record,
            [
                "turnoverRequirement",
                "turnoverRequirementAmount",
                "turnoverAmount",
                "requiredTurnover",
                "requireTurnover",
                "turnoverRequired",
                "bettingRequirement",
                "requiredBetting",
                "requirementAmount",
                "requiredAmount",
                "flowRequirement",
                "waterRequirement",
                "betRequirement",
                "targetTurnover",
            ],
        )
    )
    valid = to_money_number(
        first_present(
            record,
            [
                "validTurnover",
                "validFlow",
                "validWater",
                "validBetting",
                "validGameBetting",
                "effectiveTurnover",
                "effectiveBetting",
                "completedTurnover",
                "completedAmount",
                "finishedTurnover",
                "accumulatedTurnover",
            ],
        )
    )
    has_status = status is not None and str(status).strip() != ""
    is_incomplete = is_incomplete_turnover_status(status) or (not has_status and remaining > 0)
    return {
        "transaction_time": first_present(record, ["transactionTime", "transactionDate", "txnTime", "createTime", "createdTime", "requestDate", "lastUpdateTime"]),
        "transaction_type": first_present(record, ["transactionTypeName", "transactionType", "txTypeName", "typeName", "type"]),
        "transaction_id": first_present(record, ["transactionId", "transactionNo", "depositId", "orderId", "id"]),
        "amount": to_money_number(first_present(record, ["amount", "transactionAmount", "depositAmount", "requestAmount"])),
        "required_turnover": required,
        "valid_turnover": valid,
        "remaining_turnover": remaining,
        "status": status,
        "is_incomplete": is_incomplete,
    }


def summarize_turnover_requirement(account_or_phone: str, player: dict[str, Any], window: dict[str, str], records: list[dict[str, Any]]) -> dict[str, Any]:
    active = [record for record in records if record.get("is_incomplete")]
    remaining = round_money(sum(to_money_number(record.get("remaining_turnover")) for record in active))
    required = round_money(sum(to_money_number(record.get("required_turnover")) for record in active)) if active else None
    valid = round_money(sum(to_money_number(record.get("valid_turnover")) for record in active)) if active else None
    return {
        "account_or_phone": account_or_phone,
        "player_found": True,
        "customer_id": player.get("customer_id"),
        "customer_name": player.get("customer_name"),
        "active_requirements_count": len(active),
        "remaining_turnover": remaining,
        "required_turnover": required,
        "valid_turnover": valid,
        "is_met": len(active) == 0 or remaining <= 0,
        "records": records,
        "active_requirements": active,
        "records_count": len(records),
        "active_requirements_count": len(active),
        "query_mode": window["query_mode"],
        "date_type": window["date_type"],
        "start_date": window["start_date"],
        "end_date": window["end_date"],
        "query_windows": [],
    }


def first_present(record: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if record.get(key) not in (None, ""):
            return record[key]
    return None


def is_incomplete_turnover_status(value: Any) -> bool:
    status = str(value or "").strip().upper()
    return bool(
        status
        and (
            "未完成" in status
            or "未达成" in status
            or "未達成" in status
            or "未满足" in status
            or "未滿足" in status
            or "INCOMPLETE" in status
            or "UNFINISHED" in status
            or "PENDING" in status
            or status in {"I", "N", "NO", "OPEN"}
        )
    )


def is_complete_turnover_status(value: Any) -> bool:
    status = str(value or "").strip().upper()
    return bool(
        status
        and (
            "完成" in status
            or "已达成" in status
            or "已達成" in status
            or "已满足" in status
            or "已滿足" in status
            or "COMPLETE" in status
            or "COMPLETED" in status
            or "FINISHED" in status
            or "CLOSED" in status
            or status in {"C", "Y", "YES", "DONE"}
        )
    )


def to_money_number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        decimal = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        cleaned = "".join(ch for ch in str(value) if ch.isdigit() or ch in ".-")
        try:
            decimal = Decimal(cleaned or "0")
        except InvalidOperation:
            return 0.0
    return float(decimal)


def round_money(value: Any) -> float:
    return float(Decimal(str(to_money_number(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _matches_identity(row: dict[str, Any], account_or_phone: str) -> bool:
    wanted = str(account_or_phone)
    return any(str(row.get(key)) == wanted for key in ("customerName", "username", "loginName", "mobile", "phone") if row.get(key))


def _get(value: Any, *keys: str) -> Any:
    node = value
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _range_days(days: int, now: datetime) -> dict[str, str]:
    start = now - timedelta(days=days)
    return {"start_date": f"{start:%Y-%m-%d} 00:00:00", "end_date": f"{now:%Y-%m-%d} 23:59:59"}


def _month_range(now: datetime) -> dict[str, str]:
    start = now.replace(day=1)
    return {"start_date": f"{start:%Y-%m-%d} 00:00:00", "end_date": f"{now:%Y-%m-%d} 23:59:59"}


def _last_withdrawal_window(now: datetime) -> dict[str, str]:
    start = now - timedelta(days=1)
    return {"start_date": f"{start:%Y-%m-%d} 12:00:00", "end_date": f"{now:%Y-%m-%d} 23:59:59"}


def _base_url(config: BackendConfig) -> str:
    if not config.base_url:
        raise BackendApiError("FAILED_CONFIG", "backend base_url is required")
    return config.base_url.rstrip("/")


def _join_url(base_url: str, path: str) -> str:
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))
