"""Command line: `kumo seed`, `kumo serve`."""

import argparse
import os

from . import db
from .seed import seed


def main() -> None:
    p = argparse.ArgumentParser(prog="kumo")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="fill an empty database with the demo data set")
    s.add_argument("--force", action="store_true", help="delete the database first")
    v = sub.add_parser("serve", help="run the web app (seeds an empty database first)")
    v.add_argument("--host", default="0.0.0.0")
    v.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = p.parse_args()

    if args.cmd == "seed":
        if args.force and db.db_path().exists():
            db.db_path().unlink()
        conn = db.connect()
        if conn.execute("SELECT COUNT(*) FROM deal").fetchone()[0]:
            print("Database is not empty; use --force to start again.")
            return
        print(seed(conn))
    elif args.cmd == "serve":
        conn = db.connect()
        if not conn.execute("SELECT COUNT(*) FROM deal").fetchone()[0]:
            print("Empty database: seeding demo data", seed(conn))
        conn.close()
        import uvicorn

        uvicorn.run("kumo.app:app", host=args.host, port=args.port)
