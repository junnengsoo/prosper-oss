from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import select

from .backup import create_pilot_backup
from .database.connection import SessionLocal, init_db
from .database.models import PropertyPlaybook
from .database.seed import DEFAULT_TEST_PLAYBOOK_PROPERTY_IDS, seed_all, seed_property_playbooks
from .services import get_all_config, update_config


def init_database() -> None:
    init_db()
    with SessionLocal() as session:
        seed_all(session)
    print("Initialized Prosper database and seeded sample data.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prosper operator utilities")
    subcommands = parser.add_subparsers(dest="command")

    subcommands.add_parser("init-db", help="Initialize the database and seed sample data")
    subcommands.add_parser("show-config", help="Show application config")

    backup = subcommands.add_parser("backup", help="Create a verified pilot backup archive")
    backup.add_argument("--output-dir", type=Path, help="Directory for the completed backup archive")
    backup.add_argument("--name", help="Archive file name; .tar.gz is added when omitted")

    set_config = subcommands.add_parser("set-config", help="Set one or more application config values")
    set_config.add_argument("pairs", nargs="+", help="Config updates as key=value")

    subcommands.add_parser("lock-sends", help="Enable the outbound send lock")
    subcommands.add_parser("unlock-sends", help="Disable the outbound send lock")

    seed_playbooks = subcommands.add_parser("seed-playbooks", help="Create starter playbooks for selected properties")
    seed_playbooks.add_argument("--property-id", action="append", default=[], help="Property ID to seed; repeatable")

    prune_playbooks = subcommands.add_parser("prune-playbooks", help="Delete playbooks except selected properties")
    prune_playbooks.add_argument("--keep-property-id", action="append", default=[], help="Property ID to keep; repeatable")
    return parser


def parse_key_value_pairs(pairs: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"Expected key=value, got {pair!r}")
        values[key.strip()] = value
    return values


def print_config(values: dict[str, str]) -> None:
    for key in sorted(values):
        value = values[key].replace("\\", "\\\\").replace("\n", "\\n")
        print(f"{key}={value}")


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command in {None, "init-db"}:
        init_database()
        return
    if args.command == "backup":
        result = create_pilot_backup(args.output_dir, name=args.name)
        print(f"Created verified Prosper pilot backup: {result.archive_path}")
        return

    init_db()
    with SessionLocal() as session:
        if args.command == "show-config":
            print_config(get_all_config(session))
            return
        if args.command == "set-config":
            values = update_config(session, parse_key_value_pairs(args.pairs))
            session.commit()
            print_config(values)
            return
        if args.command in {"lock-sends", "unlock-sends"}:
            values = update_config(session, {"send_lock": "true" if args.command == "lock-sends" else "false"})
            session.commit()
            print_config(values)
            return
        if args.command == "seed-playbooks":
            property_ids = {value.strip() for value in args.property_id if value.strip()} or None
            seed_property_playbooks(session, property_ids)
            session.commit()
            print(f"Seeded starter playbooks for {', '.join(sorted(property_ids or DEFAULT_TEST_PLAYBOOK_PROPERTY_IDS))}.")
            return
        if args.command == "prune-playbooks":
            keep_property_ids = {value.strip() for value in args.keep_property_id if value.strip()} or set(DEFAULT_TEST_PLAYBOOK_PROPERTY_IDS)
            playbooks = session.scalars(select(PropertyPlaybook)).all()
            deleted = 0
            for playbook in playbooks:
                if playbook.property_id in keep_property_ids:
                    continue
                session.delete(playbook)
                deleted += 1
            session.commit()
            print(f"Kept playbooks for {', '.join(sorted(keep_property_ids))}; deleted {deleted}.")
            return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
