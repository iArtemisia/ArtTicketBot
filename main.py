
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
TICKET_STATS_PATH = Path(os.getenv("TICKET_STATS_PATH", str(CONFIG_PATH.parent / "ticket_stats.json")))
TICKET_PING_COOLDOWN_SECONDS = 10 * 60

TICKET_CONFIG: dict[str, str] = {
    "label": "Open Ticket",
    "emoji": "🎫",
    "description": "Press the button below to open a private ticket.",
    "channel_prefix": "ticket",
}

PRIORITY_TICKET_CONFIG: dict[str, str] = {
    "label": "Open Priority Ticket",
    "emoji": "🚨",
    "description": "Open a priority ticket if you have the required role.",
    "channel_prefix": "priority",
}

ROLE_TARGETS: dict[str, dict[str, str]] = {
    "staff_pool": {
        "config_key": "staff_pool_role_ids",
        "title": "Staff Role Filter",
        "short": "staff filter",
        "current_label": "Roles shown in ticket staff dropdowns",
        "description": "roles considered staff candidates in ticket admin dropdowns",
    },
    "normal_staff": {
        "config_key": "staff_role_ids",
        "title": "Normal Ticket Access",
        "short": "normal access",
        "current_label": "Normal ticket access roles",
        "description": "roles that can see normal tickets",
    },
    "normal_ping": {
        "config_key": "ping_role_ids",
        "title": "Normal Ticket Ping",
        "short": "normal ping",
        "current_label": "Normal ticket ping roles",
        "description": "roles pinged when a normal ticket opens",
    },
    "priority_staff": {
        "config_key": "priority_staff_role_ids",
        "title": "Priority Ticket Access",
        "short": "priority access",
        "current_label": "Priority ticket access roles",
        "description": "roles that can see priority tickets",
    },
    "priority_ping": {
        "config_key": "priority_ping_role_ids",
        "title": "Priority Ticket Ping",
        "short": "priority ping",
        "current_label": "Priority ticket ping roles",
        "description": "roles pinged when a priority ticket opens",
    },
    "priority_allowed": {
        "config_key": "priority_allowed_role_ids",
        "title": "Priority Ticket Open Permission",
        "short": "priority opener",
        "current_label": "Roles allowed to open priority tickets",
        "description": "roles allowed to open priority tickets",
    },
}

