from __future__ import annotations

import argparse
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal, init_db
from .models import PropertyPlaybook, WhatsappAccount, Workspace, WorkspaceMember
from .seed import seed_all, seed_app_config, seed_properties, seed_property_playbooks
from .services import get_all_config, update_config
from .tenant import WorkspaceScope, workspace_scope


def upsert_workspace_bundle(
    session: Session,
    *,
    workspace_id: str,
    slug: str,
    name: str,
    auth_user_id: str,
    email: str | None = None,
    role: str = "owner",
    account_id: str,
    account_key: str,
    account_display_name: str | None = None,
    phone_jid: str | None = None,
    bridge_base_url: str | None = None,
    seed_property_rows: bool = False,
) -> tuple[Workspace, WhatsappAccount, WorkspaceMember]:
    workspace_id = workspace_id.strip()
    slug = slug.strip()
    name = name.strip()
    auth_user_id = auth_user_id.strip()
    account_id = account_id.strip()
    account_key = account_key.strip()
    if not workspace_id:
        raise ValueError("workspace_id must not be blank")
    if not slug:
        raise ValueError("slug must not be blank")
    if not name:
        raise ValueError("name must not be blank")
    if not auth_user_id:
        raise ValueError("auth_user_id must not be blank")
    if not account_id:
        raise ValueError("account_id must not be blank")
    if not account_key:
        raise ValueError("account_key must not be blank")

    slug_owner = session.scalar(select(Workspace).where(Workspace.slug == slug, Workspace.id != workspace_id))
    if slug_owner:
        raise ValueError(f"workspace slug is already used by {slug_owner.id}")

    account_owner = session.get(WhatsappAccount, account_id)
    if account_owner and account_owner.workspace_id != workspace_id:
        raise ValueError(f"WhatsApp account id is already used by workspace {account_owner.workspace_id}")

    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        workspace = Workspace(id=workspace_id, slug=slug, name=name, status="active")
        session.add(workspace)
    else:
        workspace.slug = slug
        workspace.name = name
        workspace.status = "active"

    account = session.get(WhatsappAccount, account_id)
    if not account:
        account = WhatsappAccount(
            id=account_id,
            workspace_id=workspace_id,
            account_key=account_key,
            display_name=account_display_name or name,
            phone_jid=phone_jid.strip() if phone_jid else None,
            bridge_base_url=bridge_base_url.strip() if bridge_base_url else None,
            status="active",
        )
        session.add(account)
    else:
        account.workspace_id = workspace_id
        account.account_key = account_key
        account.display_name = account_display_name or account.display_name or name
        account.phone_jid = phone_jid.strip() if phone_jid else account.phone_jid
        account.bridge_base_url = bridge_base_url.strip() if bridge_base_url else account.bridge_base_url
        account.status = "active"

    other_accounts = session.scalars(
        select(WhatsappAccount).where(
            WhatsappAccount.workspace_id == workspace_id,
            WhatsappAccount.id != account_id,
            WhatsappAccount.status == "active",
        )
    ).all()
    for other_account in other_accounts:
        other_account.status = "inactive"

    member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.auth_user_id == auth_user_id,
        )
    )
    if not member:
        member = WorkspaceMember(
            workspace_id=workspace_id,
            auth_user_id=auth_user_id,
            email=email.strip() if email else None,
            role=role,
            status="active",
        )
        session.add(member)
    else:
        member.email = email.strip() if email else member.email
        member.role = role
        member.status = "active"

    session.flush()
    with workspace_scope(WorkspaceScope(workspace_id, account_id)):
        seed_app_config(session)
        if seed_property_rows:
            seed_properties(session)
    session.flush()
    return workspace, account, member


