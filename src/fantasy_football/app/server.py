"""Loopback-only local application server entry point."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from threading import Event, Thread

LOOPBACK_HOST = "127.0.0.1"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local fantasy-football API")
    parser.add_argument("--database", default="outputs/fantasy_football.sqlite3")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--frontend", default="frontend/dist")
    parser.add_argument("--shutdown-file")
    parser.add_argument("--run-id")
    parser.add_argument("--stdout-log")
    parser.add_argument("--stderr-log")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if bool(args.shutdown_file) != bool(args.run_id):
        parser.error("--shutdown-file and --run-id must be supplied together")

    stdout_stream = None
    stderr_stream = None
    if args.stdout_log:
        stdout_stream = Path(args.stdout_log).open("a", encoding="utf-8", buffering=1)
        sys.stdout = stdout_stream
    if args.stderr_log:
        stderr_stream = Path(args.stderr_log).open("a", encoding="utf-8", buffering=1)
        sys.stderr = stderr_stream

    import uvicorn

    from fantasy_football.app.http import create_app
    from fantasy_football.cgl_history import CglHistoryError, load_cgl_history

    history = None
    archive = os.environ.get("WAR_ROOM_HISTORY_ARCHIVE")
    source = os.environ.get("WAR_ROOM_HISTORY_SOURCE")
    league = os.environ.get("WAR_ROOM_HISTORY_LEAGUE_ID")
    if any(value is not None for value in (archive, source, league)):
        if (
            not archive or not source or not league
            or not league.isascii() or not league.isdecimal()
        ):
            parser.error(
                "Configure WAR_ROOM_HISTORY_ARCHIVE, WAR_ROOM_HISTORY_SOURCE, "
                "and WAR_ROOM_HISTORY_LEAGUE_ID together."
            )
        try:
            history = load_cgl_history(
                Path(archive), Path(source), expected_league_id=int(league)
            )
        except CglHistoryError as error:
            parser.error(str(error))

    app = create_app(
        database_path=args.database,
        frontend_directory=args.frontend,
        launcher_run_id=args.run_id,
        history=history,
    )
    configuration = uvicorn.Config(app, host=LOOPBACK_HOST, port=args.port)
    server = uvicorn.Server(configuration)
    watcher_stop = Event()

    def watch_for_shutdown() -> None:
        shutdown_path = Path(args.shutdown_file)
        while not watcher_stop.wait(0.2):
            try:
                requested_run = shutdown_path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                continue
            if requested_run == args.run_id:
                server.should_exit = True
                return

    watcher = None
    if args.shutdown_file:
        watcher = Thread(
            target=watch_for_shutdown,
            name="local-shutdown-watcher",
            daemon=True,
        )
        watcher.start()
    try:
        server.run()
    finally:
        watcher_stop.set()
        if watcher is not None:
            watcher.join(timeout=1)
        if args.shutdown_file:
            shutdown_path = Path(args.shutdown_file)
            try:
                if shutdown_path.read_text(encoding="utf-8").strip() == args.run_id:
                    shutdown_path.unlink()
            except FileNotFoundError:
                pass
        if stdout_stream is not None:
            stdout_stream.close()
        if stderr_stream is not None:
            stderr_stream.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
