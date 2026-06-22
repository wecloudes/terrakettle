"""CLI entrypoint: run the server, or manage projects/tokens locally."""

import argparse
import sys

from . import auth, db, retention
from .__init__ import __version__


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="terrakettle")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="Run the web server")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")

    p_proj = sub.add_parser("create-project", help="Create a project")
    p_proj.add_argument("slug")
    p_proj.add_argument("--name")

    p_tok = sub.add_parser("mint-token", help="Mint a push token for a project")
    p_tok.add_argument("slug")
    p_tok.add_argument("--label")
    p_tok.add_argument("--ttl-days", type=int, default=None,
                       help="Token lifetime in days (default: server setting)")

    p_del = sub.add_parser("delete-project",
                           help="Delete a project, its runs, and stored files")
    p_del.add_argument("slug")

    p_lst = sub.add_parser("list-tokens", help="List a project's push tokens")
    p_lst.add_argument("slug")

    p_rev = sub.add_parser("revoke-token", help="Revoke a token by id")
    p_rev.add_argument("slug")
    p_rev.add_argument("token_id", type=int)

    p_prune = sub.add_parser("prune", help="Delete old runs and their files")
    p_prune.add_argument("--keep", type=int,
                         help="Keep newest N runs per project")
    p_prune.add_argument("--older-than", type=int, metavar="DAYS",
                         help="Delete runs older than DAYS")
    p_prune.add_argument("--project", help="Limit --keep to one project slug")

    args = parser.parse_args(argv)

    if args.cmd == "create-project":
        db.init_db()
        if db.get_project(args.slug):
            print(f"Project '{args.slug}' already exists", file=sys.stderr)
            return 1
        row = db.create_project(args.slug, args.name or args.slug)
        print(f"Created project: {row['slug']}")
        return 0

    if args.cmd == "mint-token":
        db.init_db()
        project = db.get_project(args.slug)
        if project is None:
            print(f"Unknown project '{args.slug}'", file=sys.stderr)
            return 1
        from .config import get_settings
        ttl = args.ttl_days if args.ttl_days is not None \
            else get_settings().token_ttl_days
        token = auth.generate_token(args.slug)
        db.add_token(project["id"], auth.hash_token(token), args.label,
                     auth.token_expiry(ttl))
        print(token)
        print("# Store this token now — it is not retrievable later.",
              file=sys.stderr)
        return 0

    if args.cmd == "delete-project":
        db.init_db()
        from .storage import get_storage
        runs = db.delete_project(args.slug)
        storage = get_storage()
        for run in runs:
            for key in (run["html_key"], run["data_js_key"], run["json_key"]):
                if key:
                    try:
                        storage.delete(key)
                    except Exception:
                        pass
        print(f"Deleted project '{args.slug}' and {len(runs)} run(s)")
        return 0

    if args.cmd == "list-tokens":
        db.init_db()
        project = db.get_project(args.slug)
        if project is None:
            print(f"Unknown project '{args.slug}'", file=sys.stderr)
            return 1
        tokens = db.list_tokens(project["id"])
        if not tokens:
            print("(no tokens)")
            return 0
        print(f"{'ID':>4}  {'LABEL':<20} {'CREATED':<28} LAST USED")
        for t in tokens:
            print(f"{t['id']:>4}  {(t['label'] or '-'):<20} "
                  f"{t['created_at']:<28} {t['last_used_at'] or 'never'}")
        return 0

    if args.cmd == "revoke-token":
        db.init_db()
        project = db.get_project(args.slug)
        if project is None:
            print(f"Unknown project '{args.slug}'", file=sys.stderr)
            return 1
        if db.revoke_token(project["id"], args.token_id) == 0:
            print(f"No token {args.token_id} for '{args.slug}'", file=sys.stderr)
            return 1
        print(f"Revoked token {args.token_id}")
        return 0

    if args.cmd == "prune":
        db.init_db()
        if not args.keep and not args.older_than:
            print("Specify --keep N and/or --older-than DAYS", file=sys.stderr)
            return 1
        pruned = 0
        if args.keep:
            if args.project:
                project = db.get_project(args.project)
                if project is None:
                    print(f"Unknown project '{args.project}'", file=sys.stderr)
                    return 1
                pruned += retention.prune_project(project["id"], args.keep)
            else:
                pruned += retention.prune_all_keep(args.keep)
        if args.older_than:
            pruned += retention.prune_older_than(args.older_than)
        print(f"Pruned {pruned} run(s)")
        return 0

    # Default: serve
    import uvicorn

    host = getattr(args, "host", "0.0.0.0")
    port = getattr(args, "port", 8000)
    reload = getattr(args, "reload", False)
    if reload:
        # Reload needs an import string; the worker re-imports the package.
        uvicorn.run("terrakettle.app:app", host=host, port=port, reload=True)
    else:
        # Pass the app object so we don't depend on import-string resolution
        # (a `terrakettle.py` shim in CWD would otherwise shadow the package).
        from .app import app
        uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