def init_database() -> None:
    init_db()
    with SessionLocal() as session:
        seed_all(session)
    print("Initialized WhatsApp PA database and seeded default workspace/config.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WhatsApp PA operator utilities")
    subcommands = parser.add_subparsers(dest="command")

    subcommands.add_parser("init-db", help="Initialize the database and seed the default workspace")

    workspace = subcommands.add_parser("upsert-workspace", help="Create or update a workspace, owner, and WhatsApp account")
    workspace.add_argument("--workspace-id", required=True)
    workspace.add_argument("--slug", required=True)
    workspace.add_argument("--name", required=True)
    workspace.add_argument("--auth-user-id", required=True, help="Owner identifier for the workspace record")
    workspace.add_argument("--email")
    workspace.add_argument("--role", default="owner")
    workspace.add_argument("--account-id", required=True)
    workspace.add_argument("--account-key", required=True)
    workspace.add_argument("--account-display-name")
    workspace.add_argument("--phone-jid")
    workspace.add_argument("--account-bridge-base-url")
    workspace.add_argument("--seed-properties", action="store_true")

    list_workspaces = subcommands.add_parser("list-workspaces", help="List configured workspaces and WhatsApp accounts")
    list_workspaces.add_argument("--include-inactive", action="store_true")

    show_config = subcommands.add_parser("show-config", help="Show app config for a workspace")
    show_config.add_argument("--workspace-id", default="default")
    show_config.add_argument("--account-id", default="default-whatsapp")

    set_config = subcommands.add_parser("set-config", help="Set one or more app config values for a workspace")
    set_config.add_argument("--workspace-id", default="default")
    set_config.add_argument("--account-id", default="default-whatsapp")
    set_config.add_argument("pairs", nargs="+", help="Config updates as key=value")

    lock_sends = subcommands.add_parser("lock-sends", help="Enable send lock for a workspace")
    lock_sends.add_argument("--workspace-id", default="default")
    lock_sends.add_argument("--account-id", default="default-whatsapp")

    unlock_sends = subcommands.add_parser("unlock-sends", help="Disable send lock for a workspace")
    unlock_sends.add_argument("--workspace-id", default="default")
    unlock_sends.add_argument("--account-id", default="default-whatsapp")

    seed_playbooks = subcommands.add_parser("seed-playbooks", help="Create explicit starter Playbooks for selected properties only")
    seed_playbooks.add_argument("--workspace-id", default="default")
    seed_playbooks.add_argument("--account-id", default="default-whatsapp")
    seed_playbooks.add_argument(
        "--property-id",
        action="append",
        default=[],
        help="Property ID to seed. Can be repeated. If omitted, seeds the built-in test/demo property set.",
    )

    prune_playbooks = subcommands.add_parser("prune-playbooks", help="Delete Playbooks except selected test/live properties")
    prune_playbooks.add_argument("--workspace-id", default="default")
    prune_playbooks.add_argument("--account-id", default="default-whatsapp")
    prune_playbooks.add_argument(
        "--keep-property-id",
        action="append",
        default=[],
        help="Property ID to keep. Can be repeated. If omitted, keeps the built-in test/demo property set.",
    )
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
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {None, "init-db"}:
        init_database()
        return

    init_db()
    if args.command == "list-workspaces":
        with SessionLocal() as session:
            workspace_query = select(Workspace).order_by(Workspace.id)
            account_query = select(WhatsappAccount).order_by(WhatsappAccount.workspace_id, WhatsappAccount.id)
            if not args.include_inactive:
                workspace_query = workspace_query.where(Workspace.status == "active")
                account_query = account_query.where(WhatsappAccount.status == "active")
            workspaces = list(session.scalars(workspace_query).all())
            accounts = list(session.scalars(account_query).all())
        for workspace in workspaces:
            print(f"workspace id={workspace.id} slug={workspace.slug} name={workspace.name!r} status={workspace.status}")
            for account in [account for account in accounts if account.workspace_id == workspace.id]:
                print(
                    "  account "
                    f"id={account.id} key={account.account_key} display={account.display_name!r} "
                    f"bridge={account.bridge_base_url or '-'} status={account.status}"
                )
        return

    if args.command == "show-config":
        with SessionLocal() as session, workspace_scope(WorkspaceScope(args.workspace_id, args.account_id)):
            print_config(get_all_config(session))
        return

    if args.command == "set-config":
        updates = parse_key_value_pairs(args.pairs)
        with SessionLocal() as session, workspace_scope(WorkspaceScope(args.workspace_id, args.account_id)):
            values = update_config(session, updates)
            session.commit()
            print_config(values)
        return

    if args.command in {"lock-sends", "unlock-sends"}:
        desired = "true" if args.command == "lock-sends" else "false"
        with SessionLocal() as session, workspace_scope(WorkspaceScope(args.workspace_id, args.account_id)):
            values = update_config(session, {"send_lock": desired})
            session.commit()
            print_config(values)
        return

    if args.command == "seed-playbooks":
        property_ids = {value.strip() for value in args.property_id if value.strip()} or None
        with SessionLocal() as session, workspace_scope(WorkspaceScope(args.workspace_id, args.account_id)):
            seed_property_playbooks(session, property_ids)
            session.commit()
            seeded = sorted(property_ids) if property_ids else ["<built-in-test-set>"]
        print(f"Seeded starter Playbooks for {', '.join(seeded)}.")
        return

    if args.command == "prune-playbooks":
        from .seed import DEFAULT_TEST_PLAYBOOK_PROPERTY_IDS

        keep_property_ids = {value.strip() for value in args.keep_property_id if value.strip()} or set(DEFAULT_TEST_PLAYBOOK_PROPERTY_IDS)
        with SessionLocal() as session, workspace_scope(WorkspaceScope(args.workspace_id, args.account_id)):
            playbooks = session.scalars(select(PropertyPlaybook)).all()
            deleted: list[str] = []
            for playbook in playbooks:
                if playbook.workspace_id != args.workspace_id:
                    continue
                if playbook.property_id in keep_property_ids:
                    continue
                deleted.append(playbook.property_id)
                session.delete(playbook)
            session.commit()
        print(f"Kept Playbooks for {', '.join(sorted(keep_property_ids))}; deleted {len(deleted)}.")
        return

    with SessionLocal() as session:
        workspace, account, member = upsert_workspace_bundle(
            session,
            workspace_id=args.workspace_id,
            slug=args.slug,
            name=args.name,
            auth_user_id=args.auth_user_id,
            email=args.email,
            role=args.role,
            account_id=args.account_id,
            account_key=args.account_key,
            account_display_name=args.account_display_name,
            phone_jid=args.phone_jid,
            bridge_base_url=args.account_bridge_base_url,
            seed_property_rows=args.seed_properties,
        )
        session.commit()
        summary = (workspace.id, workspace.slug, account.id, account.account_key, member.auth_user_id)
    print(
        "Upserted workspace "
        f"{summary[0]} ({summary[1]}), account {summary[2]} ({summary[3]}), "
        f"member {summary[4]}."
    )


if __name__ == "__main__":
    main()
