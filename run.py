"""App entrypoint — starts the server bound to localhost.

This is the recommended way to run the project:

    python3 run.py            # http://127.0.0.1:8000
    python3 run.py --port 9000

The host choice is NOT configurable on purpose. The app downloads videos under
the network identity of whoever hosts it and has no authentication, no rate
limit and no other defense: exposed to the internet, it becomes a public
download service running on your IP. Keeping the bind on 127.0.0.1 is what
guarantees its reach is only this machine.

Running it through `uvicorn server:app` directly also works, but then the host
is on you — see the note about `--host` in the README.
"""

from __future__ import annotations

import argparse

import uvicorn

HOST = "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube Downloader local")
    parser.add_argument(
        "--port", type=int, default=8000, help="local port (default: 8000)"
    )
    parser.add_argument(
        "--reload", action="store_true", help="reload when the code is edited"
    )
    args = parser.parse_args()

    print(f"Serving at http://{HOST}:{args.port}  (this machine only)")
    uvicorn.run("server:app", host=HOST, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