ROLE_TARGET_ALIASES: dict[str, str] = {
    "staff": "normal_staff",
    "ping": "normal_ping",
    "staff_filter": "staff_pool",
    "staff_pool": "staff_pool",
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

    def format_ticket_number(self, value: int) -> str:
        """Format ticket numbers as 000, 001, 002, ... 010, ... 1000."""
        return f"{max(0, int(value)):03d}"

    def peek_next_ticket_number(self, guild_id: int) -> str:
        config = self.get_guild(guild_id)
        try:
            next_value = int(config.get("next_ticket_number", 0) or 0)
        except (TypeError, ValueError):
            next_value = 0
        return self.format_ticket_number(next_value)

    def allocate_ticket_number(self, guild_id: int) -> str:
        """Return the next sequential ticket number and save the next counter value."""
        config = self.get_guild(guild_id)
        try:
            next_value = int(config.get("next_ticket_number", 0) or 0)
        except (TypeError, ValueError):
            next_value = 0

        ticket_number = self.format_ticket_number(next_value)
        config["next_ticket_number"] = next_value + 1
        self.set_guild(guild_id, config)
        return ticket_number

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


class TicketStatsStore:
    """Persistent per-guild ticket/staff statistics."""

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

    def _get_guild(self, data: dict[str, Any], guild_id: int) -> dict[str, Any]:
        guild_key = str(guild_id)
        guild_data = data.setdefault(guild_key, {})
        guild_data.setdefault("tickets", {})
        return guild_data

    def get_guild(self, guild_id: int) -> dict[str, Any]:
        data = self._read_all()
        return data.get(str(guild_id), {"tickets": {}})

    def get_ticket(self, guild_id: int, channel_id: int) -> dict[str, Any]:
        guild_data = self.get_guild(guild_id)
        return guild_data.get("tickets", {}).get(str(channel_id), {})

    def ensure_ticket(
        self,
        guild_id: int,
        channel_id: int,
        *,
        ticket_number: str,
        ticket_type: str,
        opened_by: Optional[int] = None,
        opened_at: Optional[str] = None,
    ) -> dict[str, Any]:
        data = self._read_all()
        guild_data = self._get_guild(data, guild_id)
        tickets = guild_data.setdefault("tickets", {})
        ticket_key = str(channel_id)
        ticket = tickets.setdefault(ticket_key, {})

        ticket.setdefault("ticket_number", str(ticket_number))
        ticket.setdefault("ticket_channel_id", int(channel_id))
        ticket.setdefault("ticket_type", ticket_type or "normal")
        ticket.setdefault("opened_by", int(opened_by) if opened_by else 0)
        ticket.setdefault("opened_at", opened_at or now_utc().isoformat())
        ticket.setdefault("claimed_by", 0)
        ticket.setdefault("claimed_at", "")
        ticket.setdefault("closed_by", 0)
        ticket.setdefault("closed_at", "")
        ticket.setdefault("close_reason", "")
        ticket.setdefault("claim_events", [])
        ticket.setdefault("unclaim_events", [])
        ticket.setdefault("staff_messages", {})
        ticket.setdefault("staff_message_last_at", {})

        # Keep these values fresh if the channel was renamed/re-numbered after create.
        ticket["ticket_number"] = str(ticket_number or ticket.get("ticket_number", ""))
        ticket["ticket_channel_id"] = int(channel_id)
        ticket["ticket_type"] = ticket_type or ticket.get("ticket_type", "normal")
        if opened_by:
            ticket["opened_by"] = int(opened_by)
        if opened_at:
            ticket["opened_at"] = opened_at

        self._write_all(data)
        return ticket

    def ensure_ticket_from_channel(self, channel: discord.TextChannel) -> dict[str, Any]:
        owner_id = get_ticket_owner_id(channel) or 0
        opened_at = channel.created_at.isoformat() if channel.created_at else now_utc().isoformat()
        return self.ensure_ticket(
            channel.guild.id,
            channel.id,
            ticket_number=get_ticket_number(channel),
            ticket_type=get_ticket_kind(channel),
            opened_by=owner_id,
            opened_at=opened_at,
        )

    def record_open(self, guild_id: int, channel_id: int, *, ticket_number: str, ticket_type: str, opened_by: int) -> None:
        self.ensure_ticket(
            guild_id,
            channel_id,
            ticket_number=ticket_number,
            ticket_type=ticket_type,
            opened_by=opened_by,
            opened_at=now_utc().isoformat(),
        )

    def _mutate_ticket(self, guild_id: int, channel_id: int, callback) -> dict[str, Any]:
        data = self._read_all()
        guild_data = self._get_guild(data, guild_id)
        tickets = guild_data.setdefault("tickets", {})
        ticket = tickets.setdefault(str(channel_id), {})
        ticket.setdefault("ticket_channel_id", int(channel_id))
        ticket.setdefault("claim_events", [])
        ticket.setdefault("unclaim_events", [])
        ticket.setdefault("staff_messages", {})
        ticket.setdefault("staff_message_last_at", {})
        callback(ticket)
        self._write_all(data)
        return ticket

    def record_claim(self, guild_id: int, channel_id: int, staff_id: int) -> None:
        now_text = now_utc().isoformat()

        def apply(ticket: dict[str, Any]) -> None:
            ticket["claimed_by"] = int(staff_id)
            ticket["claimed_at"] = now_text
            events = ticket.setdefault("claim_events", [])
            events.append({"staff_id": int(staff_id), "at": now_text})

        self._mutate_ticket(guild_id, channel_id, apply)

    def record_unclaim(self, guild_id: int, channel_id: int, staff_id: int) -> None:
        now_text = now_utc().isoformat()

        def apply(ticket: dict[str, Any]) -> None:
            ticket["claimed_by"] = 0
            ticket["claimed_at"] = ""
            events = ticket.setdefault("unclaim_events", [])
            events.append({"staff_id": int(staff_id), "at": now_text})

        self._mutate_ticket(guild_id, channel_id, apply)

    def record_staff_message(self, guild_id: int, channel_id: int, staff_id: int, created_at: Optional[datetime] = None) -> None:
        now_text = (created_at or now_utc()).isoformat()

        def apply(ticket: dict[str, Any]) -> None:
            messages = ticket.setdefault("staff_messages", {})
            key = str(staff_id)
            messages[key] = int(messages.get(key, 0)) + 1
            last = ticket.setdefault("staff_message_last_at", {})
            last[key] = now_text

        self._mutate_ticket(guild_id, channel_id, apply)

    def record_close(self, guild_id: int, channel_id: int, staff_id: int, reason: str) -> None:
        now_text = now_utc().isoformat()

        def apply(ticket: dict[str, Any]) -> None:
            ticket["closed_by"] = int(staff_id)
            ticket["closed_at"] = now_text
            ticket["close_reason"] = reason or "No reason provided."

        self._mutate_ticket(guild_id, channel_id, apply)

    def member_summary(self, guild_id: int, user_id: int) -> dict[str, Any]:
        guild_data = self.get_guild(guild_id)
        tickets = guild_data.get("tickets", {})
        claimed_ticket_ids: set[str] = set()
        closed_ticket_ids: set[str] = set()
        typed_ticket_ids: set[str] = set()
        total_messages = 0
        recent: list[dict[str, Any]] = []
        user_key = str(user_id)

        for ticket_id, ticket in tickets.items():
            claim_events = ticket.get("claim_events", []) if isinstance(ticket.get("claim_events"), list) else []
            if str(ticket.get("claimed_by", "0")) == user_key or any(str(event.get("staff_id", "")) == user_key for event in claim_events):
                claimed_ticket_ids.add(str(ticket_id))
                event_times = [str(event.get("at", "")) for event in claim_events if str(event.get("staff_id", "")) == user_key]
                recent.append({"action": "claimed", "ticket": ticket, "at": max(event_times) if event_times else str(ticket.get("claimed_at", ""))})

            if str(ticket.get("closed_by", "0")) == user_key:
                closed_ticket_ids.add(str(ticket_id))
                recent.append({"action": "closed", "ticket": ticket, "at": str(ticket.get("closed_at", ""))})

            messages = ticket.get("staff_messages", {}) if isinstance(ticket.get("staff_messages"), dict) else {}
            count = int(messages.get(user_key, 0) or 0)
            if count > 0:
                typed_ticket_ids.add(str(ticket_id))
                total_messages += count
                last = ticket.get("staff_message_last_at", {}) if isinstance(ticket.get("staff_message_last_at"), dict) else {}
                recent.append({"action": f"typed {count} message(s)", "ticket": ticket, "at": str(last.get(user_key, ""))})

        recent = [item for item in recent if item.get("at")]
        recent.sort(key=lambda item: str(item.get("at", "")), reverse=True)
        return {
            "claimed": len(claimed_ticket_ids),
            "closed": len(closed_ticket_ids),
            "typed": len(typed_ticket_ids),
            "messages": total_messages,
            "recent": recent[:10],
        }

    def leaderboard(self, guild_id: int) -> dict[str, dict[int, int]]:
        guild_data = self.get_guild(guild_id)
        tickets = guild_data.get("tickets", {})
        claimed: dict[int, set[str]] = {}
        closed: dict[int, int] = {}
        messages: dict[int, int] = {}

        for ticket_id, ticket in tickets.items():
            claim_events = ticket.get("claim_events", []) if isinstance(ticket.get("claim_events"), list) else []
            for event in claim_events:
                try:
                    staff_id = int(event.get("staff_id", 0))
                except (TypeError, ValueError):
                    staff_id = 0
                if staff_id:
                    claimed.setdefault(staff_id, set()).add(str(ticket_id))

            try:
                closed_by = int(ticket.get("closed_by", 0) or 0)
            except (TypeError, ValueError):
                closed_by = 0
            if closed_by:
                closed[closed_by] = closed.get(closed_by, 0) + 1

            staff_messages = ticket.get("staff_messages", {}) if isinstance(ticket.get("staff_messages"), dict) else {}
            for raw_id, count in staff_messages.items():
                try:
                    staff_id = int(raw_id)
                    amount = int(count)
                except (TypeError, ValueError):
                    continue
                messages[staff_id] = messages.get(staff_id, 0) + amount

        return {
            "claimed": {staff_id: len(ticket_ids) for staff_id, ticket_ids in claimed.items()},
            "closed": closed,
            "messages": messages,
        }


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
    ticket_number: str = "",
) -> str:
    ping_part = ",".join(str(role_id) for role_id in (ping_role_ids or []))
    clean_ticket_number = re.sub(r"[^0-9]", "", str(ticket_number or ""))
    clean_ticket_type = ticket_type or "normal"
    return (
        f"ticket_owner:{owner_id}|"
        f"ticket_type:{clean_ticket_type}|"
        f"ticket_number:{clean_ticket_number}|"
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


def get_ticket_number(channel: discord.TextChannel) -> str:
    data = parse_ticket_topic(channel.topic)
    value = data.get("ticket_number", "")
    if value and value.isdigit():
        return value
    return str(channel.id)[-4:]


def get_ticket_kind(channel: discord.TextChannel) -> str:
    data = parse_ticket_topic(channel.topic)
    value = data.get("ticket_type", "normal").lower().strip()
    return "priority" if value == "priority" else "normal"


def get_claimed_by_id(channel: discord.TextChannel) -> Optional[int]:
    data = parse_ticket_topic(channel.topic)
    value = data.get("claimed_by", "0")
    return int(value) if value and value.isdigit() and int(value) > 0 else None


def ticket_base_channel_name(channel: discord.TextChannel) -> str:
    ticket_number = get_ticket_number(channel)
    ticket_kind = get_ticket_kind(channel)
    if ticket_kind == "priority":
        return f"priority-{ticket_number}"
    return f"ticket-{ticket_number}"


def claimed_channel_name(member: discord.Member, channel: discord.TextChannel) -> str:
    safe_name = slugify(member.display_name)[:24]
    ticket_number = get_ticket_number(channel)
    if get_ticket_kind(channel) == "priority":
        return f"priority-{safe_name}-{ticket_number}"[:95]
    return f"{safe_name}-{ticket_number}"[:95]


def format_user_reference(guild: discord.Guild, user_id: Optional[int]) -> str:
    if not user_id:
        return "None"
    member = guild.get_member(user_id)
    if member is not None:
        return f"{member.mention} (`{member.id}`)"
    return f"<@{user_id}> (`{user_id}`)"


def format_stat_time(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "None"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return raw


def format_staff_activity_summary(guild: discord.Guild, ticket: dict[str, Any]) -> str:
    messages = ticket.get("staff_messages", {}) if isinstance(ticket.get("staff_messages"), dict) else {}
    if not messages:
        return "None"

    lines: list[str] = []
    for raw_id, count in sorted(messages.items(), key=lambda item: int(item[1] or 0), reverse=True):
        try:
            staff_id = int(raw_id)
            amount = int(count)
        except (TypeError, ValueError):
            continue
        member = guild.get_member(staff_id)
        name = member.display_name if member is not None else f"User {staff_id}"
        lines.append(f"- {name} ({staff_id}): {amount} message(s)")
    return "\n".join(lines) if lines else "None"


def build_ticket_audit_text(bot: "TicketBot", guild: discord.Guild, channel: discord.TextChannel) -> str:
    ticket = bot.stats_store.get_ticket(guild.id, channel.id)
    if not ticket:
        bot.stats_store.ensure_ticket_from_channel(channel)
        ticket = bot.stats_store.get_ticket(guild.id, channel.id)

    lines = [
        "================ TICKET AUDIT ================",
        f"Ticket Number: {ticket.get('ticket_number') or get_ticket_number(channel)}",
        f"Ticket Channel ID: {ticket.get('ticket_channel_id') or channel.id}",
        f"Ticket Type: {str(ticket.get('ticket_type') or get_ticket_kind(channel)).title()}",
        f"Opened By: {format_user_reference(guild, int(ticket.get('opened_by') or 0))}",
        f"Claimed By: {format_user_reference(guild, int(ticket.get('claimed_by') or 0))}",
        f"Closed By: {format_user_reference(guild, int(ticket.get('closed_by') or 0))}",
        f"Opened At: {format_stat_time(ticket.get('opened_at'))}",
        f"Claimed At: {format_stat_time(ticket.get('claimed_at'))}",
        f"Closed At: {format_stat_time(ticket.get('closed_at'))}",
        f"Close Reason: {ticket.get('close_reason') or 'None'}",
        "",
        "Staff Who Typed In Ticket:",
        format_staff_activity_summary(guild, ticket),
        "================================================",
    ]
    return "\n".join(lines)


def build_staff_activity_embed_value(guild: discord.Guild, ticket: dict[str, Any]) -> str:
    value = format_staff_activity_summary(guild, ticket)
    return truncate(value, 1024)


def format_leaderboard_section(guild: discord.Guild, data: dict[int, int]) -> str:
    if not data:
        return "None"
    lines: list[str] = []
    for index, (user_id, amount) in enumerate(sorted(data.items(), key=lambda item: item[1], reverse=True)[:10], start=1):
        lines.append(f"`#{index}` {format_user_reference(guild, user_id)} — **{amount}**")
    return "\n".join(lines)


async def safe_edit_channel_name(channel: discord.TextChannel, name: str, *, reason: str) -> None:
    if channel.name == name:
        return
    try:
        await channel.edit(name=name, reason=reason)
    except discord.HTTPException:
        pass


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


def normalize_role_target(target: str) -> str:
    return ROLE_TARGET_ALIASES.get(target, target)


def role_target_config(target: str) -> dict[str, str]:
    normalized = normalize_role_target(target)
    return ROLE_TARGETS.get(normalized, ROLE_TARGETS["normal_staff"])


def get_role_ids_from_config(bot: "TicketBot", guild_id: int, target: str) -> list[int]:
    config = bot.config_store.get_guild(guild_id)
    key = role_target_config(target)["config_key"]
    result: list[int] = []
    for value in config.get(key, []):
        if str(value).isdigit():
            role_id = int(value)
            if role_id not in result:
                result.append(role_id)
    return result


def set_role_ids_in_config(bot: "TicketBot", guild_id: int, target: str, role_ids: list[int]) -> dict[str, Any]:
    config = bot.config_store.get_guild(guild_id)
    key = role_target_config(target)["config_key"]
    clean_role_ids: list[int] = []
    for role_id in role_ids:
        if int(role_id) not in clean_role_ids:
            clean_role_ids.append(int(role_id))
    config[key] = clean_role_ids
    bot.config_store.set_guild(guild_id, config)
    return config


def add_role_id_to_config(bot: "TicketBot", guild_id: int, target: str, role_id: int) -> dict[str, Any]:
    role_ids = get_role_ids_from_config(bot, guild_id, target)
    if role_id not in role_ids:
        role_ids.append(role_id)
    return set_role_ids_in_config(bot, guild_id, target, role_ids)


def remove_role_id_from_config(bot: "TicketBot", guild_id: int, target: str, role_id: int) -> dict[str, Any]:
    role_ids = [existing for existing in get_role_ids_from_config(bot, guild_id, target) if existing != role_id]
    return set_role_ids_in_config(bot, guild_id, target, role_ids)


def get_roles_from_config(bot: "TicketBot", guild: discord.Guild, target: str) -> list[discord.Role]:
    roles: list[discord.Role] = []
    for role_id in get_role_ids_from_config(bot, guild.id, target):
        role = guild.get_role(role_id)
        if role is not None and not role.is_default():
            roles.append(role)
    return roles

def get_staff_filter_roles(bot: "TicketBot", guild: discord.Guild) -> list[discord.Role]:
    """Roles allowed to appear in ticket staff/admin role dropdowns.

    If this list is empty, the admin dropdowns fall back to all non-managed
    server roles so a brand-new server can still configure itself.

    This filter is only used for staff-facing ticket roles:
    normal access, normal ping, priority access, and priority ping.

    Priority opener is intentionally NOT filtered because it is meant for
    non-staff roles too, such as VIP, Donator, Premium, Supporter, etc.
    """
    return get_roles_from_config(bot, guild, "staff_pool")


def get_staff_filter_role_ids(bot: "TicketBot", guild_id: int) -> set[int]:
    return set(get_role_ids_from_config(bot, guild_id, "staff_pool"))



def get_guild_ping_roles(bot: "TicketBot", guild: discord.Guild) -> list[discord.Role]:
    return get_roles_from_config(bot, guild, "normal_ping")


def get_priority_allowed_roles(bot: "TicketBot", guild: discord.Guild) -> list[discord.Role]:
    return get_roles_from_config(bot, guild, "priority_allowed")


def get_priority_staff_roles(bot: "TicketBot", guild: discord.Guild) -> list[discord.Role]:
    roles = get_roles_from_config(bot, guild, "priority_staff")
    return roles if roles else get_staff_roles(bot, guild)


def get_priority_ping_roles(bot: "TicketBot", guild: discord.Guild) -> list[discord.Role]:
    roles = get_roles_from_config(bot, guild, "priority_ping")
    if roles:
        return roles
    priority_staff = get_priority_staff_roles(bot, guild)
    return priority_staff if priority_staff else get_guild_ping_roles(bot, guild)


def member_can_open_priority(bot: "TicketBot", member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
        return True
    allowed_role_ids = set(get_role_ids_from_config(bot, member.guild.id, "priority_allowed"))
    member_role_ids = {role.id for role in member.roles}
    return bool(allowed_role_ids & member_role_ids)


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
    return get_role_ids_from_config(bot, guild_id, target)


def get_config_roles(bot: "TicketBot", guild: discord.Guild, target: str) -> list[discord.Role]:
    return get_roles_from_config(bot, guild, target)


def normalize_role_search_query(search_query: str) -> str:
    return str(search_query or "").strip()[:100]


def role_matches_search(role: discord.Role, search_query: str) -> bool:
    query = normalize_role_search_query(search_query).lower()
    if not query:
        return True

    # Allow searching by role name, partial role name, raw role ID, or role mention.
    matching_ids = set(extract_ids(query))
    if role.id in matching_ids:
        return True

    return query in role.name.lower() or query in str(role.id)


def get_selectable_roles(
    bot: "TicketBot",
    guild: discord.Guild,
    target: str,
    action: str,
    search_query: str = "",
) -> list[discord.Role]:
    normalized_target = normalize_role_target(target)
    configured = set(get_config_role_ids(bot, guild.id, normalized_target))
    roles = [role for role in guild.roles if not role.is_default() and not role.managed]
    roles.sort(key=lambda role: role.position, reverse=True)

    # Staff Filter only narrows staff-facing ticket dropdowns.
    # Priority Opener is intentionally NOT filtered to staff roles because
    # it is meant for player/customer roles like VIP, Donator, Premium, etc.
    staff_filtered_targets = {"normal_staff", "normal_ping", "priority_staff", "priority_ping"}

    if action == "add":
        if normalized_target in staff_filtered_targets:
            staff_filter_ids = get_staff_filter_role_ids(bot, guild.id)
            if staff_filter_ids:
                roles = [role for role in roles if role.id in staff_filter_ids]
        roles = [role for role in roles if role.id not in configured]
    else:
        # Removal dropdowns always show currently configured roles so cleanup is possible.
        roles = [role for role in roles if role.id in configured]

    clean_query = normalize_role_search_query(search_query)
    if clean_query:
        roles = [role for role in roles if role_matches_search(role, clean_query)]

    return roles[:25]

def format_role_list(roles: list[discord.Role]) -> str:
    return "\n".join(role.mention for role in roles) if roles else "None"


def build_role_config_embed(
    bot: "TicketBot",
    guild: discord.Guild,
    target: str,
    action: str,
    search_query: str = "",
) -> discord.Embed:
    normalized_target = normalize_role_target(target)
    target_info = role_target_config(normalized_target)
    action_label = "Add" if action == "add" else "Remove"
    configured_roles = get_config_roles(bot, guild, normalized_target)
    clean_search_query = normalize_role_search_query(search_query)
    selectable_roles = get_selectable_roles(bot, guild, normalized_target, action, clean_search_query)
    staff_filter_roles = get_staff_filter_roles(bot, guild)

    if normalized_target == "staff_pool":
        description = (
            "Pick which Discord roles should appear in staff/admin ticket dropdowns.\
\
"
            "Once this list is configured, normal/priority **access** and **ping** dropdowns only show these staff-filter roles. "
            "Priority Opener is not staff-filtered because it is meant for non-staff roles too."
        )
    elif normalized_target == "priority_allowed":
        description = (
            "Pick which Discord roles are allowed to open priority tickets.\
\
"
            "This dropdown intentionally shows non-staff roles too, such as VIP, Donator, Premium, Supporter, customer/player roles, etc."
        )
    else:
        description = (
            "Pick a role from the dropdown below.\
\
"
            "Already configured roles are shown here so you do not need to paste role IDs."
        )

    embed = discord.Embed(
        title=f"{action_label} {target_info['title']}",
        description=description,
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name=target_info["current_label"], value=format_role_list(configured_roles), inline=False)
    if clean_search_query:
        embed.add_field(
            name="Current search",
            value=f"`{truncate(clean_search_query, 90)}`",
            inline=False,
        )

    if normalized_target not in {"staff_pool", "priority_allowed"}:
        staff_filter_text = (
            format_role_list(staff_filter_roles)
            if staff_filter_roles
            else "Not configured yet. Until you add staff-filter roles, this dropdown falls back to all server roles."
        )
        embed.add_field(name="Staff role filter", value=staff_filter_text, inline=False)
    elif normalized_target == "priority_allowed":
        embed.add_field(
            name="Staff role filter",
            value="Not applied. Priority opener roles can be non-staff roles.",
            inline=False,
        )

    helper = (
        f"Dropdown shows roles that are not already configured as {target_info['description']}."
        if action == "add"
        else f"Dropdown shows roles that are currently configured as {target_info['description']}."
    )
    if normalized_target in {"normal_staff", "normal_ping", "priority_staff", "priority_ping"} and action == "add" and staff_filter_roles:
        helper += "\
This list is filtered to your configured staff-filter roles."
    elif normalized_target == "priority_allowed" and action == "add":
        helper += "\
Priority Opener is intentionally not filtered to staff roles."
    elif normalized_target == "staff_pool":
        helper += "\
This dropdown intentionally shows all non-managed server roles so you can build the staff filter."

    if clean_search_query:
        helper += f"\nSearch active: only roles matching `{truncate(clean_search_query, 60)}` are shown."

    embed.add_field(name="Dropdown status", value=f"{helper}\
Available choices shown: {len(selectable_roles)} / 25 max.", inline=False)
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


def tag_choices(bot: "TicketBot", guild_id: int, current: str = "") -> list[app_commands.Choice[str]]:
    """Return autocomplete choices for saved tag names."""
    query = clean_tag_name(current)
    names = sorted(get_tags(bot, guild_id).keys())
    if query:
        names = [name for name in names if query in name]
    return [app_commands.Choice(name=name, value=name) for name in names[:25]]


async def saved_tag_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild is None or not isinstance(interaction.client, TicketBot):
        return []
    return tag_choices(interaction.client, interaction.guild.id, current)


def format_tag_list_for_embed(tags: dict[str, str]) -> str:
    if not tags:
        return "No tags are saved yet."
    lines = [f"`{name}` — {truncate(value, 140)}" for name, value in sorted(tags.items())]
    return truncate("\n".join(lines), 4000)


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

    normal_staff_roles = get_staff_roles(bot, guild)
    normal_ping_roles = get_roles_from_config(bot, guild, "normal_ping")
    priority_staff_roles = get_roles_from_config(bot, guild, "priority_staff")
    priority_ping_roles = get_roles_from_config(bot, guild, "priority_ping")
    priority_allowed_roles = get_roles_from_config(bot, guild, "priority_allowed")
    staff_filter_roles = get_staff_filter_roles(bot, guild)

    embed = discord.Embed(
        title="Ticket Admin Panel",
        description=(
            "Use the buttons below to configure normal tickets, priority tickets, pings, and access roles with dropdowns. "
            "No Discord role IDs are needed."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Ticket category", value=category.mention if category else "Not set", inline=False)
    embed.add_field(name="Closed-ticket log channel", value=log_channel.mention if log_channel else "Not set", inline=False)
    embed.add_field(name="Next ticket number", value=f"`{bot.config_store.peek_next_ticket_number(guild.id)}`", inline=False)
    embed.add_field(
        name="Staff role dropdown filter",
        value=format_role_list(staff_filter_roles) if staff_filter_roles else "Not set — staff-facing dropdowns currently show all non-managed server roles.",
        inline=False,
    )
    embed.add_field(name="Normal ticket access roles", value=format_role_list(normal_staff_roles), inline=False)
    embed.add_field(name="Normal ticket ping roles", value=format_role_list(normal_ping_roles), inline=False)
    embed.add_field(name="Priority ticket access roles", value=format_role_list(priority_staff_roles), inline=False)
    embed.add_field(name="Priority ticket ping roles", value=format_role_list(priority_ping_roles), inline=False)
    embed.add_field(name="Priority opener roles (non-staff allowed)", value=format_role_list(priority_allowed_roles), inline=False)

    if isinstance(channel, discord.TextChannel) and is_ticket_channel(channel):
        owner_id = get_ticket_owner_id(channel)
        ticket_ping_roles = get_ticket_ping_roles(bot, guild, channel)
        extra_roles = get_ticket_extra_roles(bot, guild, channel)
        extra_users = get_ticket_extra_users(bot, guild, channel)
        claimed_by = get_claimed_by_id(channel)

        embed.add_field(name="Current ticket", value=channel.mention, inline=False)
        embed.add_field(name="Ticket number", value=f"`{get_ticket_number(channel)}`", inline=True)
        embed.add_field(name="Ticket type", value=get_ticket_kind(channel).title(), inline=True)
        embed.add_field(name="Ticket owner", value=format_user_reference(guild, owner_id), inline=False)
        embed.add_field(name="Claimed by", value=format_user_reference(guild, claimed_by), inline=False)
        embed.add_field(name="Ticket ping roles", value=format_role_list(ticket_ping_roles), inline=False)
        embed.add_field(name="Extra allowed roles", value=format_role_list(extra_roles), inline=False)
        embed.add_field(
            name="Extra allowed users",
            value="\n".join(user.mention for user in extra_users) if extra_users else "None",
            inline=False,
        )
        embed.add_field(
            name="Ticket-specific tools",
            value="Ticket-specific role/ping overrides are removed. Use normal/priority default settings above.",
            inline=False,
        )
    else:
        embed.add_field(
            name="Ticket-specific tools",
            value="Run `/ticket admin` inside a ticket channel to edit that ticket's role permissions and ping roles.",
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
    ticket_number: Optional[str] = None,
    ticket_type: Optional[str] = None,
) -> None:
    data = parse_ticket_topic(channel.topic)
    owner_id = int(data.get("ticket_owner", "0") or 0)
    current_ticket_type = ticket_type or data.get("ticket_type", "normal")
    current_ticket_number = ticket_number if ticket_number is not None else data.get("ticket_number", get_ticket_number(channel))
    current_status = status or data.get("status", "open")
    current_claimed_by = claimed_by if claimed_by is not None else int(data.get("claimed_by", "0") or 0)
    current_ping_role_ids = ping_role_ids if ping_role_ids is not None else get_ticket_ping_role_ids(channel)

    await channel.edit(
        topic=build_ticket_topic(
            owner_id=owner_id,
            ticket_type=current_ticket_type,
            ticket_number=current_ticket_number,
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
    notes_thread: Optional[discord.abc.GuildChannel] = None,
    audit_text: str = "",
) -> str:
    lines: list[str] = []
    lines.append(f"Transcript for #{channel.name}")
    lines.append(f"Ticket ID: {get_ticket_log_id(channel)}")
    lines.append(f"Channel ID: {channel.id}")
    lines.append(f"Generated: {now_utc().isoformat()}")
    lines.append("=" * 70)
    lines.append("")

    if audit_text:
        lines.append(audit_text)
        lines.append("")
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
) -> Optional[discord.abc.GuildChannel]:
    notes_id = get_notes_thread_id(bot, guild.id, channel.id)
    if not notes_id:
        return None

    thread = guild.get_thread(notes_id)
    if thread is not None:
        return thread

    found = guild.get_channel(notes_id)
    if found is not None:
        return found

    try:
        fetched = await bot.fetch_channel(notes_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None

    if isinstance(fetched, (discord.Thread, discord.TextChannel)):
        return fetched
    return None


async def ensure_notes_participant(notes_channel, member: discord.Member) -> None:
    """If staff notes are a private thread, add the staff member to it."""
    if isinstance(notes_channel, discord.Thread):
        try:
            await notes_channel.add_user(member)
        except (discord.Forbidden, discord.HTTPException):
            pass


async def create_staff_notes_channel_fallback(
    bot: "TicketBot",
    guild: discord.Guild,
    ticket_channel: discord.TextChannel,
    creator: discord.Member,
) -> tuple[Optional[discord.TextChannel], str]:
    """Create a hidden staff-only notes text channel if private threads are unavailable."""
    owner_id = get_ticket_owner_id(ticket_channel)
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }

    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
            embed_links=True,
            attach_files=True,
        )

    staff_roles: list[discord.Role] = []
    for role in get_staff_roles(bot, guild) + get_priority_staff_roles(bot, guild):
        if role not in staff_roles and not role.is_default():
            staff_roles.append(role)

    for role in staff_roles:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            embed_links=True,
            attach_files=True,
        )

    overwrites[creator] = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        embed_links=True,
        attach_files=True,
    )

    category = ticket_channel.category
    ticket_number = get_ticket_number(ticket_channel)
    name = f"notes-{ticket_number}-{ticket_channel.name}"[:95]
    topic = f"staff_notes_for:{ticket_channel.id}|ticket_owner:{owner_id or 0}"

    try:
        notes_channel = await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            topic=topic,
            reason=f"Staff notes fallback created by {creator} ({creator.id})",
        )
    except discord.Forbidden:
        return None, (
            "I could not create staff notes. I tried a private thread and a staff-only notes channel.\n\n"
            "Give the bot **Create Private Threads**, **Send Messages in Threads**, **Manage Threads**, "
            "and **Manage Channels** in the ticket category."
        )
    except discord.HTTPException as exc:
        return None, f"Discord refused both staff-notes methods: `{exc}`"

    set_notes_thread_id(bot, guild.id, ticket_channel.id, notes_channel.id)
    await notes_channel.send(
        "📝 **Staff Notes**\n"
        f"Linked ticket: {ticket_channel.mention}\n"
        "This is a staff-only fallback notes channel. These messages are appended to the ticket transcript when the ticket closes."
    )
    return notes_channel, "created_channel"


async def create_or_get_notes_thread(
    bot: "TicketBot",
    guild: discord.Guild,
    channel: discord.TextChannel,
    creator: discord.Member,
) -> tuple[Optional[discord.abc.GuildChannel], str]:
    existing = await get_notes_thread(bot, guild, channel)
    if existing is not None:
        await ensure_notes_participant(existing, creator)
        return existing, "existing"

    private_thread_error = ""
    try:
        thread = await channel.create_thread(
            name=f"staff-notes-{channel.name}"[:100],
            type=discord.ChannelType.private_thread,
            invitable=False,
            reason=f"Staff notes created by {creator} ({creator.id})",
        )
        await ensure_notes_participant(thread, creator)
        set_notes_thread_id(bot, guild.id, channel.id, thread.id)
        await thread.send(
            "📝 **Staff Notes**\n"
            "Use this private thread for internal discussion. These notes are appended to the ticket transcript when the ticket closes."
        )
        return thread, "created"
    except TypeError:
        try:
            thread = await channel.create_thread(
                name=f"staff-notes-{channel.name}"[:100],
                type=discord.ChannelType.private_thread,
                reason=f"Staff notes created by {creator} ({creator.id})",
            )
            await ensure_notes_participant(thread, creator)
            set_notes_thread_id(bot, guild.id, channel.id, thread.id)
            await thread.send(
                "📝 **Staff Notes**\n"
                "Use this private thread for internal discussion. These notes are appended to the ticket transcript when the ticket closes."
            )
            return thread, "created"
        except (discord.Forbidden, discord.HTTPException) as exc:
            private_thread_error = str(exc)
    except (discord.Forbidden, discord.HTTPException) as exc:
        private_thread_error = str(exc)

    notes_channel, status = await create_staff_notes_channel_fallback(bot, guild, channel, creator)
    if notes_channel is None:
        if private_thread_error:
            status += f"\nPrivate thread error: `{truncate(private_thread_error, 500)}`"
        return None, status
    return notes_channel, status

async def create_ticket_channel(
    bot: "TicketBot",
    interaction: discord.Interaction,
    *,
    priority: bool = False,
) -> tuple[Optional[discord.TextChannel], str]:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return None, "This can only be used inside a server."

    if not bot.config_store.is_ready(interaction.guild.id):
        return None, "This server is not configured yet. An admin should run /ticket setup first."

    if priority and not member_can_open_priority(bot, interaction.user):
        allowed_roles = get_priority_allowed_roles(bot, interaction.guild)
        role_list = ", ".join(role.mention for role in allowed_roles) if allowed_roles else "No priority opener roles are configured yet."
        return None, f"You do not have the required role to open a priority ticket.\nRequired role(s): {role_list}"

    category = await get_ticket_category(bot, interaction.guild)
    if category is None:
        return None, "The configured ticket category no longer exists. Run /ticket setup again."

    existing = find_open_ticket_for_user(category, interaction.user.id)
    if existing:
        return existing, f"You already have an open ticket: {existing.mention}"

    guild = interaction.guild
    ticket_type = "priority" if priority else "normal"
    channel_prefix = PRIORITY_TICKET_CONFIG["channel_prefix"] if priority else TICKET_CONFIG["channel_prefix"]
    ticket_number = bot.config_store.allocate_ticket_number(guild.id)
    channel_name = f"{channel_prefix}-{ticket_number}"[:95]

    if priority:
        visible_roles = get_priority_staff_roles(bot, guild)
        ping_roles = get_priority_ping_roles(bot, guild)
    else:
        visible_roles = get_staff_roles(bot, guild)
        ping_roles = get_guild_ping_roles(bot, guild)

    ping_role_ids = [role.id for role in ping_roles]

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
            manage_threads=True,
            embed_links=True,
            attach_files=True,
        )

    for role in visible_roles:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            attach_files=True,
            embed_links=True,
        )

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            topic=build_ticket_topic(
                owner_id=interaction.user.id,
                ticket_type=ticket_type,
                ticket_number=ticket_number,
                ping_role_ids=ping_role_ids,
            ),
            overwrites=overwrites,
            reason=f"{ticket_type.title()} ticket opened by {interaction.user} ({interaction.user.id})",
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

    await update_ticket_metadata(
        channel,
        ticket_number=ticket_number,
        ticket_type=ticket_type,
        ping_role_ids=ping_role_ids,
    )
    await safe_edit_channel_name(
        channel,
        ticket_base_channel_name(channel),
        reason="Set clean ticket channel name after create.",
    )

    config = PRIORITY_TICKET_CONFIG if priority else TICKET_CONFIG
    title_prefix = "Priority Ticket" if priority else config["label"]
    description = (
        "Thanks for opening a **priority** ticket. A higher-level staff member will help you soon.\n\n"
        if priority
        else "Thanks for opening a ticket. A staff member will help you soon.\n\n"
    )
    description += "Use **Ping Team** if you need staff attention."

    embed = discord.Embed(
        title=f"{config['emoji']} {title_prefix}",
        description=description,
        color=discord.Color.red() if priority else discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Ticket Number", value=f"`{ticket_number}`", inline=True)
    embed.add_field(name="Ticket Type", value="Priority" if priority else "Normal", inline=True)
    embed.add_field(name="Created By", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=False)
    embed.add_field(name="Status", value="Open", inline=True)
    embed.add_field(name="Claimed By", value="Not claimed yet", inline=True)
    embed.set_footer(text=f"Creator ID: {interaction.user.id}")

    opening_mentions = mention_roles(ping_roles) if ping_roles else None

    try:
        await channel.send(
            content=opening_mentions,
            embed=embed,
            view=TicketChannelView(bot),
            allowed_mentions=discord.AllowedMentions(roles=True, users=False),
        )
    except discord.Forbidden:
        return None, "I created the channel but couldn't post the starter message. Check the category/channel permissions."
    except discord.HTTPException:
        return None, "I created the channel but Discord rejected the starter message."

    bot.stats_store.record_open(
        guild.id,
        channel.id,
        ticket_number=ticket_number,
        ticket_type=ticket_type,
        opened_by=interaction.user.id,
    )

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
        placeholder="Resolved, player did not answer, duplicate, invalid report, etc.",
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

        claimed_by = get_claimed_by_id(self.channel) or 0
        ping_role_ids = get_ticket_ping_role_ids(self.channel)
        ticket_log_id = get_ticket_log_id(self.channel)
        ticket_number = get_ticket_number(self.channel)
        ticket_kind = get_ticket_kind(self.channel)

        try:
            await update_ticket_metadata(
                self.channel,
                status="closed",
                claimed_by=claimed_by,
                ping_role_ids=ping_role_ids,
                ticket_number=ticket_number,
                ticket_type=ticket_kind,
            )
        except discord.HTTPException:
            pass

        self.bot.stats_store.ensure_ticket_from_channel(self.channel)
        self.bot.stats_store.record_close(interaction.guild.id, self.channel.id, interaction.user.id, reason_text)
        notes_thread = await get_notes_thread(self.bot, interaction.guild, self.channel)
        audit_text = build_ticket_audit_text(self.bot, interaction.guild, self.channel)
        transcript_text = await build_ticket_and_notes_transcript_text(self.channel, notes_thread, audit_text=audit_text)
        try:
            save_ticket_log_text(interaction.guild.id, ticket_log_id, transcript_text)
        except OSError:
            pass

        ticket_stats = self.bot.stats_store.get_ticket(interaction.guild.id, self.channel.id)
        log_channel = await get_log_channel(self.bot, interaction.guild)
        closed_at = now_utc()
        embed = discord.Embed(title="Ticket Closed", color=discord.Color.red(), timestamp=closed_at)
        embed.add_field(name="Ticket Number", value=f"`{ticket_number}`", inline=True)
        embed.add_field(name="Ticket ID / Channel ID", value=f"`{ticket_log_id}`", inline=True)
        embed.add_field(name="Ticket Type", value=ticket_kind.title(), inline=True)
        embed.add_field(name="Created By", value=format_user_reference(interaction.guild, owner_id), inline=False)
        embed.add_field(name="Claimed By", value=format_user_reference(interaction.guild, claimed_by if claimed_by else None), inline=False)
        embed.add_field(name="Closed By", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
        embed.add_field(name="Close Reason", value=truncate(reason_text, 1024), inline=False)
        embed.add_field(name="Closed At", value=closed_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=False)
        embed.add_field(
            name="Staff Notes",
            value=(f"Included from {notes_thread.mention}" if notes_thread is not None else "No staff notes were linked."),
            inline=False,
        )
        embed.add_field(
            name="Staff Activity",
            value=build_staff_activity_embed_value(interaction.guild, ticket_stats),
            inline=False,
        )
        embed.set_footer(text="Transcript file includes ticket conversation, staff notes, and ticket audit stats when available.")

        log_sent = False
        log_error = ""
        if log_channel is None:
            log_error = "No closed-ticket log channel is configured or the configured channel no longer exists."
        else:
            try:
                transcript = transcript_file_from_text(
                    transcript_text,
                    f"ticket-{ticket_number}-{ticket_log_id}-{self.channel.name}-transcript.txt",
                )
                await log_channel.send(embed=embed, file=transcript)
                log_sent = True
            except discord.Forbidden:
                log_error = f"I do not have permission to send embeds/files in {log_channel.mention}."
            except discord.HTTPException as exc:
                log_error = f"Discord rejected the ticket log message: `{truncate(str(exc), 500)}`"

        if not log_sent:
            await interaction.followup.send(
                "I could not send the transcript to the configured ticket log channel, so I am keeping this ticket open to prevent transcript loss.\n\n"
                f"Reason: {log_error}\n\n"
                "Fix the log channel permissions, then press **Close** again.",
                ephemeral=True,
            )
            try:
                await self.channel.send(
                    f"⚠️ Ticket close was stopped because the transcript could not be posted to the log channel.\nReason: {log_error}"
                )
            except discord.HTTPException:
                pass
            return

        try:
            await self.channel.send(f"🔒 Ticket closed by {interaction.user.mention}. Reason: {reason_text}")
        except discord.HTTPException:
            pass

        if isinstance(notes_thread, discord.TextChannel):
            try:
                await notes_thread.delete(reason=f"Ticket {self.channel.id} closed; staff notes transcript saved.")
            except discord.HTTPException:
                pass

        await asyncio.sleep(4)
        try:
            await self.channel.delete(reason=f"Ticket closed by {interaction.user} ({interaction.user.id})")
        except discord.Forbidden:
            await interaction.followup.send(
                "The transcript was posted, but I could not delete the ticket channel. Give me **Manage Channels** in the ticket category.",
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"The transcript was posted, but Discord rejected the channel delete: `{truncate(str(exc), 500)}`",
                ephemeral=True,
            )


class TicketOpenButton(discord.ui.Button):
    def __init__(self, bot: "TicketBot", *, priority: bool = False):
        config = PRIORITY_TICKET_CONFIG if priority else TICKET_CONFIG
        super().__init__(
            label=config["label"],
            emoji=config["emoji"],
            style=discord.ButtonStyle.danger if priority else discord.ButtonStyle.primary,
            custom_id="ticket:open_priority" if priority else "ticket:open",
        )
        self.bot = bot
        self.priority = priority

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        channel, status = await create_ticket_channel(self.bot, interaction, priority=self.priority)
        if channel is None:
            await interaction.followup.send(status, ephemeral=True)
            return

        if status == "created":
            ticket_type_label = "priority ticket" if self.priority else "ticket"
            await interaction.followup.send(f"Your {ticket_type_label} has been created: {channel.mention}", ephemeral=True)
        else:
            await interaction.followup.send(status, ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=None)
        self.add_item(TicketOpenButton(bot, priority=False))
        self.add_item(TicketOpenButton(bot, priority=True))


class TicketChannelView(discord.ui.View):
    """Public ticket controls.

    Buttons are visible to everyone who can see the private ticket channel.
    Permission checks happen inside each button.
    """

    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=None)
        self.bot = bot

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
                "No ticket ping roles are set. Staff can configure ping roles from `/ticket admin`.",
                ephemeral=True,
            )
            return

        self.bot.ticket_ping_cooldowns[channel.id] = now_utc()
        await channel.send(
            f"📣 {interaction.user.mention} requested staff attention: {mention_roles(ping_roles)}",
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        await interaction.response.send_message("Pinged the configured ticket roles. This ticket can ping again in 10 minutes.", ephemeral=True)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, custom_id="ticket:claim", row=0)
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        owner_id = get_ticket_owner_id(channel)
        if owner_id == interaction.user.id:
            await interaction.response.send_message("You cannot claim your own ticket.", ephemeral=True)
            return

        if not member_is_staff(self.bot, interaction.user):
            await interaction.response.send_message("Only ticket staff can claim tickets.", ephemeral=True)
            return

        claimed_by = get_claimed_by_id(channel)
        if claimed_by == interaction.user.id:
            await interaction.response.send_message("You already claimed this ticket.", ephemeral=True)
            return
        if claimed_by is not None:
            await interaction.response.send_message(
                f"This ticket is already claimed by {format_user_reference(interaction.guild, claimed_by)}. It must be unclaimed first.",
                ephemeral=True,
            )
            return

        await update_ticket_metadata(channel, claimed_by=interaction.user.id)
        self.bot.stats_store.ensure_ticket_from_channel(channel)
        self.bot.stats_store.record_claim(interaction.guild.id, channel.id, interaction.user.id)
        await safe_edit_channel_name(
            channel,
            claimed_channel_name(interaction.user, channel),
            reason=f"Ticket claimed by {interaction.user} ({interaction.user.id})",
        )
        await channel.send(f"📌 Ticket claimed by {interaction.user.mention} (`{interaction.user.id}`).")
        await interaction.response.send_message("You claimed this ticket.", ephemeral=True)

    @discord.ui.button(label="Unclaim", style=discord.ButtonStyle.secondary, custom_id="ticket:unclaim", row=0)
    async def unclaim_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        owner_id = get_ticket_owner_id(channel)
        if owner_id == interaction.user.id:
            await interaction.response.send_message("You cannot unclaim your own ticket.", ephemeral=True)
            return

        if not member_is_staff(self.bot, interaction.user):
            await interaction.response.send_message("Only ticket staff can unclaim tickets.", ephemeral=True)
            return

        claimed_by = get_claimed_by_id(channel)
        if claimed_by is None:
            await interaction.response.send_message("This ticket is not currently claimed.", ephemeral=True)
            return

        can_unclaim = claimed_by == interaction.user.id or interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_channels
        if not can_unclaim:
            await interaction.response.send_message(
                f"Only the staff member who claimed this ticket or an admin can unclaim it. Claimed by: {format_user_reference(interaction.guild, claimed_by)}",
                ephemeral=True,
            )
            return

        await update_ticket_metadata(channel, claimed_by=0)
        self.bot.stats_store.ensure_ticket_from_channel(channel)
        self.bot.stats_store.record_unclaim(interaction.guild.id, channel.id, interaction.user.id)
        await safe_edit_channel_name(
            channel,
            ticket_base_channel_name(channel),
            reason=f"Ticket unclaimed by {interaction.user} ({interaction.user.id})",
        )
        await channel.send(f"📌 Ticket unclaimed by {interaction.user.mention} (`{interaction.user.id}`).")
        await interaction.response.send_message("You unclaimed this ticket.", ephemeral=True)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket:close", row=0)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        await interaction.response.send_modal(CloseTicketModal(self.bot, channel))

