"""Run the complete suite against a temporary local PostgreSQL cluster."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy.engine import URL


def main() -> int:
    root = Path.cwd()
    if not (root / "alembic.ini").is_file() or not (root / "tests").is_dir():
        raise SystemExit("Run this script from the football-analytics project root")
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()):
        raise SystemExit("PostgreSQL binaries initdb and pg_ctl must be available on PATH")
    initdb, pg_ctl = str(binaries["initdb"]), str(binaries["pg_ctl"])
    # A short /tmp socket path avoids PostgreSQL's Unix socket filename limit.
    temporary = Path(tempfile.mkdtemp(prefix="football-tests-", dir="/tmp"))
    data = temporary / "data"
    environment = os.environ.copy()
    url = URL.create(
        "postgresql+psycopg",
        username="football_test",
        host="localhost",
        database="postgres",
        port=5432,
        query={"host": str(temporary)},
    ).render_as_string(hide_password=False)
    environment.update(DATABASE_URL=url, TEST_DATABASE_URL=url)
    started = False
    try:
        subprocess.run(
            [
                initdb,
                "-D",
                str(data),
                "-A",
                "trust",
                "-U",
                "football_test",
                "--encoding=UTF8",
                "--no-locale",
            ],
            check=True,
        )
        started = True
        # No TCP listener: the isolated cluster is accessible via its own socket.
        subprocess.run(
            [
                pg_ctl,
                "-D",
                str(data),
                "-l",
                str(temporary / "postgres.log"),
                "-o",
                shlex.join(["-h", "", "-p", "5432", "-k", str(temporary)]),
                "-w",
                "start",
            ],
            check=True,
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=root,
            env=environment,
            check=True,
        )
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-rs"],
            cwd=root,
            env=environment,
            check=False,
        ).returncode
    finally:
        stopped = not started
        if started:
            stopped = (
                subprocess.run(
                    [pg_ctl, "-D", str(data), "-m", "fast", "-w", "stop"],
                    check=False,
                ).returncode
                == 0
            )
        if stopped:
            shutil.rmtree(temporary)
        else:
            print(f"PostgreSQL shutdown failed; preserved cluster: {temporary}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
