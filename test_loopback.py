"""Checks the local-access lock in server.py.

The "runs only on your machine" guarantee is a behavior of the code, not a
promise in the README — so it has a test. Run it with:

    python3 test_loopback.py

It does not use pytest on purpose: the project has no test dependencies and
this is the only invariant that needs to be verified mechanically.
"""

from __future__ import annotations

import asyncio
import sys

import server

ALLOWED = ["127.0.0.1", "::1"]
BLOCKED = ["192.168.0.10", "203.0.113.7", "10.0.0.5", "172.16.0.1"]


async def _status_for(ip: str) -> int:
    """Calls the ASGI app directly, faking a request coming from `ip`."""
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": "/", "raw_path": b"/", "query_string": b"", "root_path": "",
        "scheme": "http", "headers": [(b"host", b"localhost")],
        "client": (ip, 1234), "server": ("127.0.0.1", 8000), "app": server.app,
    }
    captured: dict = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]

    await server.app(scope, receive, send)
    return captured["status"]


def main() -> int:
    failures = []

    for ip in ALLOWED:
        status = asyncio.run(_status_for(ip))
        ok = status == 200
        print(f"  {ip:15} -> {status} {'ok' if ok else 'FAILED (expected 200)'}")
        if not ok:
            failures.append(ip)

    for ip in BLOCKED:
        status = asyncio.run(_status_for(ip))
        ok = status == 403
        print(f"  {ip:15} -> {status} {'ok' if ok else 'FAILED (expected 403)'}")
        if not ok:
            failures.append(ip)

    if failures:
        print(f"\nFAILED: {len(failures)} case(s) — {', '.join(failures)}")
        return 1
    print("\nOK: local access allowed, external access blocked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