class ConfigRoleSelect(discord.ui.Select):
    def __init__(self, bot: "TicketBot", guild: discord.Guild, target: str, action: str, search_query: str = ""):
        self.bot = bot
        self.target = normalize_role_target(target)
        self.action = action
        self.guild_id = guild.id
        self.search_query = normalize_role_search_query(search_query)

        roles = get_selectable_roles(bot, guild, self.target, action, self.search_query)
        options: list[discord.SelectOption] = []
        for role in roles:
            label = role.name[:100]
            description = f"ID: {role.id}"
            options.append(discord.SelectOption(label=label, value=str(role.id), description=description))

        if not options:
            options.append(discord.SelectOption(label="No roles available", value="0", description="Nothing to select right now."))

        target_info = role_target_config(self.target)
        action_label = "add" if action == "add" else "remove"
        placeholder = f"Choose a role to {action_label}: {target_info['short']}..."
        if self.search_query:
            placeholder = f"Search `{self.search_query[:35]}` — choose a role..."

        super().__init__(
            placeholder=placeholder[:150],
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

        role_id = int(self.values[0])
        role = interaction.guild.get_role(role_id)
        if role is None or role.is_default():
            await interaction.response.send_message("That role is no longer available.", ephemeral=True)
            return

        target_info = role_target_config(self.target)
        if self.action == "add":
            add_role_id_to_config(self.bot, interaction.guild.id, self.target, role.id)
            result = f"Added {role.mention} to **{target_info['current_label']}**."
        else:
            remove_role_id_from_config(self.bot, interaction.guild.id, self.target, role.id)
            result = f"Removed {role.mention} from **{target_info['current_label']}**."

        embed = build_role_config_embed(self.bot, interaction.guild, self.target, self.action, self.search_query)
        embed.add_field(name="Updated", value=result, inline=False)
        await interaction.response.edit_message(embed=embed, view=RoleConfigSelectView(self.bot, interaction.guild, self.target, self.action, self.search_query))


class RoleSearchModal(discord.ui.Modal, title="Search Roles"):
    search_input = discord.ui.TextInput(
        label="Search role name, mention, or ID",
        placeholder="Example: admin, moderator, VIP, Donator, 1234567890",
        max_length=100,
        required=False,
    )

    def __init__(self, bot: "TicketBot", target: str, action: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.target = normalize_role_target(target)
        self.action = action

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return


        clean_query = normalize_role_search_query(str(self.search_input.value))
        embed = build_role_config_embed(self.bot, interaction.guild, self.target, self.action, clean_query)
        await interaction.response.edit_message(
            embed=embed,
            view=RoleConfigSelectView(self.bot, interaction.guild, self.target, self.action, clean_query),
        )


class RoleConfigSelectView(discord.ui.View):
    def __init__(self, bot: "TicketBot", guild: discord.Guild, target: str, action: str, search_query: str = ""):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild.id
        self.target = normalize_role_target(target)
        self.action = action
        self.search_query = normalize_role_search_query(search_query)
        self.add_item(ConfigRoleSelect(bot, guild, self.target, action, self.search_query))

    @discord.ui.button(label="Search Roles", style=discord.ButtonStyle.secondary, row=1)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        await interaction.response.send_modal(RoleSearchModal(self.bot, self.target, self.action))

    @discord.ui.button(label="Clear Search / Refresh", style=discord.ButtonStyle.primary, row=1)
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

    normalized_target = normalize_role_target(target)
    embed = build_role_config_embed(bot, interaction.guild, normalized_target, action)
    await interaction.response.send_message(embed=embed, view=RoleConfigSelectView(bot, interaction.guild, normalized_target, action), ephemeral=True)


class TicketAdminPanelView(discord.ui.View):
    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=600)
        self.bot = bot

    def _get_ticket_channel(self, interaction: discord.Interaction) -> Optional[discord.TextChannel]:
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel) and is_ticket_channel(channel):
            return channel
        return None

    @discord.ui.button(label="Add Normal Access", style=discord.ButtonStyle.secondary, row=0)
    async def add_normal_access_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "normal_staff", "add")

    @discord.ui.button(label="Remove Normal Access", style=discord.ButtonStyle.secondary, row=0)
    async def remove_normal_access_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "normal_staff", "remove")

    @discord.ui.button(label="Add Normal Ping", style=discord.ButtonStyle.secondary, row=0)
    async def add_normal_ping_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "normal_ping", "add")

    @discord.ui.button(label="Remove Normal Ping", style=discord.ButtonStyle.secondary, row=0)
    async def remove_normal_ping_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "normal_ping", "remove")

    @discord.ui.button(label="Add Priority Access", style=discord.ButtonStyle.secondary, row=1)
    async def add_priority_access_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "priority_staff", "add")

    @discord.ui.button(label="Remove Priority Access", style=discord.ButtonStyle.secondary, row=1)
    async def remove_priority_access_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "priority_staff", "remove")

    @discord.ui.button(label="Add Priority Ping", style=discord.ButtonStyle.secondary, row=1)
    async def add_priority_ping_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "priority_ping", "add")

    @discord.ui.button(label="Remove Priority Ping", style=discord.ButtonStyle.secondary, row=1)
    async def remove_priority_ping_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "priority_ping", "remove")

    @discord.ui.button(label="Add Staff Filter", style=discord.ButtonStyle.secondary, row=2)
    async def add_staff_filter_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "staff_pool", "add")

    @discord.ui.button(label="Remove Staff Filter", style=discord.ButtonStyle.secondary, row=2)
    async def remove_staff_filter_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "staff_pool", "remove")

    @discord.ui.button(label="Add Priority Opener", style=discord.ButtonStyle.secondary, row=3)
    async def add_priority_opener_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "priority_allowed", "add")

    @discord.ui.button(label="Remove Priority Opener", style=discord.ButtonStyle.secondary, row=3)
    async def remove_priority_opener_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await send_role_config_panel(interaction, self.bot, "priority_allowed", "remove")


    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=4)
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
    @app_commands.default_permissions(manage_guild=True)
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
            priority_staff_role_ids=current.get("priority_staff_role_ids", []),
            priority_ping_role_ids=current.get("priority_ping_role_ids", []),
            priority_allowed_role_ids=current.get("priority_allowed_role_ids", []),
            staff_pool_role_ids=current.get("staff_pool_role_ids", []),
            panel_gif_url=current.get("panel_gif_url", ""),
            tags=current.get("tags", {}),
            notes_thread_ids=current.get("notes_thread_ids", {}),
        )

        await interaction.response.send_message(
            (
                "Saved ticket setup for this server.\n\n"
                f"**Ticket category:** {real_category.name}\n"
                "Used for newly created private ticket channels.\n\n"
                f"**Closed-ticket log channel:** {real_log_channel.mention}\n"
                "Used when tickets close. The transcript file will be posted there automatically.\n\n"
                f"**Normal access roles configured:** {len(config.get('staff_role_ids', []))}\n"
                f"**Priority access roles configured:** {len(config.get('priority_staff_role_ids', []))}\n"
                f"**Priority opener roles configured:** {len(config.get('priority_allowed_role_ids', []))}\n\n"
                "Run `/ticket admin` to configure pings/access with dropdowns.\n"
                "Run `/ticket panel` in the channel where you want the ticket embed posted."
            ),
            ephemeral=True,
        )

    @app_commands.command(name="addstaff", description="Add a normal ticket access role.")
    @app_commands.default_permissions(manage_guild=True)
    async def add_staff(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.add_staff_role(interaction.guild.id, role.id)
        await interaction.response.send_message(
            f"Added {role.mention} as a normal ticket access role. Total normal access roles: {len(config.get('staff_role_ids', []))}",
            ephemeral=True,
        )

    @app_commands.command(name="removestaff", description="Remove a normal ticket access role.")
    @app_commands.default_permissions(manage_guild=True)
    async def remove_staff(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.remove_staff_role(interaction.guild.id, role.id)
        await interaction.response.send_message(
            f"Removed {role.mention} from normal ticket access roles. Total normal access roles: {len(config.get('staff_role_ids', []))}",
            ephemeral=True,
        )

    @app_commands.command(name="addpingrole", description="Add a normal ticket ping role.")
    @app_commands.default_permissions(manage_guild=True)
    async def add_ping_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.add_ping_role(interaction.guild.id, role.id)
        await interaction.response.send_message(
            f"Added {role.mention} to the normal ticket ping list. Total normal ping roles: {len(config.get('ping_role_ids', []))}",
            ephemeral=True,
        )

    @app_commands.command(name="removepingrole", description="Remove a normal ticket ping role.")
    @app_commands.default_permissions(manage_guild=True)
    async def remove_ping_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.remove_ping_role(interaction.guild.id, role.id)
        await interaction.response.send_message(
            f"Removed {role.mention} from the normal ticket ping list. Total normal ping roles: {len(config.get('ping_role_ids', []))}",
            ephemeral=True,
        )

    @app_commands.command(name="panelgif", description="Set or clear the GIF/image shown on the ticket panel.")
    @app_commands.default_permissions(manage_guild=True)
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
            priority_staff_role_ids=current.get("priority_staff_role_ids", []),
            priority_ping_role_ids=current.get("priority_ping_role_ids", []),
            priority_allowed_role_ids=current.get("priority_allowed_role_ids", []),
            panel_gif_url=cleaned,
            tags=current.get("tags", {}),
            notes_thread_ids=current.get("notes_thread_ids", {}),
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
    @app_commands.default_permissions(manage_guild=True)
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
        await interaction.response.send_message(f"Saved tag `{clean_name}`. Staff can now use `/tag send name:{clean_name}` inside tickets.", ephemeral=True)

    @app_commands.command(name="removetag", description="Remove a reusable staff tag response.")
    @app_commands.default_permissions(manage_guild=True)
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
    @app_commands.default_permissions(manage_messages=True)
    async def list_tags_command(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        tags = get_tags(self.bot, interaction.guild.id)
        if not tags:
            await interaction.response.send_message("No tags are saved yet. Use `/tag admin` first.", ephemeral=True)
            return

        lines = [f"`{name}` — {truncate(value, 120)}" for name, value in sorted(tags.items())]
        await interaction.response.send_message("Saved tags:\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="tag", description="Send a saved staff tag response inside a ticket.")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.autocomplete(name=saved_tag_autocomplete)
    async def send_tag_command(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("Use `/tag send` inside a ticket channel.", ephemeral=True)
            return

        clean_name = clean_tag_name(name)
        response = get_tags(self.bot, interaction.guild.id).get(clean_name)
        if not response:
            await interaction.response.send_message(f"No tag named `{clean_name}` was found. Use `/tag list` to view saved tags.", ephemeral=True)
            return

        await interaction.response.send_message(response, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="notes", description="Create or open the staff-only notes thread for this ticket.")
    @app_commands.default_permissions(manage_messages=True)
    async def ticket_notes(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("Use `/ticket notes` inside a ticket channel.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        thread, status = await create_or_get_notes_thread(self.bot, interaction.guild, channel, interaction.user)
        if thread is None:
            await interaction.followup.send(status, ephemeral=True)
            return

        msg = "Created" if status == "created" else "Opened existing"
        await interaction.followup.send(f"{msg} staff notes thread: {thread.mention}\nNotes will be appended under a **STAFF NOTES** divider in the ticket transcript.", ephemeral=True)

    @app_commands.command(name="admin", description="Open the ticket admin control panel.")
    @app_commands.default_permissions(manage_guild=True)
    async def ticket_admin(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        embed = build_admin_panel_embed(self.bot, interaction.guild, interaction.channel)
        await interaction.response.send_message(embed=embed, view=TicketAdminPanelView(self.bot), ephemeral=True)

    @app_commands.command(name="config", description="Show the ticket configuration for this server.")
    @app_commands.default_permissions(manage_guild=True)
    async def show_config(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.get_guild(interaction.guild.id)
        category = interaction.guild.get_channel(config.get("ticket_category_id", 0))
        log_channel = interaction.guild.get_channel(config.get("log_channel_id", 0))
        panel_url = get_panel_gif_url(self.bot, interaction.guild.id) or "Not set"

        embed = discord.Embed(title="Ticket Configuration", color=discord.Color.blurple())
        embed.add_field(name="Ticket category", value=category.mention if category else "Not set", inline=False)
        embed.add_field(name="Closed-ticket log channel", value=log_channel.mention if log_channel else "Not set", inline=False)
        embed.add_field(name="Next ticket number", value=f"`{self.bot.config_store.peek_next_ticket_number(interaction.guild.id)}`", inline=False)
        embed.add_field(name="Staff role dropdown filter", value=format_role_list(get_staff_filter_roles(self.bot, interaction.guild)), inline=False)
        embed.add_field(name="Normal access roles", value=format_role_list(get_roles_from_config(self.bot, interaction.guild, "normal_staff")), inline=False)
        embed.add_field(name="Normal ping roles", value=format_role_list(get_roles_from_config(self.bot, interaction.guild, "normal_ping")), inline=False)
        embed.add_field(name="Priority access roles", value=format_role_list(get_roles_from_config(self.bot, interaction.guild, "priority_staff")), inline=False)
        embed.add_field(name="Priority ping roles", value=format_role_list(get_roles_from_config(self.bot, interaction.guild, "priority_ping")), inline=False)
        embed.add_field(name="Priority opener roles", value=format_role_list(get_roles_from_config(self.bot, interaction.guild, "priority_allowed")), inline=False)
        embed.add_field(
            name="Priority opener dropdown",
            value="Can include non-staff roles. This is for roles like VIP, Donator, Premium, Supporter, etc.",
            inline=False,
        )
        embed.add_field(name="Panel GIF/Image", value=truncate(panel_url, 1024), inline=False)
        embed.add_field(
            name="In-ticket controls",
            value="Ticket buttons shown in the private ticket: **Ping Team**, **Claim**, **Unclaim**, and **Close**. Ticket owners cannot claim or unclaim their own tickets. Use `/ticket notes` for staff notes.",
            inline=False,
        )
        embed.add_field(name="Ready", value="Yes" if self.bot.config_store.is_ready(interaction.guild.id) else "No", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="panel", description="Post the ticket panel in the current channel.")
    @app_commands.default_permissions(manage_guild=True)
    async def ticket_panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        if not self.bot.config_store.is_ready(interaction.guild.id):
            await interaction.response.send_message(
                "This server is not configured yet. Run `/ticket setup` and configure normal access roles first.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Open a ticket!",
            description=(
                "**Please ensure that you only make a ticket when it is necessary:**\n\n"
                "For information about the server and wipe schedule, please see 📌 #server-list-wipe\n"
                "For information regarding our rules refer to ‼️ #rules\n\n"
                "For information about claiming kits ♻️ #auto-kit\n"
                "To view our shops: 🤑 #starz-shop\n\n"
                "For information about our offline protection system.\n"
                "🚧 #offline-protection\n\n"
                "**If you make a ticket about team size we require the following**\n\n"
                "A clip of the suspected team being over team limit\n"
                "Suspected team's usernames (requires just 1)\n\n"
                "**Tickets will be auto-closed without these.**\n\n"
                "If you are making a ticket asking to be unlinked we need:\n"
                "a reason as to why you need unlinked (left Discord / changed gamertag)\n"
                "proof that you own the account (receipts / screenshots)\n\n"
                "If you are making a report about over-teaming please provide:\n"
                "a clip of the team that is over team limit\n"
                "that team's gamertags\n"
                "the server this is on\n"
                "the grid they live\n\n"
                "**PRIORITY TICKETS!**\n"
                "Starz Empire Priority Ticket Support\n\n"
                "These tickets are only accessible to our admin management team:\n"
                "HEAD ADMINS\n"
                "ADMIN MANAGEMENT\n\n"
                "Only available to:\n"
                "TOP SUPPORTERS\n"
                "🤑 MEGA SUPPORTER 🤑\n\n"
                "**USES**\n"
                "Moves ticket to top of the queue\n"
                "Professional 1-on-1 customer/player service with our top-ranking admins\n"
                "Bypass the chain of command and speak to the top-ranking admins/owners\n\n"
                "Please understand that wait times may be longer as there are a very limited amount of us. "
                "If you need instant support, consider making a normal ticket as regular admins cannot see these tickets."
            ),
            color=discord.Color.from_rgb(255, 0, 170),
            timestamp=now_utc(),
        )
        embed.set_author(name="STARZ")
        panel_gif_url = get_panel_gif_url(self.bot, interaction.guild.id)
        if panel_gif_url:
            embed.set_image(url=panel_gif_url)
        embed.set_footer(text="One open ticket per user.")

        await interaction.response.send_message("Ticket panel posted.", ephemeral=True)
        await interaction.followup.send(embed=embed, view=TicketPanelView(self.bot))

    @app_commands.command(name="ping", description="Check if the ticket bot is online.")
    @app_commands.default_permissions(send_messages=True)
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


class TagCreateModal(discord.ui.Modal, title="Create Tag"):
    name_input = discord.ui.TextInput(
        label="Tag name",
        placeholder="Example: rules, payment, unlink",
        max_length=40,
    )
    response_input = discord.ui.TextInput(
        label="Tag response",
        placeholder="Message to send when staff uses /tag send",
        style=discord.TextStyle.paragraph,
        max_length=1900,
    )

    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=300)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        clean_name = clean_tag_name(str(self.name_input.value))
        if not clean_name:
            await interaction.response.send_message("Use a tag name with letters or numbers.", ephemeral=True)
            return
        set_tag(self.bot, interaction.guild.id, clean_name, str(self.response_input.value))
        await interaction.response.send_message(f"Saved tag `{clean_name}`.", ephemeral=True)


class TagEditModal(discord.ui.Modal, title="Edit Tag"):
    response_input = discord.ui.TextInput(
        label="New tag response",
        style=discord.TextStyle.paragraph,
        max_length=1900,
    )

    def __init__(self, bot: "TicketBot", tag_name: str, current_response: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.tag_name = clean_tag_name(tag_name)
        self.response_input.default = current_response[:1900]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        set_tag(self.bot, interaction.guild.id, self.tag_name, str(self.response_input.value))
        await interaction.response.send_message(f"Updated tag `{self.tag_name}`.", ephemeral=True)


class TagSelect(discord.ui.Select):
    def __init__(self, bot: "TicketBot", guild_id: int, mode: str):
        self.bot = bot
        self.guild_id = guild_id
        self.mode = mode
        tags = get_tags(bot, guild_id)
        options = [
            discord.SelectOption(label=name[:100], value=name, description=truncate(value, 90))
            for name, value in sorted(tags.items())[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="No tags saved", value="0", description="Create a tag first.")]
        super().__init__(placeholder=f"Choose a tag to {mode}...", min_values=1, max_values=1, options=options)
        if not tags:
            self.disabled = True

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        if self.values[0] == "0":
            await interaction.response.send_message("No tags are saved yet.", ephemeral=True)
            return
        tag_name = clean_tag_name(self.values[0])
        tags = get_tags(self.bot, interaction.guild.id)
        if tag_name not in tags:
            await interaction.response.send_message(f"No tag named `{tag_name}` was found.", ephemeral=True)
            return
        if self.mode == "edit":
            await interaction.response.send_modal(TagEditModal(self.bot, tag_name, tags[tag_name]))
            return
        existed = remove_tag(self.bot, interaction.guild.id, tag_name)
        if existed:
            await interaction.response.edit_message(
                embed=build_tag_admin_embed(self.bot, interaction.guild),
                view=TagAdminPanelView(self.bot),
            )
            await interaction.followup.send(f"Deleted tag `{tag_name}`.", ephemeral=True)
        else:
            await interaction.response.send_message(f"No tag named `{tag_name}` was found.", ephemeral=True)


class TagSelectView(discord.ui.View):
    def __init__(self, bot: "TicketBot", guild_id: int, mode: str):
        super().__init__(timeout=300)
        self.add_item(TagSelect(bot, guild_id, mode))


def build_tag_admin_embed(bot: "TicketBot", guild: discord.Guild) -> discord.Embed:
    tags = get_tags(bot, guild.id)
    embed = discord.Embed(
        title="Tag Admin Panel",
        description=(
            "Create, edit, or delete reusable ticket responses.\n\n"
            "Use `/tag send` inside a ticket to send one of these saved responses."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Saved tags", value=format_tag_list_for_embed(tags), inline=False)
    embed.set_footer(text="Tag commands can be managed from Discord Server Settings > Integrations.")
    return embed


class TagAdminPanelView(discord.ui.View):
    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=600)
        self.bot = bot

    @discord.ui.button(label="Create Tag", style=discord.ButtonStyle.success, row=0)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TagCreateModal(self.bot))

    @discord.ui.button(label="Edit Tag", style=discord.ButtonStyle.secondary, row=0)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Choose the tag you want to edit.",
            view=TagSelectView(self.bot, interaction.guild.id, "edit"),
            ephemeral=True,
        )

    @discord.ui.button(label="Delete Tag", style=discord.ButtonStyle.danger, row=0)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Choose the tag you want to delete.",
            view=TagSelectView(self.bot, interaction.guild.id, "delete"),
            ephemeral=True,
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=build_tag_admin_embed(self.bot, interaction.guild), view=self)


@app_commands.guild_only()
class TagCommands(commands.GroupCog, group_name="tag", group_description="Reusable ticket tag responses"):
    def __init__(self, bot: "TicketBot"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="admin", description="Open the tag admin panel.")
    @app_commands.default_permissions(manage_guild=True)
    async def tag_admin(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_tag_admin_embed(self.bot, interaction.guild),
            view=TagAdminPanelView(self.bot),
            ephemeral=True,
        )

    @app_commands.command(name="send", description="Send a saved tag response inside a ticket.")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.autocomplete(name=saved_tag_autocomplete)
    async def tag_send(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("Use `/tag send` inside a ticket channel.", ephemeral=True)
            return
        clean_name = clean_tag_name(name)
        response = get_tags(self.bot, interaction.guild.id).get(clean_name)
        if not response:
            await interaction.response.send_message(f"No tag named `{clean_name}` was found. Use `/tag list` to view saved tags.", ephemeral=True)
            return
        await interaction.response.send_message(response, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="list", description="List saved tag responses for this server.")
    @app_commands.default_permissions(manage_messages=True)
    async def tag_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        tags = get_tags(self.bot, interaction.guild.id)
        await interaction.response.send_message(format_tag_list_for_embed(tags), ephemeral=True)


@app_commands.guild_only()
class StatsCommands(commands.GroupCog, group_name="stats", group_description="Ticket staff statistics"):
    def __init__(self, bot: "TicketBot"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="user", description="Show ticket stats for a staff member.")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(member="Staff member to check. Leave blank to check yourself.")
    async def stats_user(self, interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        target = member or interaction.user
        summary = self.bot.stats_store.member_summary(interaction.guild.id, target.id)
        embed = discord.Embed(
            title=f"Ticket Stats: {target.display_name}",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Tickets Claimed", value=str(summary["claimed"]), inline=True)
        embed.add_field(name="Tickets Closed", value=str(summary["closed"]), inline=True)
        embed.add_field(name="Tickets Typed In", value=str(summary["typed"]), inline=True)
        embed.add_field(name="Staff Ticket Messages", value=str(summary["messages"]), inline=True)

        recent_lines: list[str] = []
        for item in summary["recent"][:5]:
            ticket = item["ticket"]
            ticket_number = ticket.get("ticket_number", "unknown")
            ticket_type = str(ticket.get("ticket_type", "normal")).title()
            recent_lines.append(
                f"`{ticket_number}` ({ticket_type}) — {item['action']} — {format_stat_time(item.get('at'))}"
            )
        embed.add_field(name="Recent Ticket Activity", value="\n".join(recent_lines) if recent_lines else "None", inline=False)
        embed.set_footer(text=f"User ID: {target.id}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="leaderboard", description="Show ticket staff leaderboard stats.")
    @app_commands.default_permissions(manage_messages=True)
    async def stats_leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        board = self.bot.stats_store.leaderboard(interaction.guild.id)
        embed = discord.Embed(
            title="Ticket Staff Leaderboard",
            description="Tracks claims, closes, and staff message counts from ticket channels.",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Most Tickets Claimed", value=format_leaderboard_section(interaction.guild, board["claimed"]), inline=False)
        embed.add_field(name="Most Tickets Closed", value=format_leaderboard_section(interaction.guild, board["closed"]), inline=False)
        embed.add_field(name="Most Ticket Messages", value=format_leaderboard_section(interaction.guild, board["messages"]), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TicketBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True

        super().__init__(command_prefix="!", intents=intents)
        self.config_store = GuildConfigStore(CONFIG_PATH)
        self.stats_store = TicketStatsStore(TICKET_STATS_PATH)
        self.ticket_ping_cooldowns: dict[int, datetime] = {}

    async def setup_hook(self) -> None:
        self.add_view(TicketPanelView(self))
        self.add_view(TicketChannelView(self))
        await self.add_cog(TicketCommands(self))
        await self.add_cog(TagCommands(self))
        await self.add_cog(StatsCommands(self))
        await self.tree.sync()

    async def on_ready(self) -> None:
        if self.user is not None:
            print(f"Logged in as {self.user} (ID: {self.user.id})")
            print("------")

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            await self.process_commands(message)
            return

        channel = message.channel
        if isinstance(channel, discord.TextChannel) and is_ticket_channel(channel) and isinstance(message.author, discord.Member):
            if member_is_staff(self, message.author):
                self.stats_store.ensure_ticket_from_channel(channel)
                self.stats_store.record_staff_message(message.guild.id, channel.id, message.author.id, message.created_at)

        await self.process_commands(message)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("Missing required configuration value: DISCORD_TOKEN")

    bot = TicketBot()
    asyncio.run(bot.start(TOKEN))


if __name__ == "__main__":
    main()
