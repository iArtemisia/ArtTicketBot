
from __future__ import annotations

import asyncio
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "data/guild_configs.json"))
DEFAULT_PANEL_GIF_URL = os.getenv("PANEL_GIF_URL", "").strip()
TICKET_LOG_DIR = CONFIG_PATH.parent / "ticket_logs"
TICKET_PING_COOLDOWN_SECONDS = 10 * 60

TICKET_CONFIG: dict[str, str] = {
    "label": "Open Ticket",
    "emoji": "🎫",
    "description": "Press the button below to open a private ticket.",
    "channel_prefix": "ticket",
}


class GuildConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def _read_all(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_all(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def get_guild(self, guild_id: int) -> dict[str, Any]:
        data = self._read_all()
        return data.get(str(guild_id), {})

    def set_guild(self, guild_id: int, config: dict[str, Any]) -> None:
        data = self._read_all()
        data[str(guild_id)] = config
        self._write_all(data)

    def update_guild(self, guild_id: int, **updates: Any) -> dict[str, Any]:
        config = self.get_guild(guild_id)
        config.update(updates)
        self.set_guild(guild_id, config)
        return config

    def add_staff_role(self, guild_id: int, role_id: int) -> dict[str, Any]:
        config = self.get_guild(guild_id)
        staff_role_ids = config.get("staff_role_ids", [])
        if role_id not in staff_role_ids:
            staff_role_ids.append(role_id)
        config["staff_role_ids"] = staff_role_ids
        self.set_guild(guild_id, config)
        return config

    def remove_staff_role(self, guild_id: int, role_id: int) -> dict[str, Any]:
        config = self.get_guild(guild_id)
        config["staff_role_ids"] = [rid for rid in config.get("staff_role_ids", []) if rid != role_id]
        self.set_guild(guild_id, config)
        return config

    def add_ping_role(self, guild_id: int, role_id: int) -> dict[str, Any]:
        config = self.get_guild(guild_id)
        ping_role_ids = config.get("ping_role_ids", [])
        if role_id not in ping_role_ids:
            ping_role_ids.append(role_id)
        config["ping_role_ids"] = ping_role_ids
        self.set_guild(guild_id, config)
        return config

    def remove_ping_role(self, guild_id: int, role_id: int) -> dict[str, Any]:
        config = self.get_guild(guild_id)
        config["ping_role_ids"] = [rid for rid in config.get("ping_role_ids", []) if rid != role_id]
        self.set_guild(guild_id, config)
        return config

    def is_ready(self, guild_id: int) -> bool:
        config = self.get_guild(guild_id)
        return bool(
            config.get("ticket_category_id")
            and config.get("log_channel_id")
            and config.get("staff_role_ids")
        )


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-") or "ticket"


def truncate(value: str, limit: int = 1024) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def extract_id(raw_value: str) -> Optional[int]:
    match = re.search(r"(\d{15,25})", raw_value)
    return int(match.group(1)) if match else None


def extract_ids(raw_value: str) -> list[int]:
    seen: set[int] = set()
    ids: list[int] = []
    for match in re.findall(r"(\d{15,25})", raw_value):
        value = int(match)
        if value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def build_ticket_topic(
    *,
    owner_id: int,
    ticket_type: str,
    status: str = "open",
    claimed_by: int = 0,
    ping_role_ids: Optional[list[int]] = None,
) -> str:
    ping_part = ",".join(str(role_id) for role_id in (ping_role_ids or []))
    return (
        f"ticket_owner:{owner_id}|"
        f"ticket_type:{ticket_type}|"
        f"status:{status}|"
        f"claimed_by:{claimed_by}|"
        f"ping_roles:{ping_part}"
    )


def parse_ticket_topic(topic: Optional[str]) -> dict[str, str]:
    data: dict[str, str] = {}
    if not topic:
        return data

    for part in topic.split("|"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        data[key] = value
    return data


def get_ticket_ping_role_ids(channel: discord.TextChannel) -> list[int]:
    data = parse_ticket_topic(channel.topic)
    raw = data.get("ping_roles", "")
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            result.append(int(part))
    return result


def is_ticket_channel(channel: discord.abc.GuildChannel) -> bool:
    return isinstance(channel, discord.TextChannel) and "ticket_owner:" in (channel.topic or "")


def get_ticket_owner_id(channel: discord.TextChannel) -> Optional[int]:
    data = parse_ticket_topic(channel.topic)
    value = data.get("ticket_owner")
    return int(value) if value and value.isdigit() else None


def normalize_panel_image_url(url: str) -> str:
    value = url.strip()
    if not value:
        return ""

    if not value.startswith(("http://", "https://")):
        return value

    parsed = urlparse(value)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")

    if host in {"imgur.com", "www.imgur.com", "m.imgur.com", "i.imgur.com"} and path:
        image_id = path.split("/")[-1]
        if "." in image_id:
            stem, ext = image_id.rsplit(".", 1)
            image_id = stem
            ext = ext.lower()
        else:
            ext = "gif"

        if ext == "gifv":
            ext = "gif"

        if ext not in {"gif", "png", "jpg", "jpeg", "webp"}:
            ext = "gif"

        return f"https://i.imgur.com/{image_id}.{ext}"

    return value


def get_panel_gif_url(bot: "TicketBot", guild_id: int) -> str:
    config = bot.config_store.get_guild(guild_id)
    saved = normalize_panel_image_url(str(config.get("panel_gif_url", "")))
    if saved:
        return saved
    return normalize_panel_image_url(DEFAULT_PANEL_GIF_URL)


def get_guild_ping_roles(bot: "TicketBot", guild: discord.Guild) -> list[discord.Role]:
    config = bot.config_store.get_guild(guild.id)
    ping_role_ids = config.get("ping_role_ids", [])
    roles: list[discord.Role] = []
    for role_id in ping_role_ids:
        role = guild.get_role(role_id)
        if role is not None:
            roles.append(role)
    return roles


def get_ticket_ping_roles(bot: "TicketBot", guild: discord.Guild, channel: discord.TextChannel) -> list[discord.Role]:
    roles: list[discord.Role] = []
    for role_id in get_ticket_ping_role_ids(channel):
        role = guild.get_role(role_id)
        if role is not None:
            roles.append(role)
    return roles


def mention_roles(roles: list[discord.Role]) -> str:
    return " ".join(f"<@&{role.id}>" for role in roles)


def get_ticket_extra_roles(bot: "TicketBot", guild: discord.Guild, channel: discord.TextChannel) -> list[discord.Role]:
    staff_ids = {role.id for role in get_staff_roles(bot, guild)}
    roles: list[discord.Role] = []
    for target, overwrite in channel.overwrites.items():
        if not isinstance(target, discord.Role):
            continue
        if target.is_default():
            continue
        if target.id in staff_ids:
            continue
        if overwrite.view_channel is True:
            roles.append(target)
    return roles


def get_ticket_extra_users(bot: "TicketBot", guild: discord.Guild, channel: discord.TextChannel) -> list[discord.Member]:
    owner_id = get_ticket_owner_id(channel)
    bot_member_id = guild.me.id if guild.me is not None else 0
    users: list[discord.Member] = []
    for target, overwrite in channel.overwrites.items():
        if not isinstance(target, discord.Member):
            continue
        if target.id in {owner_id, bot_member_id}:
            continue
        if overwrite.view_channel is True:
            users.append(target)
    return users


def get_config_role_ids(bot: "TicketBot", guild_id: int, target: str) -> list[int]:
    config = bot.config_store.get_guild(guild_id)
    key = "staff_role_ids" if target == "staff" else "ping_role_ids"
    return [int(role_id) for role_id in config.get(key, []) if str(role_id).isdigit()]


def get_config_roles(bot: "TicketBot", guild: discord.Guild, target: str) -> list[discord.Role]:
    roles: list[discord.Role] = []
    for role_id in get_config_role_ids(bot, guild.id, target):
        role = guild.get_role(role_id)
        if role is not None and not role.is_default():
            roles.append(role)
    return roles


def get_selectable_roles(bot: "TicketBot", guild: discord.Guild, target: str, action: str) -> list[discord.Role]:
    configured = set(get_config_role_ids(bot, guild.id, target))
    roles = [role for role in guild.roles if not role.is_default() and not role.managed]
    roles.sort(key=lambda role: role.position, reverse=True)

    if action == "add":
        roles = [role for role in roles if role.id not in configured]
    else:
        roles = [role for role in roles if role.id in configured]

    return roles[:25]


def format_role_list(roles: list[discord.Role]) -> str:
    return "\n".join(role.mention for role in roles) if roles else "None"


def build_role_config_embed(bot: "TicketBot", guild: discord.Guild, target: str, action: str) -> discord.Embed:
    target_label = "staff" if target == "staff" else "ping"
    action_label = "Add" if action == "add" else "Remove"
    configured_roles = get_config_roles(bot, guild, target)
    selectable_roles = get_selectable_roles(bot, guild, target, action)

    embed = discord.Embed(
        title=f"{action_label} Ticket {target_label.title()} Role",
        description=(
            "Pick a role from the dropdown below.\n\n"
            "Already configured roles are shown here so you do not need to paste role IDs."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name=f"Current {target_label} roles", value=format_role_list(configured_roles), inline=False)
    helper = "Dropdown shows roles that are not already configured." if action == "add" else "Dropdown shows roles that are currently configured."
    embed.add_field(name="Dropdown status", value=f"{helper}\nAvailable choices shown: {len(selectable_roles)} / 25 max.", inline=False)
    embed.set_footer(text="Discord dropdowns can show up to 25 roles at a time.")
    return embed


def clean_tag_name(name: str) -> str:
    value = name.lower().strip()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    return value.strip("-")[:40]


def get_tags(bot: "TicketBot", guild_id: int) -> dict[str, str]:
    config = bot.config_store.get_guild(guild_id)
    raw = config.get("tags", {})
    return raw if isinstance(raw, dict) else {}


def set_tag(bot: "TicketBot", guild_id: int, name: str, response: str) -> dict[str, str]:
    tags = dict(get_tags(bot, guild_id))
    tags[name] = response
    bot.config_store.update_guild(guild_id, tags=tags)
    return tags


def remove_tag(bot: "TicketBot", guild_id: int, name: str) -> bool:
    tags = dict(get_tags(bot, guild_id))
    existed = name in tags
    tags.pop(name, None)
    bot.config_store.update_guild(guild_id, tags=tags)
    return existed


def get_notes_thread_id(bot: "TicketBot", guild_id: int, ticket_channel_id: int) -> Optional[int]:
    config = bot.config_store.get_guild(guild_id)
    mapping = config.get("notes_thread_ids", {})
    if not isinstance(mapping, dict):
        return None
    value = mapping.get(str(ticket_channel_id))
    return int(value) if str(value).isdigit() else None


def set_notes_thread_id(bot: "TicketBot", guild_id: int, ticket_channel_id: int, thread_id: int) -> None:
    config = bot.config_store.get_guild(guild_id)
    mapping = config.get("notes_thread_ids", {})
    if not isinstance(mapping, dict):
        mapping = {}
    mapping[str(ticket_channel_id)] = int(thread_id)
    bot.config_store.update_guild(guild_id, notes_thread_ids=mapping)


def get_ticket_log_id(channel: discord.TextChannel) -> str:
    return str(channel.id)


def get_ticket_log_path(guild_id: int, ticket_id: str) -> Path:
    safe_ticket_id = re.sub(r"[^0-9]", "", str(ticket_id)) or "unknown"
    folder = TICKET_LOG_DIR / str(guild_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{safe_ticket_id}.txt"


def build_admin_panel_embed(bot: "TicketBot", guild: discord.Guild, channel: Optional[discord.abc.GuildChannel]) -> discord.Embed:
    config = bot.config_store.get_guild(guild.id)
    category = guild.get_channel(config.get("ticket_category_id", 0))
    log_channel = guild.get_channel(config.get("log_channel_id", 0))
    staff_roles = get_staff_roles(bot, guild)
    ping_roles = get_guild_ping_roles(bot, guild)

    embed = discord.Embed(
        title="Ticket Admin Panel",
        description=(
            "Use the buttons below to edit default staff/ping roles with dropdowns. "
            "When opened inside a ticket, the panel can also edit that ticket's role access and ping settings."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Ticket category", value=category.mention if category else "Not set", inline=False)
    embed.add_field(name="Closed-ticket log channel", value=log_channel.mention if log_channel else "Not set", inline=False)
    embed.add_field(
        name="Default staff roles",
        value="\n".join(role.mention for role in staff_roles) if staff_roles else "None",
        inline=False,
    )
    embed.add_field(
        name="Default ping roles",
        value="\n".join(role.mention for role in ping_roles) if ping_roles else "None",
        inline=False,
    )

    if isinstance(channel, discord.TextChannel) and is_ticket_channel(channel):
        owner_id = get_ticket_owner_id(channel)
        owner = guild.get_member(owner_id) if owner_id else None
        ticket_ping_roles = get_ticket_ping_roles(bot, guild, channel)
        extra_roles = get_ticket_extra_roles(bot, guild, channel)
        extra_users = get_ticket_extra_users(bot, guild, channel)

        embed.add_field(name="Current ticket", value=channel.mention, inline=False)
        embed.add_field(name="Ticket owner", value=owner.mention if owner else f"`{owner_id}`", inline=False)
        embed.add_field(
            name="Ticket ping roles",
            value="\n".join(role.mention for role in ticket_ping_roles) if ticket_ping_roles else "None",
            inline=False,
        )
        embed.add_field(
            name="Extra allowed roles",
            value="\n".join(role.mention for role in extra_roles) if extra_roles else "None",
            inline=False,
        )
        embed.add_field(
            name="Extra allowed users",
            value="\n".join(user.mention for user in extra_users) if extra_users else "None",
            inline=False,
        )
        embed.add_field(
            name="Ticket tools here",
            value="Use the buttons below to add/remove roles and set ticket ping roles for this specific ticket. User add/remove stays on the ticket channel buttons.",
            inline=False,
        )
    else:
        embed.add_field(
            name="Ticket-specific tools",
            value="Run `/ticket admin` inside a ticket channel to edit that ticket's role permissions and ping roles from this panel.",
            inline=False,
        )

    embed.set_footer(text="This panel is admin-only and updates settings live.")
    return embed


async def fetch_member_safe(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(user_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.HTTPException):
        return None


async def get_ticket_category(bot: "TicketBot", guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    config = bot.config_store.get_guild(guild.id)
    channel = guild.get_channel(config.get("ticket_category_id", 0))
    return channel if isinstance(channel, discord.CategoryChannel) else None


async def get_log_channel(bot: "TicketBot", guild: discord.Guild) -> Optional[discord.TextChannel]:
    config = bot.config_store.get_guild(guild.id)
    channel = guild.get_channel(config.get("log_channel_id", 0))
    return channel if isinstance(channel, discord.TextChannel) else None


def get_staff_roles(bot: "TicketBot", guild: discord.Guild) -> list[discord.Role]:
    config = bot.config_store.get_guild(guild.id)
    roles: list[discord.Role] = []
    for role_id in config.get("staff_role_ids", []):
        role = guild.get_role(role_id)
        if role is not None:
            roles.append(role)
    return roles


def member_is_staff(bot: "TicketBot", member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
        return True

    configured_roles = set(bot.config_store.get_guild(member.guild.id).get("staff_role_ids", []))
    member_role_ids = {role.id for role in member.roles}
    return bool(configured_roles & member_role_ids)


def find_open_ticket_for_user(category: discord.CategoryChannel, user_id: int) -> Optional[discord.TextChannel]:
    for channel in category.text_channels:
        data = parse_ticket_topic(channel.topic)
        if data.get("ticket_owner") == str(user_id) and data.get("status") == "open":
            return channel
    return None


async def update_ticket_metadata(
    channel: discord.TextChannel,
    *,
    status: Optional[str] = None,
    claimed_by: Optional[int] = None,
    ping_role_ids: Optional[list[int]] = None,
) -> None:
    data = parse_ticket_topic(channel.topic)
    owner_id = int(data.get("ticket_owner", "0") or 0)
    ticket_type = data.get("ticket_type", "ticket")
    current_status = status or data.get("status", "open")
    current_claimed_by = claimed_by if claimed_by is not None else int(data.get("claimed_by", "0") or 0)
    current_ping_role_ids = ping_role_ids if ping_role_ids is not None else get_ticket_ping_role_ids(channel)

    await channel.edit(
        topic=build_ticket_topic(
            owner_id=owner_id,
            ticket_type=ticket_type,
            status=current_status,
            claimed_by=current_claimed_by,
            ping_role_ids=current_ping_role_ids,
        )
    )


async def log_event(
    bot: "TicketBot",
    guild: discord.Guild,
    *,
    title: str,
    description: str,
    color: discord.Color,
    file: Optional[discord.File] = None,
) -> None:
    log_channel = await get_log_channel(bot, guild)
    if log_channel is None:
        return

    embed = discord.Embed(title=title, description=description, color=color, timestamp=now_utc())
    await log_channel.send(embed=embed, file=file)


async def append_history_lines(lines: list[str], source, *, label: str) -> None:
    lines.append(f"--- {label} ---")

    try:
        async for message in source.history(limit=None, oldest_first=True):
            created = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            author = f"{message.author} ({message.author.id})"
            content = message.content or ""
            lines.append(f"[{created}] {author}: {content}")

            if message.attachments:
                for attachment in message.attachments:
                    lines.append(f"    attachment: {attachment.url}")

            if message.embeds:
                lines.append(f"    embeds: {len(message.embeds)}")
    except discord.HTTPException:
        lines.append("Unable to read this section from Discord.")


def transcript_file_from_text(text_value: str, filename: str) -> discord.File:
    payload = text_value.encode("utf-8")
    return discord.File(io.BytesIO(payload), filename=filename)


async def build_ticket_and_notes_transcript_text(
    channel: discord.TextChannel,
    notes_thread: Optional[discord.Thread] = None,
) -> str:
    lines: list[str] = []
    lines.append(f"Transcript for #{channel.name}")
    lines.append(f"Ticket ID: {get_ticket_log_id(channel)}")
    lines.append(f"Channel ID: {channel.id}")
    lines.append(f"Generated: {now_utc().isoformat()}")
    lines.append("=" * 70)
    lines.append("")

    await append_history_lines(lines, channel, label="TICKET CONVERSATION")

    lines.append("")
    lines.append("=" * 70)
    lines.append("STAFF NOTES")
    lines.append("=" * 70)

    if notes_thread is not None:
        lines.append(f"Notes Thread: #{notes_thread.name} ({notes_thread.id})")
        await append_history_lines(lines, notes_thread, label="STAFF NOTES THREAD")
    else:
        lines.append("No staff notes thread was linked to this ticket.")

    return "\n".join(lines)


async def build_transcript_file(channel: discord.TextChannel) -> discord.File:
    text_value = await build_ticket_and_notes_transcript_text(channel, None)
    return transcript_file_from_text(text_value, f"{channel.name}-transcript.txt")


def save_ticket_log_text(guild_id: int, ticket_id: str, transcript_text: str) -> Path:
    path = get_ticket_log_path(guild_id, ticket_id)
    path.write_text(transcript_text, encoding="utf-8")
    return path


async def get_notes_thread(
    bot: "TicketBot",
    guild: discord.Guild,
    channel: discord.TextChannel,
) -> Optional[discord.Thread]:
    thread_id = get_notes_thread_id(bot, guild.id, channel.id)
    if not thread_id:
        return None

    thread = guild.get_thread(thread_id)
    if thread is not None:
        return thread

    try:
        fetched = await bot.fetch_channel(thread_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None

    return fetched if isinstance(fetched, discord.Thread) else None


async def create_or_get_notes_thread(
    bot: "TicketBot",
    guild: discord.Guild,
    channel: discord.TextChannel,
    creator: discord.Member,
) -> tuple[Optional[discord.Thread], str]:
    existing = await get_notes_thread(bot, guild, channel)
    if existing is not None:
        return existing, "existing"

    try:
        thread = await channel.create_thread(
            name=f"staff-notes-{channel.name}"[:100],
            type=discord.ChannelType.private_thread,
            invitable=False,
            reason=f"Staff notes created by {creator} ({creator.id})",
        )
    except TypeError:
        thread = await channel.create_thread(
            name=f"staff-notes-{channel.name}"[:100],
            type=discord.ChannelType.private_thread,
            reason=f"Staff notes created by {creator} ({creator.id})",
        )
    except discord.Forbidden:
        return None, "I could not create a private notes thread. Give me Manage Threads permission."
    except discord.HTTPException:
        return None, "Discord refused the notes thread request. This server may not support private threads here."

    set_notes_thread_id(bot, guild.id, channel.id, thread.id)

    await thread.send(
        "📝 **Staff Notes**\n"
        "Use this private thread for internal discussion. These notes are appended to the ticket transcript when the ticket closes."
    )
    return thread, "created"

async def create_ticket_channel(bot: "TicketBot", interaction: discord.Interaction) -> tuple[Optional[discord.TextChannel], str]:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return None, "This can only be used inside a server."

    if not bot.config_store.is_ready(interaction.guild.id):
        return None, "This server is not configured yet. An admin should run /ticket setup first."

    category = await get_ticket_category(bot, interaction.guild)
    if category is None:
        return None, "The configured ticket category no longer exists. Run /ticket setup again."

    existing = find_open_ticket_for_user(category, interaction.user.id)
    if existing:
        return existing, f"You already have an open ticket: {existing.mention}"

    display_name = slugify(interaction.user.display_name)[:18]
    channel_name = f"{TICKET_CONFIG['channel_prefix']}-{display_name}-{str(interaction.user.id)[-4:]}"
    channel_name = channel_name[:95]

    guild = interaction.guild
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
    }

    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
            attach_files=True,
            embed_links=True,
        )

    for role in get_staff_roles(bot, guild):
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            attach_files=True,
            embed_links=True,
        )

    ping_roles = get_guild_ping_roles(bot, guild)
    ping_role_ids = [role.id for role in ping_roles]

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            topic=build_ticket_topic(owner_id=interaction.user.id, ticket_type="ticket", ping_role_ids=ping_role_ids),
            overwrites=overwrites,
            reason=f"Ticket opened by {interaction.user} ({interaction.user.id})",
        )
    except discord.Forbidden:
        return None, (
            "I couldn't create the ticket channel.\n\n"
            "Make sure the bot can:\n"
            "• View the ticket category\n"
            "• Manage Channels\n"
            "• Send Messages"
        )
    except discord.HTTPException:
        return None, "Discord refused the ticket channel create request. Try again in a moment."

    # Keep ticket creation confirmation private/ephemeral.
    # Do not post a permanent "user opened a ticket" ping line in the ticket channel.
    opening_mentions = None

    embed = discord.Embed(
        title=f"{TICKET_CONFIG['emoji']} {TICKET_CONFIG['label']}",
        description=(
            "Thanks for opening a ticket. A staff member will help you soon.\n\n"
            "Use the buttons below to manage the ticket."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="User", value=interaction.user.mention, inline=False)
    embed.add_field(name="Status", value="Open", inline=True)
    embed.add_field(name="Claimed By", value="Not claimed yet", inline=True)
    embed.set_footer(text=f"User ID: {interaction.user.id}")

    try:
        await channel.send(
            content=opening_mentions,
            embed=embed,
            view=TicketChannelView(bot),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.Forbidden:
        return None, "I created the channel but couldn't post the starter message. Check the category/channel permissions."
    except discord.HTTPException:
        return None, "I created the channel but Discord rejected the starter message."

    return channel, "created"


class AddUserModal(discord.ui.Modal, title="Add User To Ticket"):
    user_input = discord.ui.TextInput(
        label="User ID or mention",
        placeholder="Paste a user ID or mention the user",
        max_length=64,
    )

    def __init__(self, bot: "TicketBot", channel: discord.TextChannel):
        super().__init__(timeout=300)
        self.bot = bot
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not member_is_staff(self.bot, interaction.user):
            await interaction.response.send_message("Only ticket staff can add users.", ephemeral=True)
            return

        user_id = extract_id(str(self.user_input.value))
        if user_id is None:
            await interaction.response.send_message("I could not find a user ID in that input.", ephemeral=True)
            return

        member = await fetch_member_safe(interaction.guild, user_id)
        if member is None:
            await interaction.response.send_message("That user is not in this server.", ephemeral=True)
            return

        overwrite = self.channel.overwrites_for(member)
        overwrite.view_channel = True
        overwrite.send_messages = True
        overwrite.read_message_history = True
        overwrite.attach_files = True
        overwrite.embed_links = True

        await self.channel.set_permissions(member, overwrite=overwrite, reason=f"Added to ticket by {interaction.user}")
        await self.channel.send(f"➕ {member.mention} was added to this ticket by {interaction.user.mention}.")
        await interaction.response.send_message(f"Added {member.mention} to the ticket.", ephemeral=True)


class RemoveUserModal(discord.ui.Modal, title="Remove User From Ticket"):
    user_input = discord.ui.TextInput(
        label="User ID or mention",
        placeholder="Paste a user ID or mention the user",
        max_length=64,
    )

    def __init__(self, bot: "TicketBot", channel: discord.TextChannel):
        super().__init__(timeout=300)
        self.bot = bot
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not member_is_staff(self.bot, interaction.user):
            await interaction.response.send_message("Only ticket staff can remove users.", ephemeral=True)
            return

        user_id = extract_id(str(self.user_input.value))
        if user_id is None:
            await interaction.response.send_message("I could not find a user ID in that input.", ephemeral=True)
            return

        owner_id = get_ticket_owner_id(self.channel)
        if owner_id == user_id:
            await interaction.response.send_message("You cannot remove the ticket owner.", ephemeral=True)
            return

        member = await fetch_member_safe(interaction.guild, user_id)
        if member is None:
            await interaction.response.send_message("That user is not in this server.", ephemeral=True)
            return

        await self.channel.set_permissions(member, overwrite=None, reason=f"Removed from ticket by {interaction.user}")
        await self.channel.send(f"➖ {member.mention} was removed from this ticket by {interaction.user.mention}.")
        await interaction.response.send_message(f"Removed {member.mention} from the ticket.", ephemeral=True)


class AddRoleModal(discord.ui.Modal, title="Add Role To Ticket"):
    role_input = discord.ui.TextInput(
        label="Role ID or mention",
        placeholder="Paste a role mention or role ID",
        max_length=64,
    )

    def __init__(self, bot: "TicketBot", channel: discord.TextChannel):
        super().__init__(timeout=300)
        self.bot = bot
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not member_is_staff(self.bot, interaction.user):
            await interaction.response.send_message("Only ticket staff can add roles.", ephemeral=True)
            return

        role_id = extract_id(str(self.role_input.value))
        if role_id is None:
            await interaction.response.send_message("I could not find a role ID in that input.", ephemeral=True)
            return

        role = interaction.guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message("That role is not in this server.", ephemeral=True)
            return

        overwrite = self.channel.overwrites_for(role)
        overwrite.view_channel = True
        overwrite.send_messages = True
        overwrite.read_message_history = True
        overwrite.attach_files = True
        overwrite.embed_links = True

        await self.channel.set_permissions(role, overwrite=overwrite, reason=f"Role added to ticket by {interaction.user}")
        await self.channel.send(f"🔓 {role.mention} can now see this ticket.")
        await interaction.response.send_message(f"Added {role.mention} to this ticket.", ephemeral=True)


class RemoveRoleModal(discord.ui.Modal, title="Remove Role From Ticket"):
    role_input = discord.ui.TextInput(
        label="Role ID or mention",
        placeholder="Paste a role mention or role ID",
        max_length=64,
    )

    def __init__(self, bot: "TicketBot", channel: discord.TextChannel):
        super().__init__(timeout=300)
        self.bot = bot
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not member_is_staff(self.bot, interaction.user):
            await interaction.response.send_message("Only ticket staff can remove roles.", ephemeral=True)
            return

        role_id = extract_id(str(self.role_input.value))
        if role_id is None:
            await interaction.response.send_message("I could not find a role ID in that input.", ephemeral=True)
            return

        role = interaction.guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message("That role is not in this server.", ephemeral=True)
            return

        if role.is_default():
            await interaction.response.send_message("You can't edit @everyone here.", ephemeral=True)
            return

        overwrite = self.channel.overwrites_for(role)
        overwrite.view_channel = False
        overwrite.send_messages = False
        overwrite.read_message_history = False
        overwrite.attach_files = False
        overwrite.embed_links = False

        await self.channel.set_permissions(role, overwrite=overwrite, reason=f"Role removed from ticket by {interaction.user}")
        await self.channel.send(f"🔒 {role.mention} can no longer see this ticket.")
        await interaction.response.send_message(f"Removed {role.mention} from this ticket.", ephemeral=True)


class SetPingRolesModal(discord.ui.Modal, title="Set Ticket Ping Roles"):
    roles_input = discord.ui.TextInput(
        label="Role mentions or IDs",
        placeholder="Example: @Support @Moderators",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=400,
    )

    def __init__(self, bot: "TicketBot", channel: discord.TextChannel):
        super().__init__(timeout=300)
        self.bot = bot
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not member_is_staff(self.bot, interaction.user):
            await interaction.response.send_message("Only ticket staff can edit ping roles.", ephemeral=True)
            return

        role_ids = extract_ids(str(self.roles_input.value))
        valid_roles: list[discord.Role] = []
        for role_id in role_ids:
            role = interaction.guild.get_role(role_id)
            if role is not None and not role.is_default():
                valid_roles.append(role)

        await update_ticket_metadata(self.channel, ping_role_ids=[role.id for role in valid_roles])

        if valid_roles:
            mentions = ", ".join(role.mention for role in valid_roles)
            await self.channel.send(f"📣 Ticket ping roles updated by {interaction.user.mention}: {mentions}")
            await interaction.response.send_message(f"Updated ticket ping roles: {mentions}", ephemeral=True)
        else:
            await self.channel.send(f"📣 Ticket ping roles cleared by {interaction.user.mention}.")
            await interaction.response.send_message("Cleared the ticket ping roles.", ephemeral=True)


class AdminRoleConfigModal(discord.ui.Modal):
    roles_input = discord.ui.TextInput(
        label="Role mentions or IDs",
        placeholder="Example: @Support @Moderators",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=400,
    )

    def __init__(self, bot: "TicketBot", action: str, target: str):
        title_map = {
            ("add", "staff"): "Add Default Staff Roles",
            ("remove", "staff"): "Remove Default Staff Roles",
            ("add", "ping"): "Add Default Ping Roles",
            ("remove", "ping"): "Remove Default Ping Roles",
        }
        super().__init__(title=title_map[(action, target)], timeout=300)
        self.bot = bot
        self.action = action
        self.target = target

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only admins can edit default ticket roles.", ephemeral=True)
            return

        role_ids = extract_ids(str(self.roles_input.value))
        roles: list[discord.Role] = []
        for role_id in role_ids:
            role = interaction.guild.get_role(role_id)
            if role is not None and not role.is_default():
                roles.append(role)

        if not roles:
            await interaction.response.send_message("I could not find any valid roles in that input.", ephemeral=True)
            return

        if self.target == "staff":
            if self.action == "add":
                for role in roles:
                    self.bot.config_store.add_staff_role(interaction.guild.id, role.id)
                msg = f"Added default staff roles: {', '.join(role.mention for role in roles)}"
            else:
                for role in roles:
                    self.bot.config_store.remove_staff_role(interaction.guild.id, role.id)
                msg = f"Removed default staff roles: {', '.join(role.mention for role in roles)}"
        else:
            if self.action == "add":
                for role in roles:
                    self.bot.config_store.add_ping_role(interaction.guild.id, role.id)
                msg = f"Added default ping roles: {', '.join(role.mention for role in roles)}"
            else:
                for role in roles:
                    self.bot.config_store.remove_ping_role(interaction.guild.id, role.id)
                msg = f"Removed default ping roles: {', '.join(role.mention for role in roles)}"

        await interaction.response.send_message(msg, ephemeral=True)


class AdminPanelGifModal(discord.ui.Modal, title="Set Panel GIF or Image"):
    image_input = discord.ui.TextInput(
        label="Image/GIF URL",
        placeholder="Direct image URL or Imgur page URL. Leave blank to clear.",
        required=False,
        max_length=400,
    )

    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=300)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only admins can edit the panel image.", ephemeral=True)
            return

        cleaned = normalize_panel_image_url(str(self.image_input.value))
        current = self.bot.config_store.get_guild(interaction.guild.id)
        self.bot.config_store.update_guild(
            interaction.guild.id,
            ticket_category_id=current.get("ticket_category_id"),
            log_channel_id=current.get("log_channel_id"),
            staff_role_ids=current.get("staff_role_ids", []),
            ping_role_ids=current.get("ping_role_ids", []),
            panel_gif_url=cleaned,
        )

        if cleaned:
            await interaction.response.send_message(f"Saved the panel image URL:\n{cleaned}", ephemeral=True)
        else:
            await interaction.response.send_message("Cleared the saved panel image URL.", ephemeral=True)


class CloseTicketModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Resolved, duplicate, invalid report, etc.",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=False,
    )

    def __init__(self, bot: "TicketBot", channel: discord.TextChannel):
        super().__init__(timeout=300)
        self.bot = bot
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
            return

        owner_id = get_ticket_owner_id(self.channel)
        if owner_id is None:
            await interaction.response.send_message("This does not look like a valid ticket channel.", ephemeral=True)
            return

        if interaction.user.id != owner_id and not member_is_staff(self.bot, interaction.user):
            await interaction.response.send_message(
                "Only the ticket owner or ticket staff can close this ticket.",
                ephemeral=True,
            )
            return

        reason_text = str(self.reason.value).strip() or "No reason provided."
        await interaction.response.send_message("Closing the ticket and saving transcript...", ephemeral=True)

        topic_data = parse_ticket_topic(self.channel.topic)
        claimed_by = int(topic_data.get("claimed_by", "0") or 0)
        ping_role_ids = get_ticket_ping_role_ids(self.channel)

        await self.channel.edit(
            topic=build_ticket_topic(
                owner_id=owner_id,
                ticket_type=topic_data.get("ticket_type", "ticket"),
                status="closed",
                claimed_by=claimed_by,
                ping_role_ids=ping_role_ids,
            )
        )

        notes_thread = await get_notes_thread(self.bot, interaction.guild, self.channel)
        transcript_text = await build_ticket_and_notes_transcript_text(self.channel, notes_thread)
        ticket_log_id = get_ticket_log_id(self.channel)
        save_ticket_log_text(interaction.guild.id, ticket_log_id, transcript_text)
        transcript = transcript_file_from_text(
            transcript_text,
            f"ticket-{ticket_log_id}-{self.channel.name}-transcript.txt",
        )

        notes_line = f"\nNotes thread: {notes_thread.mention}" if notes_thread else ""
        await log_event(
            self.bot,
            interaction.guild,
            title="Ticket Closed",
            description=(
                f"Ticket ID: `{ticket_log_id}`\n"
                f"Channel: #{self.channel.name}\n"
                f"Closed by: {interaction.user.mention}\n"
                f"Owner ID: {owner_id}\n"
                f"Reason: {truncate(reason_text, 1000)}"
                f"{notes_line}"
            ),
            color=discord.Color.red(),
            file=transcript,
        )

        await self.channel.send(f"🔒 Ticket closed by {interaction.user.mention}. Reason: {reason_text}")
        await asyncio.sleep(4)
        await self.channel.delete(reason=f"Ticket closed by {interaction.user} ({interaction.user.id})")


class TicketOpenButton(discord.ui.Button):
    def __init__(self, bot: "TicketBot"):
        super().__init__(
            label=TICKET_CONFIG["label"],
            emoji=TICKET_CONFIG["emoji"],
            style=discord.ButtonStyle.primary,
            custom_id="ticket:open",
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        channel, status = await create_ticket_channel(self.bot, interaction)
        if channel is None:
            await interaction.followup.send(status, ephemeral=True)
            return

        if status == "created":
            await interaction.followup.send(f"Your ticket has been created: {channel.mention}", ephemeral=True)
        else:
            await interaction.followup.send(status, ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=None)
        self.add_item(TicketOpenButton(bot))


class TicketChannelView(discord.ui.View):
    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, custom_id="ticket:claim", row=0)
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        if not member_is_staff(self.bot, interaction.user):
            await interaction.response.send_message("Only ticket staff can claim tickets.", ephemeral=True)
            return

        topic_data = parse_ticket_topic(channel.topic)
        claimed_by = int(topic_data.get("claimed_by", "0") or 0)
        if claimed_by == interaction.user.id:
            await interaction.response.send_message("You already claimed this ticket.", ephemeral=True)
            return

        await update_ticket_metadata(channel, claimed_by=interaction.user.id)
        await channel.send(f"📌 Ticket claimed by {interaction.user.mention}.")
        await interaction.response.send_message("You claimed this ticket.", ephemeral=True)

    @discord.ui.button(label="Ping Team", style=discord.ButtonStyle.secondary, custom_id="ticket:ping_team", row=0)
    async def ping_team_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        owner_id = get_ticket_owner_id(channel)
        is_owner = owner_id == interaction.user.id
        is_staff = member_is_staff(self.bot, interaction.user)
        if not is_owner and not is_staff:
            await interaction.response.send_message("Only the ticket owner or ticket staff can ping the team here.", ephemeral=True)
            return

        last_ping = self.bot.ticket_ping_cooldowns.get(channel.id)
        if last_ping is not None:
            elapsed = (now_utc() - last_ping).total_seconds()
            remaining = int(TICKET_PING_COOLDOWN_SECONDS - elapsed)
            if remaining > 0:
                minutes = remaining // 60
                seconds = remaining % 60
                await interaction.response.send_message(
                    f"Ping Team is on cooldown for this ticket. Try again in {minutes}m {seconds}s.",
                    ephemeral=True,
                )
                return

        ping_roles = get_ticket_ping_roles(self.bot, interaction.guild, channel)
        if not ping_roles:
            await interaction.response.send_message(
                "No ticket ping roles are set. Staff can use **Set Ping Roles** or `/ticket addpingrole`.",
                ephemeral=True,
            )
            return

        self.bot.ticket_ping_cooldowns[channel.id] = now_utc()
        await channel.send(
            f"📣 {interaction.user.mention} requested staff attention: {mention_roles(ping_roles)}",
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        await interaction.response.send_message("Pinged the configured ticket roles. This ticket can ping again in 10 minutes.", ephemeral=True)

    @discord.ui.button(label="Set Ping Roles", style=discord.ButtonStyle.secondary, custom_id="ticket:set_ping_roles", row=0)
    async def set_ping_roles_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        await interaction.response.send_modal(SetPingRolesModal(self.bot, channel))

    # Add/Remove User are intentionally not exposed as public ticket buttons.
    # They remain protected helper modals for future staff-only workflows.

    @discord.ui.button(label="Add Role", style=discord.ButtonStyle.secondary, custom_id="ticket:add_role", row=2)
    async def add_role_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        await interaction.response.send_modal(AddRoleModal(self.bot, channel))

    @discord.ui.button(label="Remove Role", style=discord.ButtonStyle.secondary, custom_id="ticket:remove_role", row=2)
    async def remove_role_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        await interaction.response.send_modal(RemoveRoleModal(self.bot, channel))

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket:close", row=3)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        await interaction.response.send_modal(CloseTicketModal(self.bot, channel))


class ConfigRoleSelect(discord.ui.Select):
    def __init__(self, bot: "TicketBot", guild: discord.Guild, target: str, action: str):
        self.bot = bot
        self.target = target
        self.action = action
        self.guild_id = guild.id

        roles = get_selectable_roles(bot, guild, target, action)
        options: list[discord.SelectOption] = []
        for role in roles:
            label = role.name[:100]
            description = f"ID: {role.id}"
            options.append(discord.SelectOption(label=label, value=str(role.id), description=description))

        if not options:
            options.append(discord.SelectOption(label="No roles available", value="0", description="Nothing to select right now."))

        target_label = "staff" if target == "staff" else "ping"
        action_label = "add" if action == "add" else "remove"
        super().__init__(
            placeholder=f"Choose a role to {action_label} from {target_label} roles...",
            min_values=1,
            max_values=1,
            options=options,
        )
        if not roles:
            self.disabled = True

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only admins can edit default ticket roles.", ephemeral=True)
            return

        role_id = int(self.values[0])
        role = interaction.guild.get_role(role_id)
        if role is None or role.is_default():
            await interaction.response.send_message("That role is no longer available.", ephemeral=True)
            return

        if self.target == "staff":
            if self.action == "add":
                self.bot.config_store.add_staff_role(interaction.guild.id, role.id)
                result = f"Added {role.mention} as a ticket staff role."
            else:
                self.bot.config_store.remove_staff_role(interaction.guild.id, role.id)
                result = f"Removed {role.mention} from ticket staff roles."
        else:
            if self.action == "add":
                self.bot.config_store.add_ping_role(interaction.guild.id, role.id)
                result = f"Added {role.mention} as a default ticket ping role."
            else:
                self.bot.config_store.remove_ping_role(interaction.guild.id, role.id)
                result = f"Removed {role.mention} from default ticket ping roles."

        embed = build_role_config_embed(self.bot, interaction.guild, self.target, self.action)
        embed.add_field(name="Updated", value=result, inline=False)
        await interaction.response.edit_message(embed=embed, view=RoleConfigSelectView(self.bot, interaction.guild, self.target, self.action))


class RoleConfigSelectView(discord.ui.View):
    def __init__(self, bot: "TicketBot", guild: discord.Guild, target: str, action: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild.id
        self.target = target
        self.action = action
        self.add_item(ConfigRoleSelect(bot, guild, target, action))

    @discord.ui.button(label="Refresh List", style=discord.ButtonStyle.primary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        embed = build_role_config_embed(self.bot, interaction.guild, self.target, self.action)
        await interaction.response.edit_message(embed=embed, view=RoleConfigSelectView(self.bot, interaction.guild, self.target, self.action))


async def send_role_config_panel(interaction: discord.Interaction, bot: "TicketBot", target: str, action: str) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
        return

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only admins can edit default ticket roles.", ephemeral=True)
        return

    embed = build_role_config_embed(bot, interaction.guild, target, action)
    await interaction.response.send_message(embed=embed, view=RoleConfigSelectView(bot, interaction.guild, target, action), ephemeral=True)


class TicketAdminPanelView(discord.ui.View):
    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=600)
        self.bot = bot

    def _get_ticket_channel(self, interaction: discord.Interaction) -> Optional[discord.TextChannel]:
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel) and is_ticket_channel(channel):
            return channel
        return None

    @discord.ui.button(label="Add Staff", style=discord.ButtonStyle.secondary, row=0)
    async def add_staff_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "staff", "add")

    @discord.ui.button(label="Remove Staff", style=discord.ButtonStyle.secondary, row=0)
    async def remove_staff_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "staff", "remove")

    @discord.ui.button(label="Add Ping Role", style=discord.ButtonStyle.secondary, row=0)
    async def add_ping_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "ping", "add")

    @discord.ui.button(label="Remove Ping Role", style=discord.ButtonStyle.secondary, row=0)
    async def remove_ping_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "ping", "remove")

    @discord.ui.button(label="Add Role", style=discord.ButtonStyle.secondary, row=1)
    async def add_role_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = self._get_ticket_channel(interaction)
        if channel is None:
            await interaction.response.send_message("Run `/ticket admin` inside a ticket channel to add roles there.", ephemeral=True)
            return
        await interaction.response.send_modal(AddRoleModal(self.bot, channel))

    @discord.ui.button(label="Remove Role", style=discord.ButtonStyle.secondary, row=1)
    async def remove_role_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = self._get_ticket_channel(interaction)
        if channel is None:
            await interaction.response.send_message("Run `/ticket admin` inside a ticket channel to remove roles there.", ephemeral=True)
            return
        await interaction.response.send_modal(RemoveRoleModal(self.bot, channel))

    @discord.ui.button(label="Set Ticket Ping", style=discord.ButtonStyle.secondary, row=1)
    async def set_ticket_ping_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = self._get_ticket_channel(interaction)
        if channel is None:
            await interaction.response.send_message("Run `/ticket admin` inside a ticket channel to edit that ticket's ping roles.", ephemeral=True)
            return
        await interaction.response.send_modal(SetPingRolesModal(self.bot, channel))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=2)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        embed = build_admin_panel_embed(self.bot, interaction.guild, interaction.channel)
        await interaction.response.edit_message(embed=embed, view=self)

@app_commands.guild_only()
class TicketCommands(commands.GroupCog, group_name="ticket", group_description="Ticket bot commands"):
    def __init__(self, bot: "TicketBot"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="setup", description="Set the ticket category and closed-ticket log channel for this server.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        category="Category where private ticket channels will be created",
        log_channel="Text channel where CLOSED ticket logs and transcript files will be sent",
    )
    async def setup_ticket(
        self,
        interaction: discord.Interaction,
        category: discord.app_commands.AppCommandChannel,
        log_channel: discord.app_commands.AppCommandChannel,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        try:
            real_category = category.resolve() or await category.fetch()
        except discord.HTTPException:
            real_category = None

        try:
            real_log_channel = log_channel.resolve() or await log_channel.fetch()
        except discord.HTTPException:
            real_log_channel = None

        if not isinstance(real_category, discord.CategoryChannel):
            picked_type = getattr(category, "type", "unknown")
            await interaction.response.send_message(
                (
                    "The **category** option must be a real Discord category.\n"
                    "This is where private ticket channels get created.\n\n"
                    f"You picked: {category.mention} (`{picked_type}`)"
                ),
                ephemeral=True,
            )
            return

        if not isinstance(real_log_channel, discord.TextChannel):
            picked_type = getattr(log_channel, "type", "unknown")
            await interaction.response.send_message(
                (
                    "The **log_channel** option must be a normal text channel.\n"
                    "This is where CLOSED ticket logs and transcript files are sent.\n\n"
                    f"You picked: {log_channel.mention} (`{picked_type}`)"
                ),
                ephemeral=True,
            )
            return

        current = self.bot.config_store.get_guild(interaction.guild.id)
        config = self.bot.config_store.update_guild(
            interaction.guild.id,
            ticket_category_id=real_category.id,
            log_channel_id=real_log_channel.id,
            staff_role_ids=current.get("staff_role_ids", []),
            ping_role_ids=current.get("ping_role_ids", []),
            panel_gif_url=current.get("panel_gif_url", ""),
        )

        await interaction.response.send_message(
            (
                "Saved ticket setup for this server.\n\n"
                f"**Ticket category:** {real_category.name}\n"
                "Used for newly created private ticket channels.\n\n"
                f"**Closed-ticket log channel:** {real_log_channel.mention}\n"
                "Used when tickets close. The transcript file will be posted there automatically.\n\n"
                f"**Staff roles configured:** {len(config.get('staff_role_ids', []))}\n\n"
                "Run `/ticket panel` in the channel where you want the ticket embed posted."
            ),
            ephemeral=True,
        )

    @app_commands.command(name="addstaff", description="Add a staff role that can access tickets.")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_staff(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.add_staff_role(interaction.guild.id, role.id)
        await interaction.response.send_message(
            f"Added {role.mention} as ticket staff. Total staff roles: {len(config.get('staff_role_ids', []))}",
            ephemeral=True,
        )

    @app_commands.command(name="removestaff", description="Remove a staff role from ticket access.")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_staff(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.remove_staff_role(interaction.guild.id, role.id)
        await interaction.response.send_message(
            f"Removed {role.mention} from ticket staff. Total staff roles: {len(config.get('staff_role_ids', []))}",
            ephemeral=True,
        )

    @app_commands.command(name="addpingrole", description="Add a default ping role for new tickets.")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_ping_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.add_ping_role(interaction.guild.id, role.id)
        await interaction.response.send_message(
            f"Added {role.mention} to the default ticket ping list. Total ping roles: {len(config.get('ping_role_ids', []))}",
            ephemeral=True,
        )

    @app_commands.command(name="removepingrole", description="Remove a default ping role for new tickets.")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_ping_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.remove_ping_role(interaction.guild.id, role.id)
        await interaction.response.send_message(
            f"Removed {role.mention} from the default ticket ping list. Total ping roles: {len(config.get('ping_role_ids', []))}",
            ephemeral=True,
        )

    @app_commands.command(name="panelgif", description="Set or clear the GIF/image shown on the ticket panel.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        image_url="Direct image URL. Imgur page URLs are also accepted. Leave blank to clear.",
        image_file="Upload a GIF/image directly to use on the panel.",
    )
    async def panel_gif(
        self,
        interaction: discord.Interaction,
        image_url: Optional[str] = None,
        image_file: Optional[discord.Attachment] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        chosen = image_file.url if image_file else (image_url or "")
        cleaned = normalize_panel_image_url(chosen)
        current = self.bot.config_store.get_guild(interaction.guild.id)

        self.bot.config_store.update_guild(
            interaction.guild.id,
            ticket_category_id=current.get("ticket_category_id"),
            log_channel_id=current.get("log_channel_id"),
            staff_role_ids=current.get("staff_role_ids", []),
            ping_role_ids=current.get("ping_role_ids", []),
            panel_gif_url=cleaned,
        )

        if cleaned:
            await interaction.response.send_message(
                f"Saved the panel GIF/image URL for this server:\n{cleaned}",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Cleared the saved panel GIF/image URL for this server.",
                ephemeral=True,
            )


    @app_commands.command(name="settag", description="Create or update a reusable staff tag response.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(name="Short tag name, like rules or payment", response="Message the bot should send when staff uses /ticket tag")
    async def set_tag_command(self, interaction: discord.Interaction, name: str, response: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        clean_name = clean_tag_name(name)
        if not clean_name:
            await interaction.response.send_message("Use a tag name with letters or numbers.", ephemeral=True)
            return

        set_tag(self.bot, interaction.guild.id, clean_name, response)
        await interaction.response.send_message(f"Saved tag `{clean_name}`. Staff can now use `/ticket tag name:{clean_name}` inside tickets.", ephemeral=True)

    @app_commands.command(name="removetag", description="Remove a reusable staff tag response.")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_tag_command(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        clean_name = clean_tag_name(name)
        existed = remove_tag(self.bot, interaction.guild.id, clean_name)
        if existed:
            await interaction.response.send_message(f"Removed tag `{clean_name}`.", ephemeral=True)
        else:
            await interaction.response.send_message(f"No tag named `{clean_name}` was found.", ephemeral=True)

    @app_commands.command(name="tags", description="List saved staff tag responses for this server.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def list_tags_command(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        tags = get_tags(self.bot, interaction.guild.id)
        if not tags:
            await interaction.response.send_message("No tags are saved yet. Use `/ticket settag` first.", ephemeral=True)
            return

        lines = [f"`{name}` — {truncate(value, 120)}" for name, value in sorted(tags.items())]
        await interaction.response.send_message("Saved tags:\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="tag", description="Send a saved staff tag response inside a ticket.")
    async def send_tag_command(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("Use `/ticket tag` inside a ticket channel.", ephemeral=True)
            return

        if not member_is_staff(self.bot, interaction.user):
            await interaction.response.send_message("Only ticket staff can use saved tags.", ephemeral=True)
            return

        clean_name = clean_tag_name(name)
        response = get_tags(self.bot, interaction.guild.id).get(clean_name)
        if not response:
            await interaction.response.send_message(f"No tag named `{clean_name}` was found. Use `/ticket tags` to view saved tags.", ephemeral=True)
            return

        await interaction.response.send_message(response, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="notes", description="Create or open the staff-only notes thread for this ticket.")
    async def ticket_notes(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("Use `/ticket notes` inside a ticket channel.", ephemeral=True)
            return

        if not member_is_staff(self.bot, interaction.user):
            await interaction.response.send_message("Only ticket staff can open staff notes.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        thread, status = await create_or_get_notes_thread(self.bot, interaction.guild, channel, interaction.user)
        if thread is None:
            await interaction.followup.send(status, ephemeral=True)
            return

        msg = "Created" if status == "created" else "Opened existing"
        await interaction.followup.send(f"{msg} staff notes thread: {thread.mention}\nNotes will be appended under a **STAFF NOTES** divider in the ticket transcript.", ephemeral=True)


    @app_commands.command(name="admin", description="Open the ticket admin control panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_admin(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        embed = build_admin_panel_embed(self.bot, interaction.guild, interaction.channel)
        await interaction.response.send_message(embed=embed, view=TicketAdminPanelView(self.bot), ephemeral=True)

    @app_commands.command(name="config", description="Show the ticket configuration for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def show_config(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.get_guild(interaction.guild.id)
        category = interaction.guild.get_channel(config.get("ticket_category_id", 0))
        log_channel = interaction.guild.get_channel(config.get("log_channel_id", 0))
        staff_mentions: list[str] = []
        ping_mentions: list[str] = []

        for role_id in config.get("staff_role_ids", []):
            role = interaction.guild.get_role(role_id)
            staff_mentions.append(role.mention if role else f"`{role_id}`")

        for role_id in config.get("ping_role_ids", []):
            role = interaction.guild.get_role(role_id)
            ping_mentions.append(role.mention if role else f"`{role_id}`")

        panel_url = get_panel_gif_url(self.bot, interaction.guild.id) or "Not set"

        embed = discord.Embed(title="Ticket Configuration", color=discord.Color.blurple())
        embed.add_field(name="Ticket category", value=category.mention if category else "Not set", inline=False)
        embed.add_field(name="Closed-ticket log channel", value=log_channel.mention if log_channel else "Not set", inline=False)
        embed.add_field(name="Staff roles", value="\n".join(staff_mentions) if staff_mentions else "None", inline=False)
        embed.add_field(name="Default ping roles", value="\n".join(ping_mentions) if ping_mentions else "None", inline=False)
        embed.add_field(name="Panel GIF/Image", value=truncate(panel_url, 1024), inline=False)
        embed.add_field(
            name="In-ticket controls",
            value="Use the ticket buttons to claim/ping and `/ticket admin` inside a ticket for role and ping tools. Use `/ticket notes` for staff notes.",
            inline=False,
        )
        embed.add_field(name="Ready", value="Yes" if self.bot.config_store.is_ready(interaction.guild.id) else "No", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="panel", description="Post the ticket panel in the current channel.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def ticket_panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        if not self.bot.config_store.is_ready(interaction.guild.id):
            await interaction.response.send_message(
                "This server is not configured yet. Run `/ticket setup` and `/ticket addstaff` first.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Open a Ticket",
            description=(
                "Press the button below to open a private ticket.\n\n"
                "A private text channel will be created for you and the ticket staff."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="How setup works",
            value=(
                "`/ticket setup` sets where tickets are created and where closed-ticket transcripts go.\n"
                "`/ticket addstaff` controls who can see all tickets.\n"
                "`/ticket addpingrole` controls who gets pinged by default.\n"
                "`/ticket admin` opens the control panel for live edits."
            ),
            inline=False,
        )
        embed.add_field(
            name="Inside the ticket",
            value="Buttons included: **Claim**, **Ping Team** with a 10-minute cooldown, **Set Ping Roles**, **Add Role**, **Remove Role**, **Close**",
            inline=False,
        )
        panel_gif_url = get_panel_gif_url(self.bot, interaction.guild.id)
        if panel_gif_url:
            embed.set_image(url=panel_gif_url)
        embed.set_footer(text="One open ticket per user.")

        await interaction.response.send_message("Ticket panel posted.", ephemeral=True)
        await interaction.followup.send(embed=embed, view=TicketPanelView(self.bot))

    @app_commands.command(name="ping", description="Check if the ticket bot is online.")
    async def ticket_ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"Pong. Bot latency: {latency_ms} ms", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You do not have permission to use that command."
        elif isinstance(error, app_commands.TransformerError):
            message = (
                "A selected option could not be read correctly.\n\n"
                "For `/ticket setup`:\n"
                "- `category` must be a real Discord category\n"
                "- `log_channel` must be a normal text channel for closed ticket logs/transcripts"
            )
        else:
            raise error

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class TicketBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True

        super().__init__(command_prefix="!", intents=intents)
        self.config_store = GuildConfigStore(CONFIG_PATH)
        self.ticket_ping_cooldowns: dict[int, datetime] = {}

    async def setup_hook(self) -> None:
        self.add_view(TicketPanelView(self))
        self.add_view(TicketChannelView(self))
        await self.add_cog(TicketCommands(self))
        await self.tree.sync()

    async def on_ready(self) -> None:
        if self.user is not None:
            print(f"Logged in as {self.user} (ID: {self.user.id})")
            print("------")


def main() -> None:
    if not TOKEN:
        raise RuntimeError("Missing required configuration value: DISCORD_TOKEN")

    bot = TicketBot()
    asyncio.run(bot.start(TOKEN))


if __name__ == "__main__":
    main()
