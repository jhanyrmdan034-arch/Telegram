#!/usr/bin/env python3
"""Load test for the messaging API.

Creates real users and a real private chat, then measures throughput and latency
of the write path (send message) and read path (fetch history) concurrently.

    python bench.py --base-url http://localhost:8000 --messages 300 --concurrency 20

Requires: httpx, (optionally) a reachable API instance.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import time
import uuid

import httpx


async def _register(client: httpx.AsyncClient, prefix: str) -> dict:
    username = f"{prefix}{uuid.uuid4().hex[:8]}"
    response = await client.post("/api/v1/auth/register", json={
        "username": username, "password": "LoadTestPass123", "display_name": username,
    })
    response.raise_for_status()
    return response.json()


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


async def run(base_url: str, total_messages: int, concurrency: int) -> dict:
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base_url, timeout=30, limits=limits) as client:
        alice = await _register(client, "load_a")
        bob = await _register(client, "load_b")
        headers = {"Authorization": f"Bearer {alice['access_token']}"}

        bob_id = (await client.get("/api/v1/users/me",
                                   headers={"Authorization": f"Bearer {bob['access_token']}"})).json()["id"]
        chat = (await client.post("/api/v1/chats", headers=headers,
                                  json={"type": "private", "member_ids": [bob_id]})).json()

        write_latencies: list[float] = []
        read_latencies: list[float] = []
        errors: list[int] = []
        semaphore = asyncio.Semaphore(concurrency)

        async def send(index: int) -> None:
            async with semaphore:
                started = time.perf_counter()
                response = await client.post(
                    f"/api/v1/chats/{chat['id']}/messages", headers=headers,
                    json={"type": "text", "text": f"load message {index}"},
                )
                write_latencies.append((time.perf_counter() - started) * 1000)
                if response.status_code != 201:
                    errors.append(response.status_code)

        async def read() -> None:
            async with semaphore:
                started = time.perf_counter()
                response = await client.get(
                    f"/api/v1/chats/{chat['id']}/messages?limit=50", headers=headers
                )
                read_latencies.append((time.perf_counter() - started) * 1000)
                if response.status_code != 200:
                    errors.append(response.status_code)

        started = time.perf_counter()
        await asyncio.gather(*[send(i) for i in range(total_messages)])
        write_seconds = time.perf_counter() - started

        started = time.perf_counter()
        await asyncio.gather(*[read() for _ in range(max(10, total_messages // 3))])
        read_seconds = time.perf_counter() - started

        final = (await client.get(f"/api/v1/chats/{chat['id']}/messages?limit=1",
                                  headers=headers)).json()

    return {
        "messages_sent": total_messages,
        "write_rps": round(total_messages / write_seconds, 1),
        "write_p50_ms": round(statistics.median(write_latencies), 1),
        "write_p95_ms": round(_percentile(write_latencies, 95), 1),
        "write_max_ms": round(max(write_latencies), 1),
        "read_rps": round(len(read_latencies) / read_seconds, 1),
        "read_p95_ms": round(_percentile(read_latencies, 95), 1),
        "errors": len(errors),
        "last_seq": final["items"][0]["seq"] if final["items"] else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--messages", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=20)
    args = parser.parse_args()

    result = asyncio.run(run(args.base_url, args.messages, args.concurrency))
    print("load test result:")
    for key, value in result.items():
        print(f"  {key:>14}: {value}")

    # A run is only meaningful if ordering stayed strictly sequential.
    assert result["last_seq"] == args.messages, "message ordering broke under load"
    assert result["errors"] == 0, f"errors observed: {result['errors']}"


if __name__ == "__main__":
    main()
