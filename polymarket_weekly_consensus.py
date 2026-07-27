#!/usr/bin/env python3
"""Find open Polymarket positions shared by top weekly sports traders."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_TOP_TRADERS = 20
DEFAULT_MIN_TRADERS_PER_POSITION = 3
DEFAULT_MAX_DEVIATION = 0.10


@dataclass(frozen=True)
class Trader:
    address: str
    name: str


@dataclass(frozen=True)
class Position:
    key: str
    market: str
    outcome: str
    average_price_paid: float


def _http_get_json(url: str, timeout: int) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _first_list(payload: Any, candidates: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in candidates:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    return []


def extract_top_traders(payload: Any, limit: int) -> list[Trader]:
    rows = _first_list(payload, ("data", "leaderboard", "results", "traders", "users"))

    mapped: list[tuple[int | None, Trader]] = []
    for row in rows:
        address = (
            row.get("proxyWallet")
            or row.get("walletAddress")
            or row.get("address")
            or row.get("wallet")
            or row.get("userAddress")
        )
        if not isinstance(address, str) or not address.strip():
            continue

        name = row.get("name") or row.get("username") or row.get("displayName") or address

        rank = row.get("rank")
        if isinstance(rank, str) and rank.isdigit():
            rank = int(rank)
        if not isinstance(rank, int):
            rank = None

        mapped.append((rank, Trader(address=address, name=str(name))))

    mapped.sort(key=lambda pair: pair[0] if pair[0] is not None else 10**9)

    seen: set[str] = set()
    traders: list[Trader] = []
    for _, trader in mapped:
        key = trader.address.lower()
        if key in seen:
            continue
        seen.add(key)
        traders.append(trader)
        if len(traders) >= limit:
            break

    return traders


def _maybe_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def extract_open_positions(payload: Any) -> list[Position]:
    rows = _first_list(payload, ("data", "positions", "results", "items"))

    positions: list[Position] = []
    for row in rows:
        size = _maybe_float(row.get("size") or row.get("amount") or row.get("shares") or row.get("balance"))
        is_open = row.get("isOpen")
        closed = row.get("closed")
        status = row.get("status")

        if isinstance(is_open, bool):
            if not is_open:
                continue
        elif isinstance(closed, bool):
            if closed:
                continue
        elif isinstance(status, str) and status.lower() in {"closed", "resolved", "settled"}:
            continue

        if size is not None and size <= 0:
            continue

        avg_price = _maybe_float(
            row.get("avgPrice")
            or row.get("averagePrice")
            or row.get("entryPrice")
            or row.get("price")
            or row.get("costBasis")
        )
        if avg_price is None:
            continue

        token_id = row.get("tokenId") or row.get("assetId") or row.get("outcomeId") or row.get("id")
        market_id = row.get("marketId") or row.get("conditionId")

        market = row.get("marketQuestion") or row.get("question") or row.get("title") or row.get("market") or "Unknown market"
        outcome = row.get("outcome") or row.get("outcomeName") or row.get("side") or "Unknown outcome"

        base_key = token_id or market_id or f"{market}::{outcome}"
        key = f"{base_key}::{outcome}" if token_id or market_id else str(base_key)

        positions.append(
            Position(
                key=str(key),
                market=str(market),
                outcome=str(outcome),
                average_price_paid=avg_price,
            )
        )

    return positions


def _fetch_top_traders(base_url: str, limit: int, timeout: int) -> list[Trader]:
    routes = [
        "/leaderboard/sports/weekly/profit",
        f"/leaderboard/sports/weekly/profit?limit={limit}",
        f"/leaderboard?category=sports&window=weekly&metric=profit&limit={limit}",
    ]

    errors: list[str] = []
    for route in routes:
        url = urllib.parse.urljoin(base_url, route)
        try:
            payload = _http_get_json(url, timeout=timeout)
            traders = extract_top_traders(payload, limit)
            if traders:
                return traders
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"{url} -> {exc}")

    joined = "\n".join(errors) if errors else "no response payloads contained traders"
    raise RuntimeError(f"Unable to fetch top traders. Attempts:\n{joined}")


def _fetch_open_positions_for_trader(base_url: str, trader: Trader, timeout: int) -> list[Position]:
    encoded_address = urllib.parse.quote(trader.address)
    routes = [
        f"/positions?user={encoded_address}&openOnly=true",
        f"/positions?address={encoded_address}&openOnly=true",
        f"/positions?proxyWallet={encoded_address}&openOnly=true",
        f"/users/{encoded_address}/positions?openOnly=true",
        f"/traders/{encoded_address}/positions?openOnly=true",
    ]

    for route in routes:
        url = urllib.parse.urljoin(base_url, route)
        try:
            payload = _http_get_json(url, timeout=timeout)
            positions = extract_open_positions(payload)
            if positions:
                return positions
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            continue

    return []


def find_consensus_positions(
    traders: list[Trader],
    positions_by_trader: dict[str, list[Position]],
    min_traders: int,
    max_deviation: float,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[Trader, Position]]] = {}
    trader_lookup = {trader.address.lower(): trader for trader in traders}

    for address, positions in positions_by_trader.items():
        trader = trader_lookup.get(address.lower())
        if trader is None:
            continue
        for position in positions:
            grouped.setdefault(position.key, []).append((trader, position))

    output: list[dict[str, Any]] = []
    for _, entries in grouped.items():
        if len(entries) < min_traders:
            continue

        ordered_entries = sorted(entries, key=lambda entry: entry[1].average_price_paid)
        qualifying: list[tuple[Trader, Position]] = []
        for start in range(len(ordered_entries)):
            for end in range(start + min_traders - 1, len(ordered_entries)):
                candidate = ordered_entries[start : end + 1]
                avg_price = statistics.mean([entry[1].average_price_paid for entry in candidate])
                if all(abs(entry[1].average_price_paid - avg_price) <= max_deviation for entry in candidate):
                    if len(candidate) > len(qualifying):
                        qualifying = candidate

        if len(qualifying) < min_traders:
            continue

        market = qualifying[0][1].market
        outcome = qualifying[0][1].outcome

        output.append(
            {
                "market": market,
                "outcome": outcome,
                "average_price_paid": round(statistics.mean([entry[1].average_price_paid for entry in qualifying]), 4),
                "holders_count": len(qualifying),
                "holders": [
                    {
                        "trader": entry[0].name,
                        "address": entry[0].address,
                        "price_paid": round(entry[1].average_price_paid, 4),
                    }
                    for entry in qualifying
                ],
            }
        )

    output.sort(key=lambda row: row["holders_count"], reverse=True)
    return output


def run(
    data_api_base: str,
    top_traders: int,
    min_traders: int,
    max_deviation: float,
    timeout: int,
) -> list[dict[str, Any]]:
    traders = _fetch_top_traders(data_api_base, limit=top_traders, timeout=timeout)

    positions_by_trader: dict[str, list[Position]] = {}
    for trader in traders:
        positions_by_trader[trader.address] = _fetch_open_positions_for_trader(data_api_base, trader, timeout=timeout)

    return find_consensus_positions(
        traders=traders,
        positions_by_trader=positions_by_trader,
        min_traders=min_traders,
        max_deviation=max_deviation,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find open positions held by at least 3 of the top 20 weekly sports traders "
            "where each entry is within 10 cents of the group's average price paid."
        )
    )
    parser.add_argument("--data-api-base", default="https://data-api.polymarket.com", help="Data API base URL")
    parser.add_argument("--top-traders", type=int, default=DEFAULT_TOP_TRADERS, help="Number of top traders to evaluate")
    parser.add_argument(
        "--min-traders",
        type=int,
        default=DEFAULT_MIN_TRADERS_PER_POSITION,
        help="Minimum number of traders that must hold a position",
    )
    parser.add_argument(
        "--max-deviation",
        type=float,
        default=DEFAULT_MAX_DEVIATION,
        help="Maximum absolute deviation from the position's average price",
    )
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout (seconds)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        result = run(
            data_api_base=args.data_api_base,
            top_traders=args.top_traders,
            min_traders=args.min_traders,
            max_deviation=args.max_deviation,
            timeout=args.timeout,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.pretty:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
