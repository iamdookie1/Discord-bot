"""
Server structure backup/restore for the web UI only — there's no chat
command for this, it's driven entirely from the Backup tab.

Captures roles, categories, and channels (name, type, position-ish order,
per-role permission overwrites) — never messages, pins, or anything else
"inside" a channel. Backups are stored in server_backups.json (gitignored,
device-local, like everything else user-generated here).
"""
import datetime
import json
import os
import uuid

import discord

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUPS_PATH = os.path.join(BASE_DIR, "server_backups.json")

# discord.py channel classes we know how to snapshot and recreate.
_CHANNEL_KIND_FOR_CLASS = [
    (discord.TextChannel, "text"),
    (discord.VoiceChannel, "voice"),
    (discord.StageChannel, "stage"),
    (discord.ForumChannel, "forum"),
]


def load_backups() -> dict:
    if not os.path.exists(BACKUPS_PATH):
        return {}
    try:
        with open(BACKUPS_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_backups(data: dict):
    with open(BACKUPS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def list_backups() -> list:
    data = load_backups()
    out = [
        {
            "id": backup_id,
            "name": b.get("name", backup_id),
            "guild_name": b.get("guild_name", "?"),
            "created_at": b.get("created_at", ""),
            "role_count": len(b.get("roles", [])),
            "category_count": len(b.get("categories", [])),
            "channel_count": len(b.get("channels", [])),
        }
        for backup_id, b in data.items()
    ]
    out.sort(key=lambda b: b["created_at"], reverse=True)
    return out


def delete_backup(backup_id: str) -> bool:
    data = load_backups()
    if backup_id in data:
        del data[backup_id]
        save_backups(data)
        return True
    return False


def _channel_kind(channel) -> str | None:
    for cls, kind in _CHANNEL_KIND_FOR_CLASS:
        if isinstance(channel, cls):
            return kind
    return None


def _overwrites_data(channel) -> list:
    out = []
    for target, overwrite in channel.overwrites.items():
        if not isinstance(target, discord.Role):
            continue  # member-specific overwrites don't carry over across servers
        allow, deny = overwrite.pair()
        role_name = "@everyone" if target.is_default() else target.name
        out.append({"role_name": role_name, "allow": allow.value, "deny": deny.value})
    return out


async def save_backup(guild: discord.Guild) -> dict:
    roles_data = []
    for role in guild.roles:
        if role.managed:
            continue  # bot/integration/booster roles — not ours to recreate
        roles_data.append({
            "name": "@everyone" if role.is_default() else role.name,
            "is_default": role.is_default(),
            "color": role.color.value,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "permissions": role.permissions.value,
        })

    categories_data = [
        {"name": cat.name, "overwrites": _overwrites_data(cat)}
        for cat in guild.categories
    ]

    channels_data = []
    for ch in guild.channels:
        if isinstance(ch, discord.CategoryChannel):
            continue
        kind = _channel_kind(ch)
        if kind is None:
            continue
        entry = {
            "name": ch.name,
            "type": kind,
            "category_name": ch.category.name if ch.category else None,
            "overwrites": _overwrites_data(ch),
        }
        if kind == "text":
            entry["topic"] = ch.topic
            entry["nsfw"] = ch.nsfw
            entry["slowmode_delay"] = ch.slowmode_delay
        elif kind == "voice":
            entry["bitrate"] = ch.bitrate
            entry["user_limit"] = ch.user_limit
        elif kind == "forum":
            entry["topic"] = ch.topic
        channels_data.append(entry)

    backup_id = uuid.uuid4().hex[:12]
    data = load_backups()
    data[backup_id] = {
        "name": f"{guild.name} — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "guild_id": str(guild.id),
        "guild_name": guild.name,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "roles": roles_data,
        "categories": categories_data,
        "channels": channels_data,
    }
    save_backups(data)
    return {
        "ok": True,
        "backup_id": backup_id,
        "roles": len(roles_data),
        "categories": len(categories_data),
        "channels": len(channels_data),
    }


def _build_overwrites(entries: list, role_map: dict) -> dict:
    overwrites = {}
    for ow in entries:
        role = role_map.get(ow["role_name"])
        if not role:
            continue
        overwrites[role] = discord.PermissionOverwrite.from_pair(
            discord.Permissions(ow["allow"]), discord.Permissions(ow["deny"])
        )
    return overwrites


async def load_backup(guild: discord.Guild, backup_id: str, mode: str) -> dict:
    data = load_backups()
    backup = data.get(backup_id)
    if not backup:
        return {"ok": False, "error": "Backup not found."}

    errors = []
    deleted_roles = 0
    deleted_channels = 0

    if mode == "replace":
        for ch in list(guild.channels):
            try:
                await ch.delete(reason="Backup restore: full replace")
                deleted_channels += 1
            except discord.HTTPException as exc:
                errors.append(f"Couldn't delete channel {ch.name}: {exc.text}")
        for role in list(guild.roles):
            if role.is_default() or role.managed:
                continue
            try:
                await role.delete(reason="Backup restore: full replace")
                deleted_roles += 1
            except discord.HTTPException as exc:
                errors.append(f"Couldn't delete role {role.name}: {exc.text}")

    # ---- roles ----
    role_map = {}
    for r in backup["roles"]:
        if r.get("is_default"):
            role_map["@everyone"] = guild.default_role
            if mode == "replace":
                try:
                    await guild.default_role.edit(permissions=discord.Permissions(r["permissions"]))
                except discord.HTTPException as exc:
                    errors.append(f"Couldn't update @everyone permissions: {exc.text}")
            continue
        try:
            role_map[r["name"]] = await guild.create_role(
                name=r["name"],
                color=discord.Color(r["color"]),
                hoist=r["hoist"],
                mentionable=r["mentionable"],
                permissions=discord.Permissions(r["permissions"]),
                reason="Backup restore",
            )
        except discord.HTTPException as exc:
            errors.append(f"Couldn't create role {r['name']}: {exc.text}")

    # ---- categories ----
    category_map = {}
    for c in backup["categories"]:
        try:
            category_map[c["name"]] = await guild.create_category(
                name=c["name"],
                overwrites=_build_overwrites(c["overwrites"], role_map),
                reason="Backup restore",
            )
        except discord.HTTPException as exc:
            errors.append(f"Couldn't create category {c['name']}: {exc.text}")

    # ---- channels ----
    created_channels = 0
    for ch in backup["channels"]:
        category = category_map.get(ch["category_name"]) if ch["category_name"] else None
        overwrites = _build_overwrites(ch["overwrites"], role_map)
        try:
            if ch["type"] == "text":
                await guild.create_text_channel(
                    name=ch["name"], category=category, overwrites=overwrites,
                    topic=ch.get("topic"), nsfw=ch.get("nsfw", False),
                    slowmode_delay=ch.get("slowmode_delay", 0) or 0,
                    reason="Backup restore",
                )
            elif ch["type"] == "voice":
                await guild.create_voice_channel(
                    name=ch["name"], category=category, overwrites=overwrites,
                    bitrate=ch.get("bitrate") or None, user_limit=ch.get("user_limit", 0) or 0,
                    reason="Backup restore",
                )
            elif ch["type"] == "stage":
                await guild.create_stage_channel(
                    name=ch["name"], category=category, overwrites=overwrites,
                    reason="Backup restore",
                )
            elif ch["type"] == "forum":
                await guild.create_forum(
                    name=ch["name"], category=category, overwrites=overwrites,
                    topic=ch.get("topic"), reason="Backup restore",
                )
            else:
                continue
            created_channels += 1
        except discord.HTTPException as exc:
            errors.append(f"Couldn't create channel {ch['name']}: {exc.text}")
        except AttributeError:
            errors.append(f"Channel type '{ch['type']}' isn't supported by this discord.py version — skipped {ch['name']}.")

    return {
        "ok": True,
        "created_roles": len(role_map) - (1 if "@everyone" in role_map else 0),
        "created_categories": len(category_map),
        "created_channels": created_channels,
        "deleted_roles": deleted_roles,
        "deleted_channels": deleted_channels,
        "errors": errors,
    }
