from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import tarfile

from sqlalchemy import select

from .backup import cleanup_backup_archives, cleanup_operational_data, create_backup, restore_backup
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

    backup = subcommands.add_parser("backup", help="Create a verified backup archive")
    backup.add_argument("--output-dir", type=Path, help="Directory for the completed backup archive")
    backup.add_argument("--name", help="Archive file name; .tar.gz is added when omitted")

    restore = subcommands.add_parser("restore", help="Restore a verified Prosper backup")
    restore.add_argument("archive", type=Path, help="Backup archive to restore")
    restore.add_argument("--confirm-restore", action="store_true", help="Confirm restore without an interactive prompt")

    cleanup_data = subcommands.add_parser("cleanup-data", help="Remove selected operational data")
    cleanup_data.add_argument("--database", action="store_true", help="Remove the SQLite database")
    cleanup_data.add_argument("--media", action="store_true", help="Remove managed property media")
    cleanup_data.add_argument("--confirm-cleanup", action="store_true", help="Confirm cleanup without an interactive prompt")

    cleanup_backups = subcommands.add_parser("cleanup-backups", help="Remove selected backup archives")
    cleanup_backups.add_argument("archives", nargs="+", help="Backup archive names or paths to remove")
    cleanup_backups.add_argument("--backup-dir", type=Path, help="Directory containing backup archives")
    cleanup_backups.add_argument("--confirm-cleanup", action="store_true", help="Confirm cleanup without an interactive prompt")

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
        result = create_backup(args.output_dir, name=args.name)
        print(f"Created verified Prosper backup: {result.archive_path}")
        return
    if args.command == "restore":
        confirmed = args.confirm_restore or require_confirmation(
            "Restore will replace the current SQLite database and managed property media.",
            "RESTORE",
        )
        try:
            result = restore_backup(args.archive, confirmed=confirmed)
        except (FileNotFoundError, PermissionError, RuntimeError, tarfile.TarError, ValueError) as error:
            raise SystemExit(str(error)) from error
        print(f"Restored Prosper backup: {result.archive_path}")
        print(f"Rollback snapshot: {result.rollback_path}")
        print("WhatsApp pairing credentials are not included; re-pairing may be required.")
        return
    if args.command == "cleanup-data":
        confirmed = args.confirm_cleanup or require_confirmation(
            "Cleanup will remove only the selected operational data.",
            "CLEANUP",
        )
        try:
            result = cleanup_operational_data(remove_database=args.database, remove_media=args.media, confirmed=confirmed)
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as error:
            raise SystemExit(str(error)) from error
        for path in result.removed_paths:
            if path.name == "properties":
                print(f"Removed managed property media: {path}")
            else:
                print(f"Removed database file: {path}")
        if not result.removed_paths:
            print("No selected operational data was present.")
        return
    if args.command == "cleanup-backups":
        confirmed = args.confirm_cleanup or require_confirmation(
            "Cleanup will remove only the selected backup archives.",
            "CLEANUP",
        )
        try:
            result = cleanup_backup_archives(args.archives, backup_dir=args.backup_dir, confirmed=confirmed)
        except (FileNotFoundError, PermissionError, ValueError) as error:
            raise SystemExit(str(error)) from error
        for path in result.removed_paths:
            print(f"Removed backup archive: {path}")
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


def require_confirmation(message: str, phrase: str) -> bool:
    try:
        response = input(f"{message}\nType {phrase} to continue: ")
    except EOFError as error:
        raise SystemExit(f"Confirmation required. Re-run with --confirm-{phrase.lower()} for non-interactive use.") from error
    if response != phrase:
        raise SystemExit("Confirmation did not match; no changes made.")
    return True


if __name__ == "__main__":
    main()
