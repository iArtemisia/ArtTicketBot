
from __future__ import annotations

import asyncio
import html
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
ENABLE_MESSAGE_CONTENT_INTENT = os.getenv("ENABLE_MESSAGE_CONTENT_INTENT", "true").strip().lower() not in {"0", "false", "no", "off"}
TRANSCRIPT_ASCII_SAFE = os.getenv("TRANSCRIPT_ASCII_SAFE", "true").strip().lower() not in {"0", "false", "no", "off"}
TRANSCRIPT_INCLUDE_BOT_EVENTS = os.getenv("TRANSCRIPT_INCLUDE_BOT_EVENTS", "true").strip().lower() in {"1", "true", "yes", "on"}
# Colored HTML transcript attachments are disabled by default. Mobile Discord
# made them harder to review, so closed tickets attach the plain .txt transcript.
TRANSCRIPT_HTML_ENABLED = os.getenv("TRANSCRIPT_HTML_ENABLED", "false").strip().lower() not in {"0", "false", "no", "off"}
TRANSCRIPT_TEXT_ATTACHMENT_ENABLED = os.getenv("TRANSCRIPT_TEXT_ATTACHMENT_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}

# STARZ ticket colors. Discord only supports the embed side-strip color and the
# built-in button styles, so these colors are applied to ticket embeds/events.
STARZ_COLOR_DARK = 0x10131A
STARZ_COLOR_RED = 0xE03131
STARZ_COLOR_BLUE = 0x5865F2
STARZ_COLOR_GREEN = 0x2ECC71
STARZ_COLOR_GOLD = 0xF1C40F
STARZ_COLOR_GRAY = 0x6B7280
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "data/guild_configs.json"))
DEFAULT_PANEL_GIF_URL = os.getenv("PANEL_GIF_URL", "").strip()
TICKET_LOG_DIR = CONFIG_PATH.parent / "ticket_logs"
TICKET_STATS_PATH = Path(os.getenv("TICKET_STATS_PATH", str(CONFIG_PATH.parent / "ticket_stats.json")))
TICKET_PING_COOLDOWN_SECONDS = 10 * 60
# Role mentionability toggling is disabled. The bot should not flip staff roles
# mentionable on/off because that spams Discord audit/mod logs.
TICKET_TEMP_ENABLE_ROLE_MENTIONS = False
# Keep direct staff-user mentions OFF by default. Role pings should stay role-only;
# set TICKET_USER_MENTION_FALLBACK=true only if you intentionally want user @ spam.
TICKET_USER_MENTION_FALLBACK = os.getenv("TICKET_USER_MENTION_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}
TICKET_HERE_FALLBACK = os.getenv("TICKET_HERE_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_MEMBERS_INTENT = os.getenv("ENABLE_MEMBERS_INTENT", "false").strip().lower() in {"1", "true", "yes", "on"}

try:
    AUTO_RESPONSE_COOLDOWN_SECONDS = max(5, int(os.getenv("AUTO_RESPONSE_COOLDOWN_SECONDS", "120").strip() or "120"))
except ValueError:
    AUTO_RESPONSE_COOLDOWN_SECONDS = 120

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


DEFAULT_TICKET_PANEL_TITLE = "Open a ticket!"
DEFAULT_TICKET_PANEL_AUTHOR = "STARZ"
DEFAULT_TICKET_PANEL_FOOTER = "One open ticket per user."
DEFAULT_TICKET_PANEL_COLOR_HEX = "#FF00AA"
DEFAULT_TICKET_PANEL_DESCRIPTION = (
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
)

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
    owner_label: str = "",
    claimed_label: str = "",
) -> str:
    """Build the public ticket channel topic/header.

    Discord shows this text in the ticket header/welcome screen. Older builds
    exposed raw machine metadata such as `ticket_owner:...|ticket_type:...`,
    which was ugly and hard for staff to use. This keeps the topic readable
    while `parse_ticket_topic` still understands it for bot logic.
    """
    clean_ticket_number = re.sub(r"[^0-9]", "", str(ticket_number or "")) or "unknown"
    clean_ticket_type = "priority" if str(ticket_type or "normal").lower().strip() == "priority" else "normal"
    clean_status = str(status or "open").lower().strip() or "open"

    try:
        clean_owner_id = int(owner_id or 0)
    except (TypeError, ValueError):
        clean_owner_id = 0

    try:
        clean_claimed_by = int(claimed_by or 0)
    except (TypeError, ValueError):
        clean_claimed_by = 0

    creator_part = owner_label.strip() if owner_label.strip() else (f"<@{clean_owner_id}> (Discord ID {clean_owner_id})" if clean_owner_id else "Unknown creator")
    claimed_part = claimed_label.strip() if claimed_label.strip() else (f"<@{clean_claimed_by}> (Discord ID {clean_claimed_by})" if clean_claimed_by else "None")

    clean_ping_ids: list[int] = []
    for raw_role_id in ping_role_ids or []:
        try:
            role_id = int(raw_role_id)
        except (TypeError, ValueError):
            continue
        if role_id and role_id not in clean_ping_ids:
            clean_ping_ids.append(role_id)

    ping_part = ", ".join(f"<@&{role_id}>" for role_id in clean_ping_ids) if clean_ping_ids else "None"

    return (
        f"Creator: {creator_part} | "
        f"Ticket #{clean_ticket_number} | "
        f"Type: {clean_ticket_type.title()} | "
        f"Status: {clean_status.title()} | "
        f"Claimed: {claimed_part} | "
        f"Ping Roles: {ping_part}"
    )


def parse_ticket_topic(topic: Optional[str]) -> dict[str, str]:
    """Parse both old raw metadata topics and new readable ticket headers."""
    data: dict[str, str] = {}
    if not topic:
        return data

    raw_topic = str(topic)

    for part in raw_topic.split("|"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        raw_key = key.strip()
        clean_key = raw_key.lower().strip().replace(" ", "_")
        clean_value = value.strip()

        # Old metadata format: ticket_owner:123|ticket_type:normal|...
        if clean_key in {"ticket_owner", "ticket_type", "ticket_number", "status", "claimed_by", "ping_roles"}:
            data[clean_key] = clean_value
            continue

        # New readable header format shown in Discord's ticket header.
        if clean_key in {"creator", "created_by", "owner", "ticket_owner"}:
            user_id = extract_id(clean_value)
            if user_id is not None:
                data["ticket_owner"] = str(user_id)
        elif clean_key in {"type", "ticket_type"}:
            value_lower = clean_value.lower().strip()
            data["ticket_type"] = "priority" if value_lower == "priority" else "normal"
        elif clean_key == "status":
            data["status"] = clean_value.lower().strip() or "open"
        elif clean_key in {"claimed", "claimed_by"}:
            user_id = extract_id(clean_value)
            data["claimed_by"] = str(user_id) if user_id is not None else "0"
        elif clean_key in {"ping_roles", "ping_role_ids"}:
            data["ping_roles"] = ",".join(str(role_id) for role_id in extract_ids(clean_value))

    # New readable header uses `Ticket #123`, which has no colon.
    if not data.get("ticket_number"):
        ticket_match = re.search(r"\bTicket\s*#\s*(\d+)\b", raw_topic, re.IGNORECASE)
        if ticket_match:
            data["ticket_number"] = ticket_match.group(1)

    # Normalize old metadata values too.
    if data.get("ticket_type"):
        data["ticket_type"] = "priority" if data["ticket_type"].lower().strip() == "priority" else "normal"
    if data.get("status"):
        data["status"] = data["status"].lower().strip() or "open"
    if data.get("claimed_by"):
        claimed_id = extract_id(data["claimed_by"]) or (int(data["claimed_by"]) if str(data["claimed_by"]).strip().isdigit() else 0)
        data["claimed_by"] = str(claimed_id)
    if data.get("ping_roles"):
        ping_ids = extract_ids(data["ping_roles"])
        if not ping_ids:
            ping_ids = [int(value) for value in re.findall(r"\d{15,25}", data["ping_roles"])]
        data["ping_roles"] = ",".join(str(role_id) for role_id in ping_ids)

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
    """Return True for both old raw ticket topics and new readable ticket headers.

    Older tickets used machine metadata in the channel topic:
    `ticket_owner:...|ticket_type:...|ticket_number:...`

    Newer tickets use a staff-friendly Discord header:
    `Creator: @user (id) | Ticket #123 | Type: Normal | ...`

    Button callbacks and `/sclose` rely on this helper, so it must not check
    only for the old literal `ticket_owner:` text.
    """
    if not isinstance(channel, discord.TextChannel):
        return False

    topic = channel.topic or ""
    data = parse_ticket_topic(topic)

    owner_id = data.get("ticket_owner", "")
    ticket_number = data.get("ticket_number", "")
    if owner_id.isdigit() and (ticket_number.isdigit() or re.search(r"\bticket[-_]?\d+\b", channel.name, re.IGNORECASE)):
        return True

    # Safety fallback for readable headers if Discord changes mention rendering.
    return bool(re.search(r"\bCreator\s*:", topic, re.IGNORECASE) and re.search(r"\bTicket\s*#\s*\d+", topic, re.IGNORECASE))


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


def discord_username_for_channel(member: discord.Member) -> str:
    raw_name = getattr(member, "name", "") or member.display_name
    return slugify(raw_name)[:40] or "user"


def claimed_channel_name(member: discord.Member, channel: discord.TextChannel) -> str:
    safe_name = discord_username_for_channel(member)
    ticket_number = get_ticket_number(channel)
    return f"{safe_name}-{ticket_number}"[:95]


def notes_channel_name(ticket_channel: discord.TextChannel) -> str:
    ticket_number = get_ticket_number(ticket_channel)
    return f"notes-{ticket_number}"[:95]


def discord_member_display_name(member: discord.abc.User) -> str:
    """Return the best readable Discord display name available for embeds/logs."""
    display_name = (
        getattr(member, "display_name", None)
        or getattr(member, "global_name", None)
        or getattr(member, "name", None)
        or str(member)
    )
    return str(display_name or "Discord User").strip() or "Discord User"


def discord_member_username(member: discord.abc.User) -> str:
    """Return the Discord username/handle when Discord exposes it."""
    username = getattr(member, "name", None) or str(member)
    return str(username or "").strip()


def format_member_reference(member: discord.abc.User) -> str:
    """Format a resolved Discord member/user with mention, name, username, and ID."""
    user_id = int(getattr(member, "id", 0) or 0)
    display_name = discord_member_display_name(member)
    username = discord_member_username(member)
    name_line = display_name
    if username and username.lower() != display_name.lower():
        name_line = f"{display_name} (@{username})"
    mention = getattr(member, "mention", f"<@{user_id}>")
    return f"{mention}\n{name_line}\n`{user_id}`"


def format_member_topic_reference(member: discord.abc.User) -> str:
    """Short readable creator/claimer text for the Discord channel header/topic."""
    user_id = int(getattr(member, "id", 0) or 0)
    display_name = discord_member_display_name(member)
    username = discord_member_username(member)
    label = display_name
    if username and username.lower() != display_name.lower():
        label = f"{display_name} / @{username}"
    mention = getattr(member, "mention", f"<@{user_id}>")
    return f"{mention} ({label} | {user_id})"


def format_user_reference(guild: discord.Guild, user_id: Optional[int]) -> str:
    """Sync best-effort user label for embeds. Prefer resolved names over raw IDs."""
    if not user_id:
        return "None"
    member = guild.get_member(int(user_id))
    if member is not None:
        return format_member_reference(member)
    clean_id = int(user_id)
    return f"<@{clean_id}>\nDiscord ID: `{clean_id}`"


async def format_user_reference_async(guild: discord.Guild, user_id: Optional[int]) -> str:
    """Async user label that fetches the member when it is not in cache."""
    if not user_id:
        return "None"
    member = await fetch_member_safe(guild, int(user_id))
    if member is not None:
        return format_member_reference(member)
    clean_id = int(user_id)
    return f"<@{clean_id}>\nDiscord ID: `{clean_id}`"


async def format_ticket_topic_user_reference(guild: discord.Guild, user_id: Optional[int]) -> str:
    """Async readable user label for channel topic/header text."""
    if not user_id:
        return "Unknown creator"
    member = await fetch_member_safe(guild, int(user_id))
    if member is not None:
        return format_member_topic_reference(member)
    clean_id = int(user_id)
    return f"<@{clean_id}> (Discord ID {clean_id})"


def format_log_user_reference(user_id: Optional[int]) -> str:
    """Format users for the transcript log embed using a Discord mention plus ID.

    This is only used in the Discord log embed, not inside downloaded
    transcripts. Embeds should show the clickable @ mention and the raw ID so
    staff can quickly open/copy the user.
    """
    if not user_id:
        return "None"
    clean_id = int(user_id)
    return f"<@{clean_id}> (`{clean_id}`)"


def ticket_status_color(ticket_type: str, *, status: str = "open", claimed_by: Optional[int] = None) -> discord.Color:
    """Return the embed accent color for the live ticket card/events."""
    clean_status = str(status or "open").lower().strip()
    clean_type = str(ticket_type or "normal").lower().strip()

    if clean_status == "closed":
        return discord.Color(STARZ_COLOR_RED)
    if claimed_by:
        return discord.Color(STARZ_COLOR_GREEN)
    if clean_status in {"ping", "attention"}:
        return discord.Color(STARZ_COLOR_GOLD)
    if clean_type == "priority":
        return discord.Color(STARZ_COLOR_RED)
    return discord.Color(STARZ_COLOR_BLUE)


def build_ticket_status_embed(
    *,
    guild: discord.Guild,
    ticket_number: str,
    ticket_type: str,
    owner_id: int,
    claimed_by: Optional[int] = None,
    status: str = "Open",
    created_by_text: str = "",
    claimed_by_text: str = "",
) -> discord.Embed:
    """Build the colored ticket card shown at the top of each live ticket."""
    clean_type = "priority" if str(ticket_type).lower() == "priority" else "normal"
    is_priority = clean_type == "priority"
    emoji = PRIORITY_TICKET_CONFIG["emoji"] if is_priority else TICKET_CONFIG["emoji"]
    title = f"{emoji} {'Priority Ticket' if is_priority else 'Ticket'} #{ticket_number}"
    description = (
        "Thanks for opening a **priority** ticket. A higher-level staff member will help you soon."
        if is_priority
        else "Thanks for opening a ticket. A staff member will help you soon."
    )
    description += "\n\nUse **Ping Team** if you need staff attention."

    embed = discord.Embed(
        title=title,
        description=description,
        color=ticket_status_color(clean_type, status=status, claimed_by=claimed_by),
        timestamp=now_utc(),
    )
    embed.add_field(name="Ticket Number", value=f"`{ticket_number}`", inline=True)
    embed.add_field(name="Ticket Type", value="Priority" if is_priority else "Normal", inline=True)
    embed.add_field(name="Status", value=status, inline=True)

    creator_value = created_by_text or format_user_reference(guild, owner_id)
    embed.add_field(name="Created By", value=creator_value, inline=False)
    claimed_value = claimed_by_text or (format_user_reference(guild, claimed_by) if claimed_by else "Not claimed yet")
    embed.add_field(
        name="Claimed By",
        value=claimed_value,
        inline=False,
    )
    embed.set_footer(text=f"Creator ID: {owner_id}")
    return embed


def build_ticket_event_embed(
    *,
    title: str,
    description: str,
    color: discord.Color,
) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color, timestamp=now_utc())


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
        f"Ticket Number : {ticket.get('ticket_number') or get_ticket_number(channel)}",
        f"Channel ID    : {ticket.get('ticket_channel_id') or channel.id}",
        f"Ticket Type   : {str(ticket.get('ticket_type') or get_ticket_kind(channel)).title()}",
        f"Opened By     : {format_user_reference(guild, int(ticket.get('opened_by') or 0))}",
        f"Claimed By    : {format_user_reference(guild, int(ticket.get('claimed_by') or 0))}",
        f"Closed By     : {format_user_reference(guild, int(ticket.get('closed_by') or 0))}",
        f"Opened At     : {format_stat_time(ticket.get('opened_at'))}",
        f"Claimed At    : {format_stat_time(ticket.get('claimed_at'))}",
        f"Closed At     : {format_stat_time(ticket.get('closed_at'))}",
        f"Close Reason  : {ticket.get('close_reason') or 'None'}",
        "",
        "Staff message activity:",
        format_staff_activity_summary(guild, ticket),
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


async def safe_edit_notes_name(notes_channel: discord.abc.GuildChannel, name: str, *, reason: str) -> None:
    if getattr(notes_channel, "name", None) == name:
        return
    try:
        await notes_channel.edit(name=name, reason=reason)
    except (discord.Forbidden, discord.HTTPException, TypeError):
        pass


async def place_notes_channel_below_ticket(
    notes_channel: discord.TextChannel,
    ticket_channel: discord.TextChannel,
    *,
    reason: str = "Place staff notes directly under linked ticket.",
) -> bool:
    """Keep visible staff-notes channels directly under their ticket channel.

    Discord text channels cannot truly be attached/nested under another text
    channel. The closest safe behavior is to keep `notes-####` immediately below
    its matching ticket in the same category. This only edits channel/category
    placement and category sync; it does not edit any Discord role permissions.
    """
    if not isinstance(notes_channel, discord.TextChannel) or not isinstance(ticket_channel, discord.TextChannel):
        return False
    if notes_channel.id == ticket_channel.id:
        return False

    category = ticket_channel.category
    if category is None:
        return False

    moved = False
    try:
        if getattr(notes_channel.category, "id", None) != category.id:
            await notes_channel.edit(
                category=category,
                sync_permissions=True,
                reason=f"{reason} Move notes into ticket category.",
            )
            moved = True

        ordered_channels = sorted(category.text_channels, key=lambda item: item.position)
        try:
            ticket_index = next(index for index, channel in enumerate(ordered_channels) if channel.id == ticket_channel.id)
        except StopIteration:
            ticket_index = -1

        already_below = (
            ticket_index >= 0
            and ticket_index + 1 < len(ordered_channels)
            and ordered_channels[ticket_index + 1].id == notes_channel.id
        )
        if already_below:
            return moved

        await notes_channel.move(
            after=ticket_channel,
            sync_permissions=True,
            reason=reason,
        )
        return True
    except (discord.Forbidden, discord.HTTPException, TypeError):
        return moved


async def repair_visible_notes_position_for_ticket(ticket_channel: discord.TextChannel) -> int:
    """Move every visible notes channel for one ticket directly below that ticket."""
    if not isinstance(ticket_channel, discord.TextChannel) or not is_ticket_channel(ticket_channel):
        return 0

    moved_count = 0
    for notes_channel in find_legacy_notes_channels_for_ticket(ticket_channel.guild, ticket_channel):
        if not isinstance(notes_channel, discord.TextChannel):
            continue
        if await place_notes_channel_below_ticket(
            notes_channel,
            ticket_channel,
            reason="Keep staff notes directly under linked ticket.",
        ):
            moved_count += 1
    return moved_count


async def repair_all_visible_notes_positions(bot: "TicketBot") -> dict[str, int]:
    """Best-effort startup cleanup for existing visible staff-notes channels."""
    scanned_tickets = 0
    moved_notes = 0

    for guild in bot.guilds:
        try:
            categories = await get_ticket_lookup_categories(bot, guild)
        except Exception:
            categories = []

        for category in categories:
            for channel in list(category.text_channels):
                if not is_ticket_channel(channel):
                    continue
                scanned_tickets += 1
                moved_notes += await repair_visible_notes_position_for_ticket(channel)

    return {"scanned_tickets": scanned_tickets, "moved_notes": moved_notes}


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



def normalize_panel_text(value: Any, default: str, limit: int) -> str:
    """Clean ticket panel text while keeping a safe default per Discord embed limits."""
    clean_value = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not clean_value:
        clean_value = default
    return clean_value[:limit]


def normalize_panel_color_hex(value: Any, default: str = DEFAULT_TICKET_PANEL_COLOR_HEX) -> str:
    """Return a safe #RRGGBB value for the ticket panel embed color."""
    clean_value = str(value or "").strip().upper()
    if not clean_value:
        clean_value = default
    if clean_value.startswith("0X"):
        clean_value = clean_value[2:]
    clean_value = clean_value.lstrip("#")
    if not re.fullmatch(r"[0-9A-F]{6}", clean_value):
        clean_value = default.lstrip("#").upper()
    return f"#{clean_value}"


def panel_color_from_hex(value: Any) -> discord.Color:
    clean_value = normalize_panel_color_hex(value).lstrip("#")
    return discord.Color(int(clean_value, 16))


def get_ticket_panel_message_config(bot: "TicketBot", guild_id: int) -> dict[str, str]:
    """Return this guild's saved ticket panel embed text, falling back to STARZ defaults."""
    config = bot.config_store.get_guild(guild_id)
    return {
        "title": normalize_panel_text(config.get("panel_title"), DEFAULT_TICKET_PANEL_TITLE, 256),
        "description": normalize_panel_text(config.get("panel_description"), DEFAULT_TICKET_PANEL_DESCRIPTION, 4096),
        "author": normalize_panel_text(config.get("panel_author"), DEFAULT_TICKET_PANEL_AUTHOR, 256),
        "footer": normalize_panel_text(config.get("panel_footer"), DEFAULT_TICKET_PANEL_FOOTER, 2048),
        "color_hex": normalize_panel_color_hex(config.get("panel_color_hex"), DEFAULT_TICKET_PANEL_COLOR_HEX),
    }


def build_ticket_panel_embed(bot: "TicketBot", guild: discord.Guild) -> discord.Embed:
    """Build the public ticket panel embed using per-server saved settings."""
    panel_config = get_ticket_panel_message_config(bot, guild.id)
    embed = discord.Embed(
        title=panel_config["title"],
        description=panel_config["description"],
        color=panel_color_from_hex(panel_config["color_hex"]),
        timestamp=now_utc(),
    )
    if panel_config["author"]:
        embed.set_author(name=panel_config["author"])

    panel_gif_url = get_panel_gif_url(bot, guild.id)
    if panel_gif_url:
        embed.set_image(url=panel_gif_url)

    if panel_config["footer"]:
        embed.set_footer(text=panel_config["footer"])
    return embed


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
    """Return whether this member can open priority tickets.

    Staff should always be able to open/test priority tickets. Earlier patches
    only allowed server managers or configured priority-opener roles, which made
    priority ticket testing look broken for staff.
    """
    if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
        return True
    if member_is_staff(bot, member):
        return True

    allowed_role_ids = set(get_role_ids_from_config(bot, member.guild.id, "priority_allowed"))
    priority_staff_ids = set(get_role_ids_from_config(bot, member.guild.id, "priority_staff"))
    member_role_ids = {role.id for role in member.roles}
    return bool((allowed_role_ids | priority_staff_ids) & member_role_ids)


def get_ticket_ping_roles(bot: "TicketBot", guild: discord.Guild, channel: discord.TextChannel) -> list[discord.Role]:
    roles: list[discord.Role] = []
    for role_id in get_ticket_ping_role_ids(channel):
        role = guild.get_role(role_id)
        if role is not None:
            roles.append(role)
    return roles


def mention_roles(roles: list[discord.Role]) -> str:
    return " ".join(f"<@&{role.id}>" for role in roles)


def unique_roles(*role_lists: list[discord.Role]) -> list[discord.Role]:
    """Return unique non-default roles while preserving order."""
    result: list[discord.Role] = []
    seen: set[int] = set()
    for role_list in role_lists:
        for role in role_list:
            if role is None or role.is_default() or role.id in seen:
                continue
            seen.add(role.id)
            result.append(role)
    return result


def ticket_role_access_overwrite() -> discord.PermissionOverwrite:
    """Safe default overwrite for staff/access roles inside ticket channels.

    This intentionally does NOT grant Manage Messages, Manage Threads,
    Manage Channels, or Mention Everyone/All Roles. Higher staff should get
    elevated powers from their actual Discord roles, not from every ticket
    channel overwrite.
    """
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
        send_messages_in_threads=True,
    )


def role_allowed_mentions(roles: list[discord.Role] | None = None) -> discord.AllowedMentions:
    """Allow role mentions to parse for ticket alerts.

    Using roles=True is more reliable across discord.py versions than passing
    Role objects here. The message content is still built only from the roles
    we chose, so this does not mention random roles.
    """
    return discord.AllowedMentions(everyone=False, users=False, roles=True)


def bot_can_manage_role(guild: discord.Guild, role: discord.Role) -> bool:
    """Return whether this bot can temporarily toggle a role's mentionable state."""
    me = guild.me
    if me is None or role.is_default() or role.managed:
        return False
    return bool(me.guild_permissions.manage_roles and me.top_role > role)


def members_for_roles(roles: list[discord.Role]) -> list[discord.Member]:
    """Return cached members that have any of the given roles.

    This is used only if TICKET_USER_MENTION_FALLBACK=true. It is disabled
    by default because it can spam individual staff members.
    """
    target_role_ids = {role.id for role in roles if role is not None}
    if not target_role_ids:
        return []

    result: list[discord.Member] = []
    seen: set[int] = set()
    for role in roles:
        for member in getattr(role, "members", []):
            if member.bot or member.id in seen:
                continue
            member_role_ids = {member_role.id for member_role in getattr(member, "roles", [])}
            if target_role_ids & member_role_ids:
                seen.add(member.id)
                result.append(member)
    return result


def build_member_mention_chunks(members: list[discord.Member], *, prefix: str, limit: int = 1850) -> list[str]:
    chunks: list[str] = []
    current = prefix.strip()
    for member in members:
        mention = member.mention
        candidate = f"{current} {mention}".strip() if current else mention
        if len(candidate) > limit and current:
            chunks.append(current)
            current = f"{prefix.strip()} {mention}".strip() if prefix else mention
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def send_role_ping_message(
    channel: discord.TextChannel,
    roles: list[discord.Role],
    *,
    content_prefix: str = "",
    embed: Optional[discord.Embed] = None,
    reason: str = "Ticket role ping",
) -> tuple[bool, str]:
    """Send a role ping without editing/toggling any Discord roles.

    This intentionally does NOT call role.edit(mentionable=True/False).
    The bot will only send the configured role mention and let Discord handle
    notification based on normal permissions:
    - the bot has Mention @everyone, @here, and All Roles permission, or
    - the target role is already mentionable.

    This avoids audit-log spam from repeatedly switching staff roles mentionable.
    """
    ping_roles = unique_roles(roles)
    if not ping_roles:
        return False, "No roles were provided for the ping."

    mentions = mention_roles(ping_roles)
    clean_prefix = str(content_prefix or "").strip()
    content = f"{clean_prefix} {mentions}".strip() if clean_prefix else mentions

    bot_member = channel.guild.me
    channel_perms = channel.permissions_for(bot_member) if bot_member is not None else None
    bot_can_mention_all_roles = bool(
        bot_member is not None
        and getattr(bot_member.guild_permissions, "mention_everyone", False)
        and getattr(channel_perms, "mention_everyone", False)
    )
    blocked_roles = [role for role in ping_roles if not role.mentionable and not bot_can_mention_all_roles]
    user_fallback_members = members_for_roles(ping_roles) if TICKET_USER_MENTION_FALLBACK else []

    try:
        await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=role_allowed_mentions(ping_roles),
        )

        # Optional fallback is still off by default. It exists only if you
        # intentionally enable it with TICKET_USER_MENTION_FALLBACK=true.
        if TICKET_USER_MENTION_FALLBACK and user_fallback_members:
            for chunk in build_member_mention_chunks(
                user_fallback_members,
                prefix="🔔 Staff direct alert fallback:",
            ):
                await channel.send(
                    content=chunk,
                    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
                )
        elif TICKET_HERE_FALLBACK and blocked_roles:
            await channel.send(
                content="🔔 Staff alert fallback: @here",
                allowed_mentions=discord.AllowedMentions(everyone=True, roles=False, users=False),
            )
    except discord.Forbidden as exc:
        return False, f"I do not have permission to send the role ping in {channel.mention}: `{truncate(str(exc), 250)}`"
    except discord.HTTPException as exc:
        return False, f"Discord rejected the role ping message: `{truncate(str(exc), 250)}`"

    if blocked_roles and TICKET_USER_MENTION_FALLBACK and user_fallback_members:
        names = ", ".join(f"{role.name} (`{role.id}`)" for role in blocked_roles[:8])
        return True, (
            "Role mention was sent, but Discord may not notify these non-mentionable roles without the bot's "
            f"Mention All Roles permission: {names}. Direct staff-user fallback was enabled, so individual staff members were also mentioned."
        )

    if blocked_roles:
        names = ", ".join(f"{role.name} (`{role.id}`)" for role in blocked_roles[:8])
        return True, (
            "Role mention was sent without editing any role settings. Discord may not notify these roles unless the bot has "
            "Mention @everyone/@here/All Roles permission in this channel, or the role is manually mentionable: "
            f"{names}."
        )

    return True, ""


async def repair_ticket_permission_overwrites(bot: "TicketBot", channel: discord.TextChannel) -> dict[str, int]:
    """Remove dangerous ticket-channel grants from role overwrites.

    Previous patches accidentally granted access roles permissions such as
    Manage Messages, Manage Threads, and Mention Everyone/All Roles inside
    every ticket. This repair keeps ticket visibility while clearing those
    elevated channel-specific grants.
    """
    scanned = 0
    repaired = 0

    for target, overwrite in list(channel.overwrites.items()):
        if not isinstance(target, discord.Role):
            continue
        if target.is_default() or target.managed:
            continue
        if overwrite.view_channel is not True:
            continue

        scanned += 1
        safe_overwrite = ticket_role_access_overwrite()

        # Preserve explicit deny values for non-dangerous basics if they existed,
        # but remove elevated grants that made low-level roles too powerful.
        safe_overwrite.manage_channels = None
        safe_overwrite.manage_messages = None
        safe_overwrite.manage_threads = None
        safe_overwrite.mention_everyone = None
        safe_overwrite.create_private_threads = None
        safe_overwrite.create_public_threads = None
        safe_overwrite.pin_messages = None

        if overwrite != safe_overwrite:
            await channel.set_permissions(
                target,
                overwrite=safe_overwrite,
                reason="Repair ticket role permissions: remove elevated channel grants",
            )
            repaired += 1

    return {"scanned": scanned, "repaired": repaired}


async def repair_ticket_permissions_in_categories(bot: "TicketBot", guild: discord.Guild) -> dict[str, int]:
    categories = await get_ticket_lookup_categories(bot, guild)
    scanned = 0
    repaired_channels = 0
    repaired_roles = 0

    for category in categories:
        for channel in category.text_channels:
            if not is_ticket_channel(channel):
                continue
            scanned += 1
            result = await repair_ticket_permission_overwrites(bot, channel)
            if result.get("repaired", 0):
                repaired_channels += 1
                repaired_roles += int(result.get("repaired", 0))

    return {"scanned": scanned, "repaired_channels": repaired_channels, "repaired_roles": repaired_roles}


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
    # Staff Filter only applies to access roles. Ping roles must be selectable
    # from any server role because servers may ping non-staff helper/support
    # roles that should not appear in the access-role dropdown.
    staff_filtered_targets = {"normal_staff", "priority_staff"}

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
    if normalized_target in {"normal_staff", "priority_staff"} and action == "add" and staff_filter_roles:
        helper += "\
This list is filtered to your configured staff-filter roles."
    elif normalized_target in {"normal_ping", "priority_ping"} and action == "add":
        helper += "\
Ping roles are not limited by the staff filter, so any non-managed server role can be selected."
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


def normalize_auto_response_trigger(trigger: Any) -> str:
    """Normalize a trigger key while preserving readable trigger text in config."""
    value = re.sub(r"\s+", " ", str(trigger or "").strip())
    return value.lower()


def normalize_auto_response_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_auto_response_mode(mode: Any) -> str:
    clean_mode = str(mode or "contains").strip().lower().replace("-", "_").replace(" ", "_")
    if clean_mode in {"exact", "exact_phrase", "phrase", "full", "full_message"}:
        return "exact"
    return "contains"


def get_auto_responses(bot: "TicketBot", guild_id: int) -> dict[str, dict[str, Any]]:
    """Return enabled/disabled ticket auto-responses from this guild's config."""
    config = bot.config_store.get_guild(guild_id)
    raw = config.get("auto_responses", {})
    if not isinstance(raw, dict):
        return {}

    cleaned: dict[str, dict[str, Any]] = {}
    for raw_key, raw_entry in raw.items():
        if isinstance(raw_entry, dict):
            trigger = normalize_auto_response_text(raw_entry.get("trigger") or raw_key)
            response = str(raw_entry.get("response") or "").strip()
            enabled = bool(raw_entry.get("enabled", True))
            mode = normalize_auto_response_mode(raw_entry.get("mode", "contains"))
        else:
            trigger = normalize_auto_response_text(raw_key)
            response = str(raw_entry or "").strip()
            enabled = True
            mode = "contains"

        trigger_key = normalize_auto_response_trigger(trigger)
        if not trigger_key or not response:
            continue

        cleaned[trigger_key] = {
            "trigger": trigger[:100],
            "response": response[:1900],
            "enabled": enabled,
            "mode": mode,
        }

    return cleaned


def save_auto_responses(bot: "TicketBot", guild_id: int, responses: dict[str, dict[str, Any]]) -> None:
    ordered = {
        key: responses[key]
        for key in sorted(responses.keys(), key=lambda item: responses[item].get("trigger", item).lower())
    }
    bot.config_store.update_guild(guild_id, auto_responses=ordered)


def set_auto_response(
    bot: "TicketBot",
    guild_id: int,
    *,
    trigger: str,
    response: str,
    mode: str = "contains",
) -> tuple[dict[str, Any], bool]:
    responses = get_auto_responses(bot, guild_id)
    clean_trigger = normalize_auto_response_text(trigger)[:100]
    trigger_key = normalize_auto_response_trigger(clean_trigger)
    existed = trigger_key in responses
    previous = responses.get(trigger_key, {})
    entry = {
        "trigger": clean_trigger,
        "response": str(response or "").strip()[:1900],
        "enabled": bool(previous.get("enabled", True)),
        "mode": normalize_auto_response_mode(mode),
    }
    responses[trigger_key] = entry
    save_auto_responses(bot, guild_id, responses)
    return entry, existed


def delete_auto_response(bot: "TicketBot", guild_id: int, trigger: str) -> Optional[dict[str, Any]]:
    responses = get_auto_responses(bot, guild_id)
    trigger_key = normalize_auto_response_trigger(trigger)
    removed = responses.pop(trigger_key, None)
    if removed is not None:
        save_auto_responses(bot, guild_id, responses)
    return removed


def toggle_auto_response(bot: "TicketBot", guild_id: int, trigger: str, enabled: bool) -> Optional[dict[str, Any]]:
    responses = get_auto_responses(bot, guild_id)
    trigger_key = normalize_auto_response_trigger(trigger)
    entry = responses.get(trigger_key)
    if entry is None:
        return None

    entry["enabled"] = bool(enabled)
    responses[trigger_key] = entry
    save_auto_responses(bot, guild_id, responses)
    return entry


def auto_response_choices(bot: "TicketBot", guild_id: int, current: str = "") -> list[app_commands.Choice[str]]:
    query = normalize_auto_response_trigger(current)
    choices: list[app_commands.Choice[str]] = []
    for entry in get_auto_responses(bot, guild_id).values():
        trigger = str(entry.get("trigger") or "").strip()
        if not trigger:
            continue
        if query and query not in trigger.lower():
            continue
        choices.append(app_commands.Choice(name=trigger[:100], value=trigger[:100]))
    choices.sort(key=lambda choice: choice.name.lower())
    return choices[:25]


async def auto_response_trigger_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild is None or not isinstance(interaction.client, TicketBot):
        return []
    return auto_response_choices(interaction.client, interaction.guild.id, current)


def find_auto_response_match(
    bot: "TicketBot",
    guild_id: int,
    message_text: str,
    *,
    include_disabled: bool = False,
) -> Optional[dict[str, Any]]:
    content = normalize_auto_response_trigger(message_text)
    if not content:
        return None

    entries = list(get_auto_responses(bot, guild_id).values())
    entries.sort(key=lambda item: len(normalize_auto_response_trigger(item.get("trigger", ""))), reverse=True)

    for entry in entries:
        if not include_disabled and not bool(entry.get("enabled", True)):
            continue

        trigger_key = normalize_auto_response_trigger(entry.get("trigger", ""))
        if not trigger_key:
            continue

        mode = normalize_auto_response_mode(entry.get("mode", "contains"))
        if mode == "exact":
            matched = content == trigger_key
        else:
            matched = trigger_key in content

        if matched:
            return entry

    return None


def auto_response_allowed_mentions() -> discord.AllowedMentions:
    """Allow configured user/role mentions, but never @everyone/@here."""
    return discord.AllowedMentions(everyone=False, users=True, roles=True, replied_user=False)


def member_can_manage_auto_responses(bot: "TicketBot", member: discord.Member) -> bool:
    """Match ticket-admin style permissions without granting this to normal users."""
    return bool(
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
        or member.guild_permissions.manage_channels
    )


async def log_auto_response_change(
    bot: "TicketBot",
    guild: discord.Guild,
    *,
    action: str,
    admin: discord.Member,
    entry: dict[str, Any],
) -> None:
    description = (
        f"**Admin:** {admin.mention} (`{admin.id}`)\n"
        f"**Trigger:** `{truncate(str(entry.get('trigger') or ''), 180)}`\n"
        f"**Mode:** `{normalize_auto_response_mode(entry.get('mode', 'contains'))}`\n"
        f"**Enabled:** `{bool(entry.get('enabled', True))}`\n"
        f"**Response Preview:** {truncate(str(entry.get('response') or ''), 900)}"
    )
    try:
        await log_event(
            bot,
            guild,
            title=f"Auto-response {action}",
            description=description,
            color=discord.Color(STARZ_COLOR_BLUE),
        )
    except (discord.Forbidden, discord.HTTPException):
        pass


async def maybe_send_auto_response(bot: "TicketBot", message: discord.Message) -> None:
    if message.guild is None or message.author.bot:
        return
    if not isinstance(message.channel, discord.TextChannel) or not is_ticket_channel(message.channel):
        return

    entry = find_auto_response_match(bot, message.guild.id, message.content or "")
    if entry is None:
        return

    trigger_key = normalize_auto_response_trigger(entry.get("trigger", ""))
    if not trigger_key:
        return

    cooldowns = getattr(bot, "auto_response_cooldowns", {})
    cooldown_key = (int(message.guild.id), int(message.channel.id), trigger_key)
    now = now_utc()
    last_sent = cooldowns.get(cooldown_key)
    if last_sent is not None and (now - last_sent).total_seconds() < AUTO_RESPONSE_COOLDOWN_SECONDS:
        return

    # Opportunistically prune stale cooldown entries for this ticket.
    for key, sent_at in list(cooldowns.items()):
        if len(key) == 3 and key[0] == message.guild.id and key[1] == message.channel.id:
            if (now - sent_at).total_seconds() > max(AUTO_RESPONSE_COOLDOWN_SECONDS * 4, 600):
                cooldowns.pop(key, None)

    cooldowns[cooldown_key] = now
    bot.auto_response_cooldowns = cooldowns

    response = str(entry.get("response") or "").strip()
    if not response:
        return

    try:
        await message.reply(
            response[:1900],
            mention_author=False,
            allowed_mentions=auto_response_allowed_mentions(),
        )
    except (discord.Forbidden, discord.HTTPException):
        pass


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


def clear_notes_thread_id(bot: "TicketBot", guild_id: int, ticket_channel_id: int) -> None:
    """Remove a stale or invalid notes-thread mapping for one ticket channel."""
    config = bot.config_store.get_guild(guild_id)
    mapping = config.get("notes_thread_ids", {})
    if not isinstance(mapping, dict):
        bot.config_store.update_guild(guild_id, notes_thread_ids={})
        return

    if str(ticket_channel_id) in mapping:
        mapping.pop(str(ticket_channel_id), None)
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
    priority_category = guild.get_channel(config.get("priority_ticket_category_id", 0))
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
    embed.add_field(name="Normal ticket category", value=category.mention if category else "Not set", inline=False)
    embed.add_field(
        name="Priority ticket category",
        value=priority_category.mention if isinstance(priority_category, discord.CategoryChannel) else "Using normal ticket category",
        inline=False,
    )
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
            value=(
                "Use the buttons below to add/remove roles from this ticket or change this ticket's ping roles.\n"
                "These ticket-specific tools are separate from the default normal/priority settings above."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="Ticket-specific tools",
            value="Run `/ticketadmin` inside a ticket channel to edit that ticket's role permissions and ping roles.",
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


async def refresh_ticket_status_message(bot: "TicketBot", channel: discord.TextChannel, *, status: str = "Open") -> None:
    """Edit the starter ticket card so its color/status stays current."""
    owner_id = get_ticket_owner_id(channel) or 0
    ticket_type = get_ticket_kind(channel)
    ticket_number = get_ticket_number(channel)
    claimed_by = get_claimed_by_id(channel)

    created_by_text = await format_user_reference_async(channel.guild, owner_id)
    claimed_by_text = await format_user_reference_async(channel.guild, claimed_by) if claimed_by else ""

    embed = build_ticket_status_embed(
        guild=channel.guild,
        ticket_number=ticket_number,
        ticket_type=ticket_type,
        owner_id=owner_id,
        claimed_by=claimed_by,
        status=status,
        created_by_text=created_by_text,
        claimed_by_text=claimed_by_text,
    )

    bot_user_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
    try:
        async for message in channel.history(limit=30, oldest_first=True):
            if bot_user_id and int(getattr(message.author, "id", 0) or 0) != bot_user_id:
                continue
            if not message.embeds:
                continue

            for existing_embed in message.embeds:
                field_names = {clean_transcript_text(getattr(field, "name", "")).lower() for field in existing_embed.fields}
                has_ticket_number = any(
                    clean_transcript_text(getattr(field, "name", "")).lower() == "ticket number"
                    and ticket_number in clean_transcript_text(getattr(field, "value", ""))
                    for field in existing_embed.fields
                )
                if has_ticket_number or {"ticket number", "ticket type", "claimed by"}.issubset(field_names):
                    await message.edit(embed=embed, view=TicketChannelView(bot))
                    return
    except (discord.Forbidden, discord.HTTPException):
        return


async def get_ticket_category(
    bot: "TicketBot",
    guild: discord.Guild,
    *,
    priority: bool = False,
) -> Optional[discord.CategoryChannel]:
    """Return the correct category for normal or priority tickets.

    Normal tickets use ticket_category_id. Priority tickets can use
    priority_ticket_category_id. If no priority category is configured,
    priority tickets fall back to the normal ticket category so existing
    setups keep working until an admin runs `/ticketprioritycategory`.
    """
    config = bot.config_store.get_guild(guild.id)
    category_id = 0
    if priority:
        try:
            category_id = int(config.get("priority_ticket_category_id", 0) or 0)
        except (TypeError, ValueError):
            category_id = 0

    if not category_id:
        try:
            category_id = int(config.get("ticket_category_id", 0) or 0)
        except (TypeError, ValueError):
            category_id = 0

    channel = guild.get_channel(category_id)
    return channel if isinstance(channel, discord.CategoryChannel) else None


async def get_ticket_lookup_categories(bot: "TicketBot", guild: discord.Guild) -> list[discord.CategoryChannel]:
    """Return all configured ticket categories, de-duplicated.

    This lets the bot keep the one-open-ticket rule even after normal and
    priority tickets are separated into different categories.
    """
    categories: list[discord.CategoryChannel] = []
    seen: set[int] = set()
    for priority in (False, True):
        category = await get_ticket_category(bot, guild, priority=priority)
        if category is not None and category.id not in seen:
            seen.add(category.id)
            categories.append(category)
    return categories


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


def can_member_close_ticket(bot: "TicketBot", member: discord.Member, channel: discord.TextChannel) -> tuple[bool, str]:
    """Return whether a member can close this ticket.

    Ticket creators are intentionally blocked from closing their own tickets so
    staff must review and finish the case. This applies to both the Close button
    and `/sclose`, including when `/sclose` is used from the notes channel.
    """
    owner_id = get_ticket_owner_id(channel)
    if owner_id is None:
        return False, "This does not look like a valid ticket channel."

    if member.id == owner_id:
        return False, "Ticket creators cannot close their own tickets. A staff member must close this ticket."

    if not member_is_staff(bot, member):
        return False, "Only ticket staff can close tickets."

    return True, ""


def find_open_ticket_for_user(
    categories: discord.CategoryChannel | list[discord.CategoryChannel],
    user_id: int,
) -> Optional[discord.TextChannel]:
    search_categories = categories if isinstance(categories, list) else [categories]
    for category in search_categories:
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
    owner_label = await format_ticket_topic_user_reference(channel.guild, owner_id)
    claimed_label = await format_ticket_topic_user_reference(channel.guild, current_claimed_by) if current_claimed_by else ""

    await channel.edit(
        topic=build_ticket_topic(
            owner_id=owner_id,
            ticket_type=current_ticket_type,
            ticket_number=current_ticket_number,
            status=current_status,
            claimed_by=current_claimed_by,
            ping_role_ids=current_ping_role_ids,
            owner_label=owner_label,
            claimed_label=claimed_label,
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


def transcript_line(char: str = "-", width: int = 42) -> str:
    return char * width


def append_transcript_section(lines: list[str], title: str) -> None:
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(f"--- {transcript_safe_text(title).upper()} ---")
    lines.append("")


def append_transcript_subsection(lines: list[str], title: str) -> None:
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(f"[{transcript_safe_text(title)}]")


def transcript_safe_text(value: Any) -> str:
    """Make transcript text readable in Discord's text-file preview.

    Discord stores message text as UTF-8, but Discord's attachment preview and
    some mobile viewers can display smart quotes/emojis as mojibake. This keeps
    transcripts review-friendly by converting smart punctuation to plain ASCII
    and, by default, stripping emoji/non-ASCII symbols from the text log.
    """
    text_value = str(value or "")
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2026": "...",
        "\u2022": "-",
        "\u00a0": " ",
        "\u200b": "",
        "\ufe0f": "",
    }
    for old, new in replacements.items():
        text_value = text_value.replace(old, new)

    if TRANSCRIPT_ASCII_SAFE:
        text_value = text_value.encode("ascii", "ignore").decode("ascii")

    return text_value


def clean_transcript_text(value: Any) -> str:
    text_value = transcript_safe_text(value)
    text_value = text_value.replace("\r\n", "\n").replace("\r", "\n")
    text_value = re.sub(r"[ \t]+", " ", text_value)
    text_value = re.sub(r"\n{4,}", "\n\n\n", text_value)
    return text_value.strip()


def append_indented_text(lines: list[str], value: str, *, indent: str = "    ", empty: str = "[no text]") -> None:
    clean_value = clean_transcript_text(value)
    if not clean_value:
        if empty:
            lines.append(f"{indent}{empty}")
        return

    for raw_line in clean_value.split("\n"):
        if raw_line.strip():
            lines.append(f"{indent}{raw_line}")
        else:
            lines.append("")


def get_message_text_for_transcript(message: discord.Message) -> str:
    """Return the best readable text Discord exposes for this message."""
    candidates = (
        getattr(message, "clean_content", ""),
        getattr(message, "content", ""),
        getattr(message, "system_content", ""),
    )
    for candidate in candidates:
        clean_candidate = clean_transcript_text(candidate)
        if clean_candidate:
            return clean_candidate
    return ""


def message_has_visible_non_text(message: discord.Message) -> bool:
    return bool(
        getattr(message, "attachments", None)
        or getattr(message, "embeds", None)
        or getattr(message, "stickers", None)
        or getattr(message, "components", None)
    )


def is_transcript_event_message(message: discord.Message) -> bool:
    """Bot/system messages are useful, but should not clutter the conversation."""
    if getattr(message.author, "bot", False):
        return True

    message_type = getattr(message, "type", discord.MessageType.default)
    return message_type != discord.MessageType.default


def is_generic_transcript_name(value: Any, user_id: Optional[int] = None) -> bool:
    """Return True for Discord fallback names like `User 123...`."""
    clean_value = clean_transcript_text(value).strip()
    if not clean_value:
        return True

    lowered = clean_value.lower()
    if lowered in {"unknown", "unknown user", "none"}:
        return True

    if user_id:
        raw_id = str(int(user_id))
        generic_values = {
            raw_id,
            f"user {raw_id}",
            f"unknown user {raw_id}",
            f"unknown user - {raw_id}",
        }
        if lowered in generic_values:
            return True
        if lowered.startswith("user ") and raw_id in lowered:
            return True

    return False


def build_user_lookup_entry(*, user_id: int, display_name: Any = "", username: Any = "") -> Optional[dict[str, str]]:
    """Create consistent transcript labels from a Discord display name + username."""
    display = clean_transcript_text(display_name)
    user = clean_transcript_text(username)

    if is_generic_transcript_name(display, user_id):
        display = ""
    if is_generic_transcript_name(user, user_id):
        user = ""

    # Prefer a real display name, then username, then a clear ID fallback.
    primary = display or (f"@{user}" if user else "")
    if not primary:
        return None

    if user and display and user.lower() != display.lower():
        header = f"{display} (@{user}) - {user_id}"
        line = f"{display} (@{user} | {user_id})"
        participant = f"{display} (@{user}) - {user_id}"
    elif user and not display:
        header = f"@{user} - {user_id}"
        line = f"@{user} ({user_id})"
        participant = f"@{user} - {user_id}"
    else:
        header = f"{primary} - {user_id}"
        line = f"{primary} ({user_id})"
        participant = f"{primary} - {user_id}"

    return {"header": header, "line": line, "participant": participant, "name": display or user, "username": user}


def user_lookup_entry_from_author(author: Any) -> Optional[dict[str, str]]:
    try:
        user_id = int(getattr(author, "id", 0) or 0)
    except (TypeError, ValueError):
        user_id = 0
    if not user_id:
        return None

    display_name = (
        getattr(author, "display_name", None)
        or getattr(author, "global_name", None)
        or getattr(author, "name", None)
        or str(author)
    )
    username = getattr(author, "name", None) or ""
    return build_user_lookup_entry(user_id=user_id, display_name=display_name, username=username)


def user_lookup_entry_from_member(member: discord.abc.User) -> Optional[dict[str, str]]:
    user_id = int(getattr(member, "id", 0) or 0)
    if not user_id:
        return None

    display_name = (
        getattr(member, "display_name", None)
        or getattr(member, "global_name", None)
        or getattr(member, "name", None)
        or str(member)
    )
    username = getattr(member, "name", None) or ""
    return build_user_lookup_entry(user_id=user_id, display_name=display_name, username=username)


async def build_transcript_user_lookup(
    guild: discord.Guild,
    user_ids: set[int],
    messages: list[discord.Message],
) -> dict[int, dict[str, str]]:
    """Build a best-effort user directory so transcripts show names, not raw IDs.

    Discord sometimes gives the transcript builder only a generic user stub such
    as `User 123456789`. This lookup first uses authors from message history,
    then falls back to the guild cache, and finally tries a Discord REST fetch.
    """
    lookup: dict[int, dict[str, str]] = {}

    for message in messages:
        author = getattr(message, "author", None)
        entry = user_lookup_entry_from_author(author)
        if not entry:
            continue
        user_id = int(getattr(author, "id", 0) or 0)
        if user_id and user_id not in lookup:
            lookup[user_id] = entry
        if user_id:
            user_ids.add(user_id)

    for raw_user_id in list(user_ids):
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            continue
        if not user_id:
            continue

        existing = lookup.get(user_id)
        if existing and not is_generic_transcript_name(existing.get("name", ""), user_id):
            continue

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None

        if member is not None:
            entry = user_lookup_entry_from_member(member)
            if entry:
                lookup[user_id] = entry

    return lookup


def format_transcript_user_reference(
    guild: discord.Guild,
    user_id: Optional[int],
    user_lookup: Optional[dict[int, dict[str, str]]] = None,
) -> str:
    """Format users in transcript/log review text without Discord mentions."""
    if not user_id:
        return "None"

    clean_id = int(user_id)
    if user_lookup and clean_id in user_lookup:
        return user_lookup[clean_id].get("header") or user_lookup[clean_id].get("participant") or f"User {clean_id} - {clean_id}"

    member = guild.get_member(clean_id)
    if member is not None:
        entry = user_lookup_entry_from_member(member)
        if entry:
            return entry["header"]

    return f"Discord User - {clean_id}"


def format_transcript_user_value(
    guild: discord.Guild,
    raw_value: Any,
    user_lookup: Optional[dict[int, dict[str, str]]] = None,
) -> str:
    """Convert a mention-like audit value into transcript-friendly name + ID text."""
    value = clean_transcript_text(raw_value)
    if not value or value == "None":
        return "None"

    user_id = extract_id(value)
    if user_id is not None:
        return format_transcript_user_reference(guild, user_id, user_lookup)

    return value


def format_transcript_author(
    message: discord.Message,
    user_lookup: Optional[dict[int, dict[str, str]]] = None,
) -> str:
    author = message.author
    user_id = int(getattr(author, "id", 0) or 0)

    if user_lookup and user_id in user_lookup:
        return user_lookup[user_id].get("line") or user_lookup[user_id].get("header") or f"User {user_id}"

    entry = user_lookup_entry_from_author(author)
    if entry:
        return entry["line"]

    return f"Discord User ({user_id})" if user_id else "Unknown User"

def compact_one_line(value: Any, limit: int = 180) -> str:
    clean_value = clean_transcript_text(value).replace("\n", " / ")
    return truncate(clean_value, limit) if clean_value else ""


def append_embed_transcript(lines: list[str], message: discord.Message, *, compact: bool = True) -> None:
    if not message.embeds:
        return

    for index, embed in enumerate(message.embeds, start=1):
        title = compact_one_line(embed.title, 120)
        description = compact_one_line(embed.description, 180)
        parts = [part for part in (title, description) if part]
        label = f"Embed {index}"

        if parts:
            lines.append(f"    [{label}] " + " - ".join(parts))
        else:
            lines.append(f"    [{label}] Embed with no readable title/description")

        field_limit = 4 if compact else 8
        if embed.fields:
            for field in embed.fields[:field_limit]:
                field_name = compact_one_line(field.name, 80) or "Unnamed field"
                field_value = compact_one_line(field.value, 180) or "[empty]"
                lines.append(f"      - {field_name}: {field_value}")
            if len(embed.fields) > field_limit:
                lines.append(f"      - ... {len(embed.fields) - field_limit} more field(s)")

        footer_text = compact_one_line(getattr(embed.footer, "text", ""), 160)
        if footer_text:
            lines.append(f"      Footer: {footer_text}")

        image_url = getattr(getattr(embed, "image", None), "url", None)
        thumbnail_url = getattr(getattr(embed, "thumbnail", None), "url", None)
        if image_url:
            lines.append(f"      Image: {image_url}")
        if thumbnail_url:
            lines.append(f"      Thumbnail: {thumbnail_url}")


def append_attachment_transcript(lines: list[str], message: discord.Message) -> None:
    if not message.attachments:
        return

    lines.append("    Attachments / Evidence:")
    for attachment in message.attachments:
        filename = clean_transcript_text(getattr(attachment, "filename", "")) or "attachment"
        size = getattr(attachment, "size", 0) or 0
        size_text = f"{size:,} bytes" if size else "unknown size"
        lines.append(f"      - {filename} ({size_text})")
        lines.append(f"        {attachment.url}")


def append_sticker_transcript(lines: list[str], message: discord.Message) -> None:
    stickers = getattr(message, "stickers", None)
    if not stickers:
        return

    lines.append("    Stickers:")
    for sticker in stickers:
        name = clean_transcript_text(getattr(sticker, "name", "")) or "sticker"
        lines.append(f"      - {name}")


async def fetch_history_messages(source) -> list[discord.Message]:
    messages: list[discord.Message] = []
    try:
        async for message in source.history(limit=None, oldest_first=True):
            messages.append(message)
    except discord.Forbidden:
        raise
    except discord.HTTPException:
        raise
    return messages


def append_messages_grouped_by_day(
    lines: list[str],
    messages: list[discord.Message],
    *,
    empty_text: str,
    compact_embeds: bool = True,
    user_lookup: Optional[dict[int, dict[str, str]]] = None,
) -> int:
    if not messages:
        lines.append(empty_text)
        lines.append("")
        return 0

    unreadable_text_count = 0
    current_day = ""

    for message in messages:
        created = message.created_at.astimezone(timezone.utc)
        day_label = created.strftime("%A, %B %d, %Y")
        if day_label != current_day:
            current_day = day_label
            append_transcript_subsection(lines, day_label)

        time_label = created.strftime("%H:%M UTC")
        author = format_transcript_author(message, user_lookup)
        lines.append(f"[{time_label}] {author}")

        content = get_message_text_for_transcript(message)
        if content:
            append_indented_text(lines, content, empty="")
        elif message_has_visible_non_text(message):
            lines.append("    [no written text - see attachment/embed/sticker details below]")
        else:
            unreadable_text_count += 1
            lines.append("    [message text unavailable]")

        append_attachment_transcript(lines, message)
        append_sticker_transcript(lines, message)
        append_embed_transcript(lines, message, compact=compact_embeds)

        if getattr(message, "reactions", None):
            reaction_parts = []
            for reaction in message.reactions:
                reaction_parts.append(f"{transcript_safe_text(reaction.emoji)} x{reaction.count}".strip())
            if reaction_parts:
                lines.append("    Reactions: " + ", ".join(reaction_parts))

        lines.append("")

    if unreadable_text_count:
        lines.append("NOTE:")
        lines.append(
            f"  {unreadable_text_count} message(s) had no readable text. "
            "This usually means Discord Message Content Intent was disabled when the transcript was generated."
        )
        lines.append("  Enable Message Content Intent in the Discord Developer Portal and keep ENABLE_MESSAGE_CONTENT_INTENT=true.")
        lines.append("  Messages already closed/transcripted while the intent was disabled cannot have their text recovered by the bot.")
        lines.append("")

    return len(messages)


async def append_history_lines(lines: list[str], source, *, label: str, include_section: bool = True) -> int:
    """Compatibility helper for any older code that still asks for raw history."""
    if include_section:
        append_transcript_section(lines, label)

    try:
        messages = await fetch_history_messages(source)
    except discord.Forbidden:
        lines.append("Unable to read this section from Discord because the bot is missing read permissions.")
        return 0
    except discord.HTTPException:
        lines.append("Unable to read this section from Discord.")
        return 0

    return append_messages_grouped_by_day(lines, messages, empty_text="No messages found in this section.")


def transcript_file_from_text(text_value: str, filename: str) -> discord.File:
    payload = text_value.encode("utf-8")
    return discord.File(io.BytesIO(payload), filename=filename)


def transcript_file_from_html(html_value: str, filename: str) -> discord.File:
    payload = html_value.encode("utf-8")
    return discord.File(io.BytesIO(payload), filename=filename)


def html_linkify_text(value: str) -> str:
    """Escape text for HTML and turn URLs into review-friendly links."""
    raw_value = str(value or "")
    url_pattern = re.compile(r"(https?://[^\s<>'\"]+)")
    parts: list[str] = []
    last_index = 0

    for match in url_pattern.finditer(raw_value):
        parts.append(html.escape(raw_value[last_index:match.start()]))
        url = match.group(1)
        safe_url = html.escape(url, quote=True)
        parts.append(f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{safe_url}</a>')
        last_index = match.end()

    parts.append(html.escape(raw_value[last_index:]))
    return "".join(parts)


def transcript_html_line_class(line: str) -> str:
    clean_line = line.strip()
    if not clean_line:
        return "blank"
    if clean_line.startswith("STARZ Ticket"):
        return "title"
    if clean_line.startswith("--- ") and clean_line.endswith(" ---"):
        label = clean_line.strip("- ").lower()
        if "staff" in label:
            return "section section-notes"
        if "evidence" in label or "attachment" in label:
            return "section section-evidence"
        if "participant" in label:
            return "section section-participants"
        return "section"
    if clean_line.startswith("[") and clean_line.endswith("]") and "UTC" not in clean_line:
        return "date"
    if re.match(r"^\[\d{2}:\d{2} UTC\]", clean_line):
        return "message"
    if clean_line.startswith(("Ticket", "Channel", "Owner", "Claimed", "Closed By", "Reason", "Opened", "Closed")):
        return "meta"
    if clean_line.startswith(("Attachments:", "Stickers:", "Reactions:")):
        return "label"
    if clean_line.startswith(("- ", "* ")):
        return "bullet"
    if clean_line == "End of transcript":
        return "end"
    return "text"


def build_colored_transcript_html(text_value: str, *, page_title: str = "STARZ Ticket Transcript") -> str:
    """Create a colored, browser-friendly transcript from the plain-text transcript."""
    safe_title = html.escape(clean_transcript_text(page_title) or "STARZ Ticket Transcript")
    rendered_lines: list[str] = []

    for raw_line in str(text_value or "").splitlines():
        css_class = transcript_html_line_class(raw_line)
        content = html_linkify_text(raw_line) if raw_line else "&nbsp;"
        rendered_lines.append(f'<div class="line {css_class}">{content}</div>')

    body = "\n".join(rendered_lines)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{
      --bg: #0f1117;
      --panel: #161922;
      --text: #e8eaf0;
      --muted: #a8adbb;
      --red: #ff4d4d;
      --blue: #67b7ff;
      --green: #70e39b;
      --purple: #c792ea;
      --gold: #ffd166;
      --line: #2c3140;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: linear-gradient(135deg, #0b0d12, #151922);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 28px 18px; }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-left: 6px solid var(--red);
      border-radius: 18px;
      box-shadow: 0 18px 60px rgba(0,0,0,.35);
      padding: 22px;
      overflow-x: auto;
    }}
    .line {{
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "JetBrains Mono", "Cascadia Code", "SFMono-Regular", Consolas, monospace;
      font-size: 14px;
      min-height: 1.35em;
    }}
    .title {{
      color: #ffffff;
      font-family: Inter, ui-sans-serif, system-ui, sans-serif;
      font-size: 28px;
      font-weight: 800;
      margin-bottom: 10px;
    }}
    .meta {{ color: var(--muted); }}
    .section {{
      color: var(--blue);
      font-family: Inter, ui-sans-serif, system-ui, sans-serif;
      font-size: 18px;
      font-weight: 800;
      margin-top: 18px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
      letter-spacing: .04em;
    }}
    .section-notes {{ color: var(--purple); }}
    .section-evidence {{ color: var(--gold); }}
    .section-participants {{ color: var(--green); }}
    .date {{ color: var(--gold); margin-top: 12px; font-weight: 700; }}
    .message {{ color: var(--blue); margin-top: 10px; font-weight: 700; }}
    .label {{ color: var(--purple); font-weight: 700; }}
    .bullet {{ color: var(--muted); }}
    .text {{ color: var(--text); }}
    .blank {{ min-height: .75em; }}
    .end {{
      margin-top: 20px;
      padding-top: 14px;
      color: var(--muted);
      border-top: 1px solid var(--line);
      font-style: italic;
    }}
    a {{ color: var(--green); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    @media (max-width: 640px) {{
      .wrap {{ padding: 14px 8px; }}
      .card {{ padding: 16px; border-radius: 14px; }}
      .title {{ font-size: 22px; }}
      .line {{ font-size: 12px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="card">
{body}
    </section>
  </main>
</body>
</html>"""


def collect_participant_counts(messages: list[discord.Message]) -> dict[int, dict[str, Any]]:
    participants: dict[int, dict[str, Any]] = {}
    for message in messages:
        if is_transcript_event_message(message):
            continue
        author = message.author
        user_id = int(getattr(author, "id", 0) or 0)
        if not user_id:
            continue
        item = participants.setdefault(
            user_id,
            {
                "name": clean_transcript_text(getattr(author, "display_name", None) or getattr(author, "name", None) or str(author)),
                "username": clean_transcript_text(getattr(author, "name", None) or str(author)),
                "messages": 0,
            },
        )
        item["messages"] = int(item.get("messages", 0)) + 1
    return participants


def merge_participant_counts(base: dict[int, dict[str, Any]], incoming: dict[int, dict[str, Any]], *, notes: bool = False) -> None:
    for user_id, item in incoming.items():
        target = base.setdefault(user_id, {"name": item.get("name", f"User {user_id}"), "username": item.get("username", ""), "messages": 0, "notes": 0})
        if notes:
            target["notes"] = int(target.get("notes", 0)) + int(item.get("messages", 0))
        else:
            target["messages"] = int(target.get("messages", 0)) + int(item.get("messages", 0))


def append_participants_section(
    lines: list[str],
    *,
    owner_id: Optional[int],
    ticket_messages: list[discord.Message],
    notes_messages_by_source: list[tuple[discord.abc.GuildChannel, list[discord.Message]]],
    user_lookup: Optional[dict[int, dict[str, str]]] = None,
) -> None:
    combined: dict[int, dict[str, Any]] = {}
    merge_participant_counts(combined, collect_participant_counts(ticket_messages), notes=False)
    for _, note_messages in notes_messages_by_source:
        merge_participant_counts(combined, collect_participant_counts(note_messages), notes=True)

    append_transcript_section(lines, "Participants")
    if not combined:
        lines.append("No non-bot participants were found.")
        lines.append("")
        return

    lines.append("Role        Messages  Notes  User")
    lines.append("----------  --------  -----  ----------------------------------------")
    for user_id, item in sorted(combined.items(), key=lambda entry: (0 if entry[0] == owner_id else 1, str(entry[1].get("name", "")).lower())):
        role = "Owner" if user_id == owner_id else "Staff/User"
        msg_count = int(item.get("messages", 0))
        note_count = int(item.get("notes", 0))
        lookup_entry = (user_lookup or {}).get(user_id, {})
        user_text = lookup_entry.get("participant")
        if not user_text:
            name = clean_transcript_text(item.get("name", f"User {user_id}")) or f"User {user_id}"
            username = clean_transcript_text(item.get("username", ""))
            user_text = f"{name}"
            if username and username.lower() != name.lower():
                user_text += f" (@{username})"
            user_text += f" - {user_id}"
        lines.append(f"{role:<10}  {msg_count:<8}  {note_count:<5}  {user_text}")
    lines.append("")


def append_attachment_index(
    lines: list[str],
    *,
    ticket_messages: list[discord.Message],
    notes_messages_by_source: list[tuple[discord.abc.GuildChannel, list[discord.Message]]],
    user_lookup: Optional[dict[int, dict[str, str]]] = None,
) -> None:
    items: list[tuple[str, discord.Message, Any]] = []
    for message in ticket_messages:
        for attachment in getattr(message, "attachments", []) or []:
            items.append(("Ticket Conversation", message, attachment))

    for notes_source, note_messages in notes_messages_by_source:
        source_label = f"Staff Notes: #{getattr(notes_source, 'name', 'notes')}"
        for message in note_messages:
            for attachment in getattr(message, "attachments", []) or []:
                items.append((source_label, message, attachment))

    append_transcript_section(lines, "Evidence / Attachments")
    if not items:
        lines.append("No attachments were posted in this ticket.")
        lines.append("")
        return

    for index, (source_label, message, attachment) in enumerate(items, start=1):
        created = message.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        filename = clean_transcript_text(getattr(attachment, "filename", "")) or "attachment"
        size = getattr(attachment, "size", 0) or 0
        size_text = f"{size:,} bytes" if size else "unknown size"
        lines.append(f"{index}. {filename} ({size_text})")
        lines.append(f"   Posted: {created} by {format_transcript_author(message, user_lookup)}")
        lines.append(f"   Source: {source_label}")
        lines.append(f"   Link  : {attachment.url}")
    lines.append("")


def audit_field_value(audit_text: str, label: str, default: str = "None") -> str:
    """Pull one value out of the audit text without printing the whole audit block."""
    pattern = rf"^{re.escape(label)}\s*:\s*(.*?)\s*$"
    match = re.search(pattern, audit_text or "", flags=re.MULTILINE)
    if not match:
        return default
    value = clean_transcript_text(match.group(1))
    return value or default


def has_transcript_attachments(
    ticket_messages: list[discord.Message],
    notes_messages_by_source: list[tuple[discord.abc.GuildChannel, list[discord.Message]]],
) -> bool:
    for message in ticket_messages:
        if getattr(message, "attachments", None):
            return True
    for _, note_messages in notes_messages_by_source:
        for message in note_messages:
            if getattr(message, "attachments", None):
                return True
    return False


def participant_count(
    ticket_messages: list[discord.Message],
    notes_messages_by_source: list[tuple[discord.abc.GuildChannel, list[discord.Message]]],
) -> int:
    ids: set[int] = set()
    for message in ticket_messages:
        if not is_transcript_event_message(message):
            user_id = int(getattr(message.author, "id", 0) or 0)
            if user_id:
                ids.add(user_id)
    for _, note_messages in notes_messages_by_source:
        for message in note_messages:
            if not is_transcript_event_message(message):
                user_id = int(getattr(message.author, "id", 0) or 0)
                if user_id:
                    ids.add(user_id)
    return len(ids)


def append_compact_ticket_header(
    lines: list[str],
    *,
    channel: discord.TextChannel,
    ticket_number: str,
    ticket_kind: str,
    owner_id: Optional[int],
    claimed_by: Optional[int],
    audit_text: str,
    generated_at: str,
    user_lookup: Optional[dict[int, dict[str, str]]] = None,
) -> None:
    """Single source of ticket metadata so transcripts do not repeat themselves."""
    closed_by = format_transcript_user_value(channel.guild, audit_field_value(audit_text, "Closed By"), user_lookup)
    opened_at = audit_field_value(audit_text, "Opened At")
    closed_at = audit_field_value(audit_text, "Closed At")
    close_reason = audit_field_value(audit_text, "Close Reason")

    lines.append(f"STARZ Ticket #{ticket_number} Transcript")
    lines.append("")
    lines.append(f"Ticket   : #{ticket_number} - {ticket_kind}")
    lines.append(f"Channel  : #{clean_transcript_text(channel.name)} ({channel.id})")
    lines.append(f"Owner    : {format_transcript_user_reference(channel.guild, owner_id, user_lookup)}")
    lines.append(f"Claimed  : {format_transcript_user_reference(channel.guild, claimed_by, user_lookup)}")
    lines.append(f"Closed By: {closed_by}")
    lines.append(f"Reason   : {close_reason}")
    if opened_at != "None":
        lines.append(f"Opened   : {opened_at}")
    if closed_at != "None":
        lines.append(f"Closed   : {closed_at}")
    lines.append("")


async def build_ticket_and_notes_transcript_text(
    channel: discord.TextChannel,
    notes_thread: Optional[discord.abc.GuildChannel] = None,
    audit_text: str = "",
    extra_notes_channels: Optional[list[discord.abc.GuildChannel]] = None,
) -> str:
    lines: list[str] = []
    ticket_number = get_ticket_number(channel)
    ticket_kind = get_ticket_kind(channel).title()
    owner_id = get_ticket_owner_id(channel)
    claimed_by = get_claimed_by_id(channel)
    generated_at = now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")

    notes_sources: list[discord.abc.GuildChannel] = []
    if notes_thread is not None:
        notes_sources.append(notes_thread)
    if extra_notes_channels:
        for notes_channel in extra_notes_channels:
            if notes_channel is not None and notes_channel not in notes_sources:
                notes_sources.append(notes_channel)

    try:
        ticket_messages = await fetch_history_messages(channel)
    except discord.Forbidden:
        ticket_messages = []
        ticket_read_error = "Unable to read the ticket conversation because the bot is missing read permissions."
    except discord.HTTPException:
        ticket_messages = []
        ticket_read_error = "Unable to read the ticket conversation from Discord."
    else:
        ticket_read_error = ""

    notes_messages_by_source: list[tuple[discord.abc.GuildChannel, list[discord.Message]]] = []
    notes_read_errors: list[str] = []
    for notes_source in notes_sources:
        try:
            note_messages = await fetch_history_messages(notes_source)
        except discord.Forbidden:
            note_messages = []
            notes_read_errors.append(f"Unable to read staff notes from #{getattr(notes_source, 'name', 'notes')} because the bot is missing read permissions.")
        except discord.HTTPException:
            note_messages = []
            notes_read_errors.append(f"Unable to read staff notes from #{getattr(notes_source, 'name', 'notes')} from Discord.")
        notes_messages_by_source.append((notes_source, note_messages))

    all_transcript_messages: list[discord.Message] = list(ticket_messages)
    for _, note_messages in notes_messages_by_source:
        all_transcript_messages.extend(note_messages)

    lookup_ids: set[int] = set()
    for possible_id in (owner_id, claimed_by, extract_id(audit_field_value(audit_text, "Closed By"))):
        if possible_id:
            lookup_ids.add(int(possible_id))

    user_lookup = await build_transcript_user_lookup(channel.guild, lookup_ids, all_transcript_messages)

    ticket_events = [message for message in ticket_messages if is_transcript_event_message(message)]
    ticket_conversation = [message for message in ticket_messages if not is_transcript_event_message(message)]
    visible_notes = [
        (notes_source, [message for message in note_messages if not is_transcript_event_message(message)])
        for notes_source, note_messages in notes_messages_by_source
    ]
    has_notes = any(note_messages for _, note_messages in visible_notes) or bool(notes_read_errors)

    append_compact_ticket_header(
        lines,
        channel=channel,
        ticket_number=ticket_number,
        ticket_kind=ticket_kind,
        owner_id=owner_id,
        claimed_by=claimed_by,
        audit_text=audit_text,
        generated_at=generated_at,
        user_lookup=user_lookup,
    )

    # Show participants only when it helps review the ticket. For one-person test
    # tickets this section just repeats the header, so it is skipped.
    if participant_count(ticket_messages, notes_messages_by_source) > 1:
        append_participants_section(
            lines,
            owner_id=owner_id,
            ticket_messages=ticket_messages,
            notes_messages_by_source=notes_messages_by_source,
            user_lookup=user_lookup,
        )

    # Keep bot/open/claim/ping events visible, but separated so they do not clutter
    # the player/staff conversation. Set TRANSCRIPT_INCLUDE_BOT_EVENTS=false only
    # if you want future transcripts to hide these ticket event messages.
    if TRANSCRIPT_INCLUDE_BOT_EVENTS and ticket_events:
        append_transcript_section(lines, "Ticket Events")
        append_messages_grouped_by_day(
            lines,
            ticket_events,
            empty_text="No ticket event messages were found.",
            compact_embeds=True,
            user_lookup=user_lookup,
        )

    append_transcript_section(lines, "Conversation")
    if ticket_read_error:
        lines.append(ticket_read_error)
        lines.append("")
    else:
        append_messages_grouped_by_day(
            lines,
            ticket_conversation,
            empty_text="No user/staff conversation messages were found.",
            compact_embeds=True,
            user_lookup=user_lookup,
        )

    if has_notes:
        append_transcript_section(lines, "Staff Notes")
        for error in notes_read_errors:
            lines.append(error)
        if notes_read_errors:
            lines.append("")

        for notes_source, staff_note_messages in visible_notes:
            if not staff_note_messages:
                continue
            if isinstance(notes_source, discord.Thread):
                source_type = "Private Thread" if is_private_notes_thread(notes_source) else "Public Thread"
            else:
                source_type = "Visible Notes Channel"
            append_transcript_subsection(lines, f"{source_type}: #{clean_transcript_text(getattr(notes_source, 'name', 'notes'))} ({notes_source.id})")
            append_messages_grouped_by_day(
                lines,
                staff_note_messages,
                empty_text="No written staff notes were posted in this notes source.",
                compact_embeds=True,
                user_lookup=user_lookup,
            )

    if has_transcript_attachments(ticket_messages, notes_messages_by_source):
        append_attachment_index(
            lines,
            ticket_messages=ticket_messages,
            notes_messages_by_source=notes_messages_by_source,
            user_lookup=user_lookup,
        )

    lines.append("End of transcript")
    return "\n".join(lines)


async def build_transcript_file(channel: discord.TextChannel) -> discord.File:
    text_value = await build_ticket_and_notes_transcript_text(channel, None)
    return transcript_file_from_text(text_value, f"{channel.name}-transcript.txt")


def save_ticket_log_text(guild_id: int, ticket_id: str, transcript_text: str) -> Path:
    path = get_ticket_log_path(guild_id, ticket_id)
    path.write_text(transcript_text, encoding="utf-8")
    return path


def notes_source_belongs_to_ticket(notes_source: Any, ticket_channel: discord.TextChannel) -> bool:
    """Return True when a staff-notes source belongs to this exact ticket.

    Current notes are Discord threads attached to the ticket channel. Legacy
    visible notes text channels from older patches are still recognized so they
    can be included in transcripts and migrated away from on the next /snotes.
    """
    if notes_source is None:
        return False

    if isinstance(notes_source, discord.Thread):
        parent_id = getattr(notes_source, "parent_id", None)
        if parent_id == ticket_channel.id:
            return True

        parent = getattr(notes_source, "parent", None)
        return getattr(parent, "id", None) == ticket_channel.id

    # Legacy visible notes text channels only. New notes should not be created
    # as standalone channels anymore.
    if isinstance(notes_source, discord.TextChannel):
        if notes_source.id == ticket_channel.id:
            return False

        topic = notes_source.topic or ""
        if f"staff_notes_for:{ticket_channel.id}" in topic:
            return True

        expected_name = notes_channel_name(ticket_channel)
        same_category = getattr(notes_source.category, "id", None) == getattr(ticket_channel.category, "id", None)
        return bool(same_category and notes_source.name == expected_name)

    return False


def notes_thread_belongs_to_ticket(notes_channel: discord.abc.GuildChannel, ticket_channel: discord.TextChannel) -> bool:
    """Backward-compatible alias for notes-source checks."""
    return notes_source_belongs_to_ticket(notes_channel, ticket_channel)


def is_private_notes_thread(thread: Any) -> bool:
    return isinstance(thread, discord.Thread) and getattr(thread, "type", None) == discord.ChannelType.private_thread


def is_public_notes_thread(thread: Any) -> bool:
    return isinstance(thread, discord.Thread) and getattr(thread, "type", None) == discord.ChannelType.public_thread


async def notes_thread_should_be_private(
    bot: "TicketBot",
    guild: discord.Guild,
    ticket_channel: discord.TextChannel,
) -> bool:
    """Staff notes must always use private attached threads.

    Discord public threads inherit the parent ticket channel visibility, which
    means a ticket opener who can view their ticket can also discover/read the
    public notes thread. To keep notes attached under the ticket while keeping
    them staff-only, always use a private thread and add staff as participants.
    """
    return True


async def find_cached_notes_thread_for_ticket(
    guild: discord.Guild,
    channel: discord.TextChannel,
) -> Optional[Any]:
    """Reconnect to an existing private notes thread for this ticket.

    Staff notes should be private threads attached to the ticket channel. Legacy
    visible text-channel notes and older public notes threads are intentionally
    not returned here so /snotes can create the proper private attached thread
    and update the mapping.
    """
    expected_name = notes_channel_name(channel)
    thread_candidates: list[discord.Thread] = []
    channel_threads = getattr(channel, "threads", []) or []
    guild_threads = getattr(guild, "threads", []) or []

    for thread in list(channel_threads) + list(guild_threads):
        if not isinstance(thread, discord.Thread):
            continue
        if not is_private_notes_thread(thread):
            continue
        if not notes_source_belongs_to_ticket(thread, channel):
            continue
        if thread.name == expected_name or thread.name.startswith(f"{expected_name}-"):
            thread_candidates.append(thread)

    if not thread_candidates:
        return None

    # Prefer active/unarchived threads, then newest.
    thread_candidates.sort(
        key=lambda thread: (
            0 if getattr(thread, "archived", False) else 1,
            getattr(thread, "created_at", None) or now_utc(),
        ),
        reverse=True,
    )
    return thread_candidates[0]


async def get_notes_thread(
    bot: "TicketBot",
    guild: discord.Guild,
    channel: discord.TextChannel,
) -> Optional[Any]:
    """Return this ticket's mapped staff-notes thread.

    The config key name is kept for compatibility. If it points at a legacy
    visible notes text channel, the mapping is cleared so a proper attached
    thread can be created on the next /snotes.
    """
    notes_id = get_notes_thread_id(bot, guild.id, channel.id)
    if notes_id:
        notes_channel: Optional[Any] = guild.get_thread(notes_id)
        if notes_channel is None:
            notes_channel = guild.get_channel(notes_id)

        if notes_channel is None:
            try:
                notes_channel = await bot.fetch_channel(notes_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                notes_channel = None

        if (
            isinstance(notes_channel, discord.Thread)
            and is_private_notes_thread(notes_channel)
            and notes_source_belongs_to_ticket(notes_channel, channel)
        ):
            return notes_channel

        # Mapped public threads and mapped text channels are legacy notes
        # destinations from older patches. Do not keep returning them as the
        # live staff-notes destination. /snotes will create/find a private
        # attached thread under the ticket and update this mapping.
        clear_notes_thread_id(bot, guild.id, channel.id)

    cached = await find_cached_notes_thread_for_ticket(guild, channel)
    if cached is not None:
        set_notes_thread_id(bot, guild.id, channel.id, cached.id)
        return cached

    return None



def legacy_notes_channel_belongs_to_ticket(notes_channel: discord.abc.GuildChannel, ticket_channel: discord.TextChannel) -> bool:
    """Return True for legacy visible staff-notes text channels linked to this ticket.

    New notes are no longer standalone text channels. Legacy channels are kept
    detectable for transcript inclusion and manual cleanup.
    """
    return isinstance(notes_channel, discord.TextChannel) and notes_source_belongs_to_ticket(notes_channel, ticket_channel)


def find_legacy_notes_channels_for_ticket(
    guild: discord.Guild,
    ticket_channel: discord.TextChannel,
) -> list[discord.abc.GuildChannel]:
    """Find old visible notes text channels for this ticket.

    The function name is kept for compatibility with the close/transcript flow.
    """
    matches: list[discord.abc.GuildChannel] = []
    seen: set[int] = set()
    for candidate in guild.text_channels:
        if legacy_notes_channel_belongs_to_ticket(candidate, ticket_channel) and candidate.id not in seen:
            seen.add(candidate.id)
            matches.append(candidate)

    matches.sort(key=lambda channel: getattr(channel, "created_at", None) or now_utc())
    return matches


def resolve_ticket_channel_from_context(
    guild: discord.Guild,
    channel: Optional[discord.abc.Messageable],
) -> Optional[discord.TextChannel]:
    """Resolve the ticket text channel from a ticket channel, notes thread, or old notes channel."""
    if isinstance(channel, discord.TextChannel) and is_ticket_channel(channel):
        return channel

    if isinstance(channel, discord.Thread):
        parent = channel.parent
        if parent is None:
            parent_id = getattr(channel, "parent_id", None)
            if parent_id:
                parent = guild.get_channel(int(parent_id))
        if isinstance(parent, discord.TextChannel) and is_ticket_channel(parent):
            return parent

    # Legacy fallback notes channels used a staff_notes_for:<ticket_channel_id> topic.
    # Let shortcut commands still find the real ticket from those old channels.
    if isinstance(channel, discord.TextChannel):
        match = re.search(r"staff_notes_for:(\d{15,25})", channel.topic or "")
        if match:
            ticket_channel = guild.get_channel(int(match.group(1)))
            if isinstance(ticket_channel, discord.TextChannel) and is_ticket_channel(ticket_channel):
                return ticket_channel

    return None


def format_notes_sources_for_embed(
    notes_thread: Optional[discord.abc.GuildChannel],
    legacy_notes_channels: Optional[list[discord.TextChannel]] = None,
) -> str:
    sources: list[str] = []

    def source_label(source: discord.abc.GuildChannel, fallback: str = "notes") -> str:
        name = clean_transcript_text(getattr(source, "name", "") or fallback)
        source_id = int(getattr(source, "id", 0) or 0)
        if source_id:
            return f"#{name} (`{source_id}`)"
        return f"#{name}"

    if notes_thread is not None:
        sources.append(source_label(notes_thread, "notes-thread"))
    for notes_channel in legacy_notes_channels or []:
        sources.append(source_label(notes_channel, "legacy-notes-channel"))

    if not sources:
        return "No staff notes were linked."
    return truncate("Included from " + ", ".join(sources), 1024)


async def ensure_notes_participant(notes_channel, member: discord.Member) -> None:
    """Add a staff member to a private staff-notes thread when needed."""
    if is_private_notes_thread(notes_channel):
        try:
            await notes_channel.add_user(member)
        except (discord.Forbidden, discord.HTTPException):
            pass


async def remove_notes_participant(notes_channel, member: discord.Member) -> None:
    """Remove a member from a private staff-notes thread when ticket access is removed."""
    if is_private_notes_thread(notes_channel):
        try:
            await notes_channel.remove_user(member)
        except (discord.Forbidden, discord.HTTPException):
            pass


def cached_ticket_staff_members_for_notes(
    bot: "TicketBot",
    guild: discord.Guild,
    ticket_channel: discord.TextChannel,
    *,
    extra_members: Optional[list[discord.Member]] = None,
) -> list[discord.Member]:
    """Return cached staff members that should be added to this ticket's notes thread.

    Private threads do not grant role membership automatically. This helper adds
    cached configured staff while keeping the ticket owner/player out of notes.
    """
    owner_id = get_ticket_owner_id(ticket_channel) or 0
    bot_id = int(getattr(getattr(guild, "me", None), "id", 0) or 0)
    configured_staff_role_ids = set(get_role_ids_from_config(bot, guild.id, "normal_staff")) | set(get_role_ids_from_config(bot, guild.id, "priority_staff"))
    members: list[discord.Member] = []
    seen: set[int] = set()

    def add_member(member: Optional[discord.Member]) -> None:
        if member is None:
            return
        if member.bot or member.id in {owner_id, bot_id} or member.id in seen:
            return
        member_role_ids = {role.id for role in getattr(member, "roles", [])}
        if not (
            (configured_staff_role_ids & member_role_ids)
            or member.guild_permissions.manage_channels
            or member.guild_permissions.administrator
        ):
            return
        seen.add(member.id)
        members.append(member)

    for member in extra_members or []:
        add_member(member)

    for target, overwrite in ticket_channel.overwrites.items():
        if isinstance(target, discord.Role) and not target.is_default() and overwrite.view_channel is True:
            for member in getattr(target, "members", []):
                add_member(member)
        elif isinstance(target, discord.Member) and overwrite.view_channel is True:
            add_member(target)

    for role in unique_roles(get_staff_roles(bot, guild), get_priority_staff_roles(bot, guild)):
        for member in getattr(role, "members", []):
            add_member(member)

    return members


async def sync_notes_thread_staff(
    bot: "TicketBot",
    ticket_channel: discord.TextChannel,
    notes_thread: Optional[discord.Thread],
    *,
    extra_members: Optional[list[discord.Member]] = None,
) -> int:
    """Add all currently known ticket staff to a private staff-notes thread."""
    if notes_thread is None or not is_private_notes_thread(notes_thread):
        return 0

    # Make the thread active/visible for invited staff where Discord allows it.
    try:
        await notes_thread.join()
    except (discord.Forbidden, discord.HTTPException, AttributeError):
        pass

    if getattr(notes_thread, "archived", False):
        try:
            await notes_thread.edit(archived=False, locked=False, reason="Re-open staff notes thread for active ticket.")
        except TypeError:
            try:
                await notes_thread.edit(archived=False, reason="Re-open staff notes thread for active ticket.")
            except (discord.Forbidden, discord.HTTPException):
                pass
        except (discord.Forbidden, discord.HTTPException):
            pass

    staff_members = cached_ticket_staff_members_for_notes(
        bot,
        ticket_channel.guild,
        ticket_channel,
        extra_members=extra_members,
    )
    added = 0
    for member in staff_members:
        before = {thread_member.id for thread_member in getattr(notes_thread, "members", [])}
        await ensure_notes_participant(notes_thread, member)
        if member.id not in before:
            added += 1
    return added


async def archive_lock_notes_thread(notes_thread: Optional[discord.abc.GuildChannel], *, reason: str) -> None:
    """Best-effort lock/archive for notes threads during ticket close."""
    if not isinstance(notes_thread, discord.Thread):
        return

    try:
        await notes_thread.edit(archived=True, locked=True, reason=reason)
    except TypeError:
        try:
            await notes_thread.edit(archived=True, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            pass
    except (discord.Forbidden, discord.HTTPException):
        try:
            await notes_thread.edit(archived=True, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            pass


async def create_ticket_notes_thread(
    bot: "TicketBot",
    channel: discord.TextChannel,
    creator: discord.Member,
    *,
    private_thread: bool,
) -> discord.Thread:
    clean_notes_name = notes_channel_name(channel)
    thread_type = discord.ChannelType.private_thread if private_thread else discord.ChannelType.public_thread
    reason = f"Staff notes thread created by {creator} ({creator.id}) for ticket {channel.id}"

    kwargs: dict[str, Any] = {
        "name": clean_notes_name[:100],
        "type": thread_type,
        "reason": reason,
    }
    if private_thread:
        kwargs["invitable"] = False

    try:
        return await channel.create_thread(**kwargs)
    except TypeError:
        kwargs.pop("invitable", None)
        return await channel.create_thread(**kwargs)


async def create_or_get_notes_thread(
    bot: "TicketBot",
    guild: discord.Guild,
    channel: discord.TextChannel,
    creator: discord.Member,
) -> tuple[Optional[Any], str]:
    """Create or open this ticket's staff notes as a private attached thread.

    Private threads stay attached under the parent ticket channel for invited
    staff, while keeping the ticket opener out of staff notes. The bot should
    never create separate visible notes text channels or public notes threads as
    the live notes destination.
    """
    existing = await get_notes_thread(bot, guild, channel)
    clean_notes_name = notes_channel_name(channel)

    if isinstance(existing, discord.Thread):
        await safe_edit_notes_name(existing, clean_notes_name, reason="Normalize staff notes thread name.")
        await ensure_notes_participant(existing, creator)
        await sync_notes_thread_staff(bot, channel, existing, extra_members=[creator])
        return existing, "existing"

    legacy_notes_channels = find_legacy_notes_channels_for_ticket(guild, channel)
    private_thread_required = await notes_thread_should_be_private(bot, guild, channel)

    try:
        notes_thread = await create_ticket_notes_thread(
            bot,
            channel,
            creator,
            private_thread=True,
        )
    except discord.Forbidden:
        return None, (
            f"I could not create the private staff-notes thread under {channel.mention}.\n\n"
            "Give the bot **Create Private Threads**, **Send Messages in Threads**, **View Channel**, and **Manage Threads** in this ticket/category. "
            "I did not change any role, category, or global permissions."
        )
    except discord.HTTPException as exc:
        return None, f"Discord refused to create the staff-notes thread: `{truncate(str(exc), 500)}`"

    if not notes_source_belongs_to_ticket(notes_thread, channel):
        try:
            await notes_thread.edit(archived=True, locked=True, reason="Staff notes thread did not link to the correct ticket.")
        except (discord.Forbidden, discord.HTTPException, TypeError):
            pass
        clear_notes_thread_id(bot, guild.id, channel.id)
        return None, "Discord created a notes thread, but it was not linked to this ticket. I stopped to prevent mixed ticket notes."

    set_notes_thread_id(bot, guild.id, channel.id, notes_thread.id)

    await ensure_notes_participant(notes_thread, creator)
    await sync_notes_thread_staff(bot, channel, notes_thread, extra_members=[creator])

    visibility_note = (
        "This is a **private staff-only thread** attached under the ticket channel. "
        "Private threads are required because public threads can be seen by anyone who can view the parent ticket channel."
    )

    legacy_notice = ""
    if legacy_notes_channels:
        legacy_notice = (
            "\n\nLegacy visible notes channel(s) were found for this ticket: "
            + ", ".join(notes_channel.mention for notes_channel in legacy_notes_channels)
            + "\nThey were left untouched, but will still be included in this ticket's transcript."
        )

    try:
        await notes_thread.send(
            "📝 **Staff Notes**\n"
            f"Linked ticket: {channel.mention} (`{channel.id}`)\n"
            f"Ticket number: `{get_ticket_number(channel)}`\n\n"
            "Use this attached notes thread for staff-only notes. These notes are appended to this ticket transcript when the ticket closes.\n"
            f"{visibility_note} The bot did not edit any Discord role, category, or global permissions."
            f"{legacy_notice}"
        )
    except discord.Forbidden:
        return None, (
            f"I created {notes_thread.mention}, but I cannot send messages there. "
            "Fix the bot's thread permissions, then run `/snotes` again."
        )
    except discord.HTTPException as exc:
        return None, f"I created {notes_thread.mention}, but Discord rejected the starter message: `{truncate(str(exc), 500)}`"

    return notes_thread, "created"


async def create_ticket_channel(
    bot: "TicketBot",
    interaction: discord.Interaction,
    *,
    priority: bool = False,
) -> tuple[Optional[discord.TextChannel], str]:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return None, "This can only be used inside a server."

    if not bot.config_store.is_ready(interaction.guild.id):
        return None, "This server is not configured yet. An admin should run /ticketsetup first."

    if priority and not member_can_open_priority(bot, interaction.user):
        allowed_roles = get_priority_allowed_roles(bot, interaction.guild)
        role_list = ", ".join(role.mention for role in allowed_roles) if allowed_roles else "No priority opener roles are configured yet."
        return None, f"You do not have the required role to open a priority ticket.\nRequired role(s): {role_list}"

    category = await get_ticket_category(bot, interaction.guild, priority=priority)
    if category is None:
        if priority:
            return None, "No valid priority or normal ticket category exists. Run `/ticketsetup` first, then optionally `/ticketprioritycategory`."
        return None, "The configured normal ticket category no longer exists. Run `/ticketsetup` again."

    lookup_categories = await get_ticket_lookup_categories(bot, interaction.guild)
    existing = find_open_ticket_for_user(lookup_categories or [category], interaction.user.id)
    if existing:
        return existing, f"You already have an open ticket: {existing.mention}"

    guild = interaction.guild
    ticket_type = "priority" if priority else "normal"
    channel_prefix = PRIORITY_TICKET_CONFIG["channel_prefix"] if priority else TICKET_CONFIG["channel_prefix"]
    ticket_number = bot.config_store.allocate_ticket_number(guild.id)
    channel_name = f"{channel_prefix}-{ticket_number}"[:95]

    if priority:
        visible_roles = get_priority_staff_roles(bot, guild)
        configured_ping_roles = get_priority_ping_roles(bot, guild)
    else:
        visible_roles = get_staff_roles(bot, guild)
        configured_ping_roles = get_guild_ping_roles(bot, guild)

    # Roles that should be notified when the ticket opens.
    # If no dedicated ping role is configured, fall back to the ticket access roles
    # so staff still gets alerted instead of silently creating an unannounced ticket.
    ping_roles = unique_roles(configured_ping_roles) if configured_ping_roles else unique_roles(visible_roles)
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
            create_private_threads=True,
            send_messages_in_threads=True,
            mention_everyone=True,
            embed_links=True,
            attach_files=True,
        )

    # Give both access roles and ping roles permission to see/respond in the ticket.
    # Some servers keep ping roles separate from access roles; if the role cannot
    # view the private channel, Discord may display the role text but not notify
    # the staff team reliably.
    ticket_role_overwrites = unique_roles(visible_roles, ping_roles)

    for role in ticket_role_overwrites:
        # Keep low-level and upper staff permission tiers separate.
        # Ticket access should only grant ticket visibility/basic chat access;
        # elevated moderation/thread powers must come from the role's normal
        # Discord permissions or a higher-level role, not from this overwrite.
        overwrites[role] = ticket_role_access_overwrite()

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            topic=build_ticket_topic(
                owner_id=interaction.user.id,
                ticket_type=ticket_type,
                ticket_number=ticket_number,
                ping_role_ids=ping_role_ids,
                owner_label=format_member_topic_reference(interaction.user),
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

    embed = build_ticket_status_embed(
        guild=guild,
        ticket_number=ticket_number,
        ticket_type=ticket_type,
        owner_id=interaction.user.id,
        claimed_by=None,
        status="Open",
        created_by_text=format_member_reference(interaction.user),
    )

    opening_mentions = mention_roles(ping_roles) if ping_roles else ""

    try:
        await channel.send(
            embed=embed,
            view=TicketChannelView(bot),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.Forbidden:
        return None, "I created the channel but couldn't post the starter message. Check the category/channel permissions."
    except discord.HTTPException:
        return None, "I created the channel but Discord rejected the starter message."

    if ping_roles and opening_mentions:
        staff_alert = build_ticket_event_embed(
            title="🎫 New Ticket Opened",
            description=(
                f"A **{ticket_type.title()}** ticket was opened: {channel.mention}\n"
                f"**Ticket #:** `{ticket_number}`\n"
                f"**Opened by:** {interaction.user.mention} (`{interaction.user.id}`)\n\n"
                "Staff were pinged in the plain role-mention message above this embed."
            ),
            color=ticket_status_color(ticket_type, status="attention"),
        )
        try:
            # Let Discord finish applying private-channel overwrites before the role ping fires.
            await asyncio.sleep(3.0)

            ping_sent, ping_warning = await send_role_ping_message(
                channel,
                ping_roles,
                content_prefix="📣 New ticket opened:",
                embed=staff_alert,
                reason=f"Ticket #{ticket_number} opened staff ping",
            )
            if ping_warning:
                await channel.send(f"⚠️ {ping_warning}", allowed_mentions=discord.AllowedMentions.none())
            if not ping_sent:
                await channel.send(
                    "⚠️ Staff role ping failed on ticket open. Use **Ping Team** for now and run `/ticketmentioncheck test_ping:true`.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except discord.HTTPException as exc:
            # The ticket itself was created successfully; leave a visible warning
            # instead of silently hiding a broken staff-ping problem.
            try:
                await channel.send(
                    f"⚠️ Staff role ping failed on ticket open. Use **Ping Team** for now. Error: `{truncate(str(exc), 300)}`",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                pass

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
        notes_thread = await get_notes_thread(self.bot, interaction.guild, self.channel)
        if notes_thread is not None:
            await sync_notes_thread_staff(self.bot, self.channel, notes_thread, extra_members=[member])
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
        notes_thread = await get_notes_thread(self.bot, interaction.guild, self.channel)
        if notes_thread is not None and isinstance(notes_thread, discord.Thread):
            await remove_notes_participant(notes_thread, member)
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

        overwrite = ticket_role_access_overwrite()

        await self.channel.set_permissions(role, overwrite=overwrite, reason=f"Role added to ticket by {interaction.user}")
        notes_thread = await get_notes_thread(self.bot, interaction.guild, self.channel)
        if notes_thread is not None:
            await sync_notes_thread_staff(self.bot, self.channel, notes_thread)
        ping_sent, ping_warning = await send_role_ping_message(
            self.channel,
            [role],
            content_prefix="🔓 Added to this ticket:",
            reason=f"Ticket #{get_ticket_number(self.channel)} role added",
        )
        response = f"Added {role.mention} to this ticket."
        if ping_warning:
            response += f"\n\nWarning: {ping_warning}"
        await interaction.response.send_message(response, ephemeral=True)


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

        current_ping_role_ids = [role_id for role_id in get_ticket_ping_role_ids(self.channel) if role_id != role.id]
        await update_ticket_metadata(self.channel, ping_role_ids=current_ping_role_ids)

        # Remove the ticket-specific channel overwrite completely. The old code
        # changed the overwrite to explicit denies, which left the role sitting
        # in Discord's channel-permissions list and made it look like the role
        # could not be removed from the ticket.
        await self.channel.set_permissions(role, overwrite=None, reason=f"Role removed from ticket by {interaction.user}")

        notes_thread = await get_notes_thread(self.bot, interaction.guild, self.channel)
        if notes_thread is not None and isinstance(notes_thread, discord.Thread):
            for member in getattr(role, "members", []):
                # Do not kick global/default staff out of notes just because a
                # ticket-specific role was removed.
                configured_staff_role_ids = set(get_role_ids_from_config(self.bot, interaction.guild.id, "normal_staff")) | set(get_role_ids_from_config(self.bot, interaction.guild.id, "priority_staff"))
                member_role_ids = {member_role.id for member_role in getattr(member, "roles", [])}
                if not ((configured_staff_role_ids & member_role_ids) or member.guild_permissions.manage_channels or member.guild_permissions.administrator):
                    await remove_notes_participant(notes_thread, member)

        await self.channel.send(f"🔒 Removed {role.mention} from this ticket's channel permissions.")
        await interaction.response.send_message(
            f"Removed the ticket-specific channel permission for {role.mention}. If the role still sees the ticket, it is inheriting access from the ticket category or from a default ticket access role.",
            ephemeral=True,
        )


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
            ping_sent, ping_warning = await send_role_ping_message(
                self.channel,
                valid_roles,
                content_prefix=f"📣 Ticket ping roles updated by {interaction.user.mention}:",
                reason=f"Ticket #{get_ticket_number(self.channel)} ping roles updated",
            )
            response = f"Updated ticket ping roles: {mentions}"
            if ping_warning:
                response += f"\n\nWarning: {ping_warning}"
            await interaction.response.send_message(response, ephemeral=True)
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
            priority_ticket_category_id=current.get("priority_ticket_category_id"),
            log_channel_id=current.get("log_channel_id"),
            staff_role_ids=current.get("staff_role_ids", []),
            ping_role_ids=current.get("ping_role_ids", []),
            panel_gif_url=cleaned,
        )

        if cleaned:
            await interaction.response.send_message(f"Saved the panel image URL:\n{cleaned}", ephemeral=True)
        else:
            await interaction.response.send_message("Cleared the saved panel image URL.", ephemeral=True)


class TicketPanelMessageModal(discord.ui.Modal):
    def __init__(self, bot: "TicketBot", guild_id: int):
        super().__init__(title="Set Ticket Panel Message", timeout=600)
        self.bot = bot
        current = get_ticket_panel_message_config(bot, guild_id)

        self.title_input = discord.ui.TextInput(
            label="Embed title",
            placeholder=DEFAULT_TICKET_PANEL_TITLE,
            default=current["title"],
            required=False,
            max_length=256,
        )
        self.author_input = discord.ui.TextInput(
            label="Embed author/header",
            placeholder=DEFAULT_TICKET_PANEL_AUTHOR,
            default=current["author"],
            required=False,
            max_length=256,
        )
        self.description_input = discord.ui.TextInput(
            label="Embed message/body",
            placeholder="Paste the full ticket panel message for this server.",
            default=current["description"],
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=4000,
        )
        self.footer_input = discord.ui.TextInput(
            label="Embed footer",
            placeholder=DEFAULT_TICKET_PANEL_FOOTER,
            default=current["footer"],
            required=False,
            max_length=2048,
        )
        self.color_input = discord.ui.TextInput(
            label="Embed color hex",
            placeholder=DEFAULT_TICKET_PANEL_COLOR_HEX,
            default=current["color_hex"],
            required=False,
            max_length=20,
        )

        self.add_item(self.title_input)
        self.add_item(self.author_input)
        self.add_item(self.description_input)
        self.add_item(self.footer_input)
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        color_hex = normalize_panel_color_hex(str(self.color_input.value))
        self.bot.config_store.update_guild(
            interaction.guild.id,
            panel_title=normalize_panel_text(str(self.title_input.value), DEFAULT_TICKET_PANEL_TITLE, 256),
            panel_author=normalize_panel_text(str(self.author_input.value), DEFAULT_TICKET_PANEL_AUTHOR, 256),
            panel_description=normalize_panel_text(str(self.description_input.value), DEFAULT_TICKET_PANEL_DESCRIPTION, 4096),
            panel_footer=normalize_panel_text(str(self.footer_input.value), DEFAULT_TICKET_PANEL_FOOTER, 2048),
            panel_color_hex=color_hex,
        )

        preview = build_ticket_panel_embed(self.bot, interaction.guild)
        await interaction.response.send_message(
            "Saved the ticket panel embed message for this server. New `/ticketpanel` posts will use this text.",
            embed=preview,
            ephemeral=True,
        )


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
        allowed_to_close, close_denial = can_member_close_ticket(self.bot, interaction.user, self.channel)
        if not allowed_to_close:
            await interaction.response.send_message(close_denial, ephemeral=True)
            return

        closing_ticket_ids = getattr(self.bot, "closing_ticket_ids", set())
        if self.channel.id in closing_ticket_ids:
            await interaction.response.send_message(
                "This ticket is already closing. Please wait for the current close/transcript task to finish.",
                ephemeral=True,
            )
            return

        closing_ticket_ids.add(self.channel.id)
        self.bot.closing_ticket_ids = closing_ticket_ids

        reason_text = str(self.reason.value).strip() or "No reason provided."
        await interaction.response.send_message("Saving transcript and closing this ticket...", ephemeral=True)

        claimed_by = get_claimed_by_id(self.channel) or 0
        ping_role_ids = get_ticket_ping_role_ids(self.channel)
        ticket_log_id = get_ticket_log_id(self.channel)
        ticket_number = get_ticket_number(self.channel)
        ticket_kind = get_ticket_kind(self.channel)

        async def safe_followup(message: str) -> None:
            try:
                await interaction.followup.send(message, ephemeral=True)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        async def reopen_ticket_metadata() -> None:
            try:
                await update_ticket_metadata(
                    self.channel,
                    status="open",
                    claimed_by=claimed_by,
                    ping_role_ids=ping_role_ids,
                    ticket_number=ticket_number,
                    ticket_type=ticket_kind,
                )
            except discord.HTTPException:
                pass

        def safe_filename_part(value: Any, limit: int = 80) -> str:
            cleaned = clean_transcript_text(value).lower()
            cleaned = re.sub(r"[^a-z0-9_.-]+", "-", cleaned)
            cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
            return (cleaned or "ticket")[:limit]

        async def send_transcript_log_message(
            log_channel: discord.TextChannel,
            embed: discord.Embed,
            transcript_text: str,
        ) -> tuple[bool, str]:
            safe_base_name = (
                f"ticket-{safe_filename_part(ticket_number, 16)}-"
                f"{safe_filename_part(ticket_log_id, 24)}-"
                f"{safe_filename_part(self.channel.name, 48)}-transcript"
            )[:140]

            # Mobile Discord made the colored HTML copy more trouble than it was
            # worth. Attach only the plain text transcript and keep the ticket/log
            # embeds colored.
            try:
                await asyncio.wait_for(
                    log_channel.send(
                        embed=embed,
                        file=transcript_file_from_text(transcript_text, f"{safe_base_name}.txt"),
                    ),
                    timeout=12,
                )
                return True, ""
            except discord.Forbidden:
                return False, f"I do not have permission to send embeds/files in {log_channel.mention}."
            except (discord.HTTPException, asyncio.TimeoutError, ValueError, OSError) as exc:
                return False, f"Discord rejected the ticket log message: `{truncate(str(exc), 500)}`"

        try:
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
            legacy_notes_channels = find_legacy_notes_channels_for_ticket(interaction.guild, self.channel)
            audit_text = build_ticket_audit_text(self.bot, interaction.guild, self.channel)

            try:
                transcript_text = await asyncio.wait_for(
                    build_ticket_and_notes_transcript_text(
                        self.channel,
                        notes_thread,
                        audit_text=audit_text,
                        extra_notes_channels=legacy_notes_channels,
                    ),
                    timeout=90,
                )
            except asyncio.TimeoutError:
                await reopen_ticket_metadata()
                await safe_followup(
                    "Ticket close stopped because transcript generation took too long. Try again in a moment, or check that the bot can read this ticket and its notes channel."
                )
                return
            except Exception as exc:
                await reopen_ticket_metadata()
                await safe_followup(
                    f"Ticket close stopped because transcript generation failed: `{truncate(str(exc), 500)}`"
                )
                return

            try:
                save_ticket_log_text(interaction.guild.id, ticket_log_id, transcript_text)
            except OSError:
                pass

            log_channel = await get_log_channel(self.bot, interaction.guild)
            closed_at = now_utc()
            embed = discord.Embed(title=f"Ticket #{ticket_number} Closed", color=discord.Color.red(), timestamp=closed_at)
            embed.description = (
                f"**Type:** {ticket_kind.title()}\n"
                f"**Channel ID:** `{ticket_log_id}`\n"
                f"**Reason:** {truncate(reason_text, 900)}"
            )
            embed.add_field(
                name="People",
                value=(
                    f"**Opened by:** {format_log_user_reference(owner_id)}\n"
                    f"**Claimed by:** {format_log_user_reference(claimed_by if claimed_by else None)}\n"
                    f"**Closed by:** {format_log_user_reference(interaction.user.id)}"
                ),
                inline=False,
            )
            if notes_thread is not None or legacy_notes_channels:
                embed.add_field(
                    name="Staff Notes",
                    value=format_notes_sources_for_embed(notes_thread, legacy_notes_channels),
                    inline=False,
                )
            embed.set_footer(text=f"Closed at {closed_at.strftime('%Y-%m-%d %H:%M:%S UTC')} • Plain text transcript attached")

            log_sent = False
            log_error = ""
            if log_channel is None:
                log_error = "No closed-ticket log channel is configured or the configured channel no longer exists."
            else:
                log_sent, log_error = await send_transcript_log_message(log_channel, embed, transcript_text)

            if not log_sent:
                await reopen_ticket_metadata()
                await safe_followup(
                    "I could not send the transcript to the configured ticket log channel, so I am keeping this ticket open to prevent transcript loss.\n\n"
                    f"Reason: {log_error}\n\n"
                    "Fix the log channel permissions, then run `/sclose` again."
                )
                try:
                    await self.channel.send(
                        f"⚠️ Ticket close was stopped because the transcript could not be posted to the log channel.\nReason: {log_error}"
                    )
                except discord.HTTPException:
                    pass
                return

            try:
                close_notice = build_ticket_event_embed(
                    title="🔒 Ticket Closed",
                    description=f"Closed by {interaction.user.mention}.\n\n**Reason:** {truncate(reason_text, 900)}",
                    color=ticket_status_color(ticket_kind, status="closed"),
                )
                await self.channel.send(embed=close_notice)
            except discord.HTTPException:
                pass

            # Do not auto-delete old visible notes text channels. They may contain
            # staff context from an older patch, so leave cleanup to staff after
            # confirming the transcript/log.
            for legacy_notes_channel in legacy_notes_channels:
                try:
                    await legacy_notes_channel.send(
                        "ℹ️ The linked ticket was closed and this notes channel was included in the transcript. "
                        "It was left in place for manual staff cleanup."
                    )
                except discord.HTTPException:
                    pass

            if notes_thread is not None:
                await archive_lock_notes_thread(
                    notes_thread,
                    reason=f"Ticket {self.channel.id} closed; staff notes transcript saved.",
                )
                clear_notes_thread_id(self.bot, interaction.guild.id, self.channel.id)

            await safe_followup("Transcript saved. Deleting this ticket channel now.")
            try:
                await self.channel.delete(reason=f"Ticket closed by {interaction.user} ({interaction.user.id})")
            except discord.Forbidden:
                await safe_followup(
                    "The transcript was posted, but I could not delete the ticket channel. Give me **Manage Channels** in the ticket category."
                )
            except discord.HTTPException as exc:
                await safe_followup(
                    f"The transcript was posted, but Discord rejected the channel delete: `{truncate(str(exc), 500)}`"
                )
        except Exception as exc:
            await reopen_ticket_metadata()
            await safe_followup(
                f"Ticket close failed before it could finish. The ticket was left open. Error: `{truncate(str(exc), 500)}`"
            )
            try:
                await self.channel.send(
                    f"⚠️ Ticket close failed and the ticket was left open. Error: `{truncate(str(exc), 500)}`"
                )
            except discord.HTTPException:
                pass
        finally:
            closing_ticket_ids = getattr(self.bot, "closing_ticket_ids", set())
            closing_ticket_ids.discard(self.channel.id)
            self.bot.closing_ticket_ids = closing_ticket_ids


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
                "No ticket ping roles are set. Staff can configure ping roles from `/ticketadmin`.",
                ephemeral=True,
            )
            return

        self.bot.ticket_ping_cooldowns[channel.id] = now_utc()
        ping_mentions = mention_roles(ping_roles)
        requester_text = format_member_reference(interaction.user)
        ping_embed = build_ticket_event_embed(
            title="📣 Staff Attention Requested",
            description=f"{interaction.user.mention} requested staff attention for this ticket.",
            color=ticket_status_color(get_ticket_kind(channel), status="ping"),
        )
        ping_embed.add_field(name="Requested By", value=requester_text, inline=False)
        ping_embed.add_field(name="Ping Roles", value=ping_mentions or "None", inline=False)
        ping_sent, ping_warning = await send_role_ping_message(
            channel,
            ping_roles,
            content_prefix="📣 Staff attention requested:",
            embed=ping_embed,
            reason=f"Ticket #{get_ticket_number(channel)} Ping Team",
        )
        if not ping_sent:
            await interaction.response.send_message(ping_warning or "I could not send the role ping.", ephemeral=True)
            return
        response = "Pinged the configured ticket roles. This ticket can ping again in 10 minutes."
        if ping_warning:
            response += f"\n\nWarning: {ping_warning}"
        await interaction.response.send_message(response, ephemeral=True)

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
        await refresh_ticket_status_message(self.bot, channel, status="Claimed")
        notes_thread = await get_notes_thread(self.bot, interaction.guild, channel)
        if isinstance(notes_thread, discord.Thread):
            await sync_notes_thread_staff(self.bot, channel, notes_thread, extra_members=[interaction.user])
        claim_embed = build_ticket_event_embed(
            title="📌 Ticket Claimed",
            description=f"{interaction.user.mention} claimed this ticket.",
            color=ticket_status_color(get_ticket_kind(channel), status="claimed", claimed_by=interaction.user.id),
        )
        claim_embed.add_field(name="Claimed By", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=False)
        await channel.send(embed=claim_embed)
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
        notes_thread = await get_notes_thread(self.bot, interaction.guild, channel)
        if isinstance(notes_thread, discord.Thread):
            await sync_notes_thread_staff(self.bot, channel, notes_thread)
        await refresh_ticket_status_message(self.bot, channel, status="Open")
        unclaim_embed = build_ticket_event_embed(
            title="📌 Ticket Unclaimed",
            description=f"{interaction.user.mention} unclaimed this ticket.",
            color=discord.Color(STARZ_COLOR_GRAY),
        )
        unclaim_embed.add_field(name="Unclaimed By", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=False)
        await channel.send(embed=unclaim_embed)
        await interaction.response.send_message("You unclaimed this ticket.", ephemeral=True)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket:close", row=0)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
            return

        allowed_to_close, close_denial = can_member_close_ticket(self.bot, interaction.user, channel)
        if not allowed_to_close:
            await interaction.response.send_message(close_denial, ephemeral=True)
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


    @discord.ui.button(label="Add Ticket Role", style=discord.ButtonStyle.success, row=4)
    async def add_ticket_role_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        ticket_channel = self._get_ticket_channel(interaction)
        if ticket_channel is None:
            await interaction.response.send_message("Open `/ticketadmin` inside a ticket channel to add a role to that ticket.", ephemeral=True)
            return
        await interaction.response.send_modal(AddRoleModal(self.bot, ticket_channel))

    @discord.ui.button(label="Remove Ticket Role", style=discord.ButtonStyle.danger, row=4)
    async def remove_ticket_role_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        ticket_channel = self._get_ticket_channel(interaction)
        if ticket_channel is None:
            await interaction.response.send_message("Open `/ticketadmin` inside a ticket channel to remove a role from that ticket.", ephemeral=True)
            return
        await interaction.response.send_modal(RemoveRoleModal(self.bot, ticket_channel))

    @discord.ui.button(label="Set Ticket Ping", style=discord.ButtonStyle.secondary, row=4)
    async def set_ticket_ping_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        ticket_channel = self._get_ticket_channel(interaction)
        if ticket_channel is None:
            await interaction.response.send_message("Open `/ticketadmin` inside a ticket channel to set that ticket's ping roles.", ephemeral=True)
            return
        await interaction.response.send_modal(SetPingRolesModal(self.bot, ticket_channel))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=4)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        embed = build_admin_panel_embed(self.bot, interaction.guild, interaction.channel)
        await interaction.response.edit_message(embed=embed, view=self)



def format_permission_flag(value: Optional[bool]) -> str:
    if value is True:
        return "✅ yes"
    if value is False:
        return "❌ no"
    return "➖ inherited"


def roles_with_positions(roles: list[discord.Role]) -> str:
    if not roles:
        return "None"
    lines: list[str] = []
    for role in roles:
        guild = role.guild
        me = guild.me
        above_text = "unknown"
        if me is not None:
            above_text = "yes" if me.top_role > role else "no"
        can_manage_text = "yes" if bot_can_manage_role(guild, role) else "no"
        lines.append(
            f"{role.mention} (`{role.id}`) — position `{role.position}` | "
            f"mentionable: `{str(role.mentionable).lower()}` | bot above: `{above_text}` | bot can manage role: `{can_manage_text}` | temp-toggle: `disabled`"
        )
    return "\n".join(lines)

class TicketCommands(commands.Cog):
    def __init__(self, bot: "TicketBot"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="ticketsetup", description="Set the ticket category and closed-ticket log channel for this server.")
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
            priority_ticket_category_id=current.get("priority_ticket_category_id", 0),
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

        saved_priority_category = interaction.guild.get_channel(config.get("priority_ticket_category_id", 0))
        priority_category_text = (
            saved_priority_category.mention
            if isinstance(saved_priority_category, discord.CategoryChannel)
            else "Using normal ticket category"
        )

        await interaction.response.send_message(
            (
                "Saved ticket setup for this server.\n\n"
                f"**Normal ticket category:** {real_category.name}\n"
                "Used for newly created normal/private ticket channels.\n\n"
                f"**Priority ticket category:** {priority_category_text}\n"
                "Run `/ticketprioritycategory` to place priority tickets in a separate category.\n\n"
                f"**Closed-ticket log channel:** {real_log_channel.mention}\n"
                "Used when tickets close. The transcript file will be posted there automatically.\n\n"
                f"**Normal access roles configured:** {len(config.get('staff_role_ids', []))}\n"
                f"**Priority access roles configured:** {len(config.get('priority_staff_role_ids', []))}\n"
                f"**Priority opener roles configured:** {len(config.get('priority_allowed_role_ids', []))}\n\n"
                "Run `/ticketadmin` to configure pings/access with dropdowns.\n"
                "Run `/ticketpanel` in the channel where you want the ticket embed posted."
            ),
            ephemeral=True,
        )

    @app_commands.command(name="ticketprioritycategory", description="Set a separate category for priority tickets.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(category="Category where priority ticket channels will be created")
    async def set_priority_category(
        self,
        interaction: discord.Interaction,
        category: discord.app_commands.AppCommandChannel,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        try:
            real_category = category.resolve() or await category.fetch()
        except discord.HTTPException:
            real_category = None

        if not isinstance(real_category, discord.CategoryChannel):
            picked_type = getattr(category, "type", "unknown")
            await interaction.response.send_message(
                (
                    "The **category** option must be a real Discord category.\n"
                    "This is where priority ticket channels get created.\n\n"
                    f"You picked: {category.mention} (`{picked_type}`)"
                ),
                ephemeral=True,
            )
            return

        self.bot.config_store.update_guild(
            interaction.guild.id,
            priority_ticket_category_id=real_category.id,
        )

        await interaction.response.send_message(
            (
                "Saved the separate priority ticket category.\n\n"
                f"**Priority tickets** will now be created in: {real_category.mention}\n"
                "**Normal tickets** will still use the normal ticket category from `/ticketsetup`."
            ),
            ephemeral=True,
        )

    @app_commands.command(name="ticketclearprioritycategory", description="Make priority tickets use the normal ticket category again.")
    @app_commands.default_permissions(manage_guild=True)
    async def clear_priority_category(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        self.bot.config_store.update_guild(
            interaction.guild.id,
            priority_ticket_category_id=0,
        )

        await interaction.response.send_message(
            "Priority tickets will now use the normal ticket category again.",
            ephemeral=True,
        )

    @app_commands.command(name="ticketaddstaff", description="Add a normal ticket access role.")
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

    @app_commands.command(name="ticketremovestaff", description="Remove a normal ticket access role.")
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

    @app_commands.command(name="ticketaddpingrole", description="Add a normal ticket ping role.")
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

    @app_commands.command(name="ticketremovepingrole", description="Remove a normal ticket ping role.")
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

    @app_commands.command(name="ticketpanelgif", description="Set or clear the GIF/image shown on the ticket panel.")
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

    @app_commands.command(name="ticketpanelmessage", description="Edit the ticket panel embed text/color for this server.")
    @app_commands.default_permissions(manage_guild=True)
    async def panel_message(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        await interaction.response.send_modal(TicketPanelMessageModal(self.bot, interaction.guild.id))

    @app_commands.command(name="ticketresetpanelmessage", description="Reset this server's ticket panel embed message to the STARZ default.")
    @app_commands.default_permissions(manage_guild=True)
    async def reset_panel_message(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        self.bot.config_store.update_guild(
            interaction.guild.id,
            panel_title=DEFAULT_TICKET_PANEL_TITLE,
            panel_author=DEFAULT_TICKET_PANEL_AUTHOR,
            panel_description=DEFAULT_TICKET_PANEL_DESCRIPTION,
            panel_footer=DEFAULT_TICKET_PANEL_FOOTER,
            panel_color_hex=DEFAULT_TICKET_PANEL_COLOR_HEX,
        )
        preview = build_ticket_panel_embed(self.bot, interaction.guild)
        await interaction.response.send_message(
            "Reset the ticket panel embed message for this server.",
            embed=preview,
            ephemeral=True,
        )

    @app_commands.command(name="ticketsettag", description="Create or update a reusable staff tag response.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(name="Short tag name, like rules or payment", response="Message the bot should send when staff uses /tickettag")
    async def set_tag_command(self, interaction: discord.Interaction, name: str, response: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        clean_name = clean_tag_name(name)
        if not clean_name:
            await interaction.response.send_message("Use a tag name with letters or numbers.", ephemeral=True)
            return

        set_tag(self.bot, interaction.guild.id, clean_name, response)
        await interaction.response.send_message(f"Saved tag `{clean_name}`. Staff can now use `/tagsend name:{clean_name}` inside tickets.", ephemeral=True)

    @app_commands.command(name="ticketremovetag", description="Remove a reusable staff tag response.")
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

    @app_commands.command(name="tickettags", description="List saved staff tag responses for this server.")
    @app_commands.default_permissions(manage_messages=True)
    async def list_tags_command(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        tags = get_tags(self.bot, interaction.guild.id)
        if not tags:
            await interaction.response.send_message("No tags are saved yet. Use `/tagadmin` first.", ephemeral=True)
            return

        lines = [f"`{name}` — {truncate(value, 120)}" for name, value in sorted(tags.items())]
        await interaction.response.send_message("Saved tags:\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="tickettag", description="Send a saved staff tag response inside a ticket.")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.autocomplete(name=saved_tag_autocomplete)
    async def send_tag_command(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("Use `/tagsend` inside a ticket channel.", ephemeral=True)
            return

        clean_name = clean_tag_name(name)
        response = get_tags(self.bot, interaction.guild.id).get(clean_name)
        if not response:
            await interaction.response.send_message(f"No tag named `{clean_name}` was found. Use `/taglist` to view saved tags.", ephemeral=True)
            return

        await interaction.response.send_message(response, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="ticketnotes", description="Create or open the staff-only notes thread for this ticket.")
    @app_commands.default_permissions(manage_messages=True)
    async def ticket_notes(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("Use `/ticketnotes` inside a ticket channel.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        thread, status = await create_or_get_notes_thread(self.bot, interaction.guild, channel, interaction.user)
        if thread is None:
            await interaction.followup.send(status, ephemeral=True)
            return

        legacy_notes_channels = [
            notes_channel
            for notes_channel in find_legacy_notes_channels_for_ticket(interaction.guild, channel)
            if int(getattr(notes_channel, "id", 0) or 0) != int(getattr(thread, "id", 0) or 0)
        ]
        legacy_notice = ""
        if legacy_notes_channels:
            legacy_notice = (
                "\n\nI also found old separate notes channel(s) for this ticket: "
                + ", ".join(notes_channel.mention for notes_channel in legacy_notes_channels)
                + "\nThey will be included in the transcript and left alone for manual cleanup."
            )

        msg = "Created" if status == "created" else "Opened existing"
        await interaction.followup.send(
            f"{msg} staff notes thread: {thread.mention}\n"
            f"Notes will be appended under a **STAFF NOTES** divider in the ticket transcript."
            f"{legacy_notice}",
            ephemeral=True,
        )


    @app_commands.command(name="ticketmentioncheck", description="Check ticket ping roles, mention permissions, and optionally send a test ping.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(test_ping="Send a real test ping to the configured ticket ping roles in this channel.")
    async def ticket_mention_check(self, interaction: discord.Interaction, test_ping: bool = False) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        guild = interaction.guild
        channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        config = self.bot.config_store.get_guild(guild.id)

        normal_access = get_staff_roles(self.bot, guild)
        normal_ping = get_guild_ping_roles(self.bot, guild)
        priority_access = get_priority_staff_roles(self.bot, guild)
        priority_ping = get_priority_ping_roles(self.bot, guild)

        bot_member = guild.me
        bot_guild_perms = bot_member.guild_permissions if bot_member is not None else None
        bot_channel_perms = channel.permissions_for(bot_member) if channel is not None and bot_member is not None else None

        embed = discord.Embed(
            title="Ticket Mention Check",
            description=(
                "Use this to verify whether the bot has configured real ping roles and whether Discord permissions allow role mentions.\n"
                "If this shows roles but test ping still does not notify, check the bot role hierarchy and the role's mentionability setting in Discord."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Normal access roles", value=truncate(roles_with_positions(normal_access), 1024), inline=False)
        embed.add_field(name="Normal ping roles", value=truncate(roles_with_positions(normal_ping), 1024), inline=False)
        embed.add_field(name="Priority access roles", value=truncate(roles_with_positions(priority_access), 1024), inline=False)
        embed.add_field(name="Priority ping roles", value=truncate(roles_with_positions(priority_ping), 1024), inline=False)

        perm_lines = [
            f"Bot role position: `{bot_member.top_role.position if bot_member else 'unknown'}`",
            f"Guild Manage Channels: {format_permission_flag(getattr(bot_guild_perms, 'manage_channels', None))}",
            f"Guild Mention Everyone/Roles: {format_permission_flag(getattr(bot_guild_perms, 'mention_everyone', None))}",
        ]
        if channel is not None:
            perm_lines.extend([
                f"Channel: {channel.mention}",
                f"Channel Send Messages: {format_permission_flag(getattr(bot_channel_perms, 'send_messages', None))}",
                f"Channel Mention Everyone/Roles: {format_permission_flag(getattr(bot_channel_perms, 'mention_everyone', None))}",
            ])
        embed.add_field(name="Bot permissions", value="\n".join(perm_lines), inline=False)

        configured_ids = [
            *(int(value) for value in config.get("ping_role_ids", []) if str(value).isdigit()),
            *(int(value) for value in config.get("priority_ping_role_ids", []) if str(value).isdigit()),
        ]
        missing_ids = [role_id for role_id in configured_ids if guild.get_role(role_id) is None]
        embed.add_field(
            name="Stored config IDs",
            value=(
                f"Normal ping IDs: `{config.get('ping_role_ids', [])}`\n"
                f"Priority ping IDs: `{config.get('priority_ping_role_ids', [])}`\n"
                f"Missing role IDs: `{missing_ids or []}`"
            ),
            inline=False,
        )

        test_roles = unique_roles(normal_ping, priority_ping)
        if not test_roles:
            test_roles = unique_roles(normal_access, priority_access)

        if test_ping and test_roles and channel is not None:
            await interaction.response.send_message(embed=embed, ephemeral=True)
            ping_sent, ping_warning = await send_role_ping_message(
                channel,
                test_roles,
                content_prefix="📣 Ticket mention test:",
                reason="Ticket mention check test ping",
            )
            if ping_warning:
                await interaction.followup.send(f"Warning: {ping_warning}", ephemeral=True)
            elif not ping_sent:
                await interaction.followup.send(ping_warning or "The test ping failed.", ephemeral=True)
            return

        if test_ping and not test_roles:
            embed.add_field(name="Test ping", value="No ping/access roles are configured to test.", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ticketfixperms", description="Repair unsafe ticket channel role permissions.")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(all_open="Repair every open ticket in configured ticket categories instead of only this ticket.")
    async def ticketfixperms(self, interaction: discord.Interaction, all_open: bool = False) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not (interaction.user.guild_permissions.manage_channels or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("You need Manage Channels to repair ticket permissions.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        if all_open:
            result = await repair_ticket_permissions_in_categories(self.bot, interaction.guild)
            await interaction.followup.send(
                "Ticket permission repair complete.\n"
                f"Scanned tickets: `{result['scanned']}`\n"
                f"Tickets changed: `{result['repaired_channels']}`\n"
                f"Role overwrites repaired: `{result['repaired_roles']}`",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.followup.send("Run this inside a ticket channel, or use `all_open:true`.", ephemeral=True)
            return

        result = await repair_ticket_permission_overwrites(self.bot, channel)
        await interaction.followup.send(
            "Ticket permission repair complete for this ticket.\n"
            f"Role overwrites scanned: `{result['scanned']}`\n"
            f"Role overwrites repaired: `{result['repaired']}`",
            ephemeral=True,
        )


    @app_commands.command(name="ticketadmin", description="Open the ticket admin control panel.")
    @app_commands.default_permissions(manage_guild=True)
    async def ticket_admin(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        embed = build_admin_panel_embed(self.bot, interaction.guild, interaction.channel)
        await interaction.response.send_message(embed=embed, view=TicketAdminPanelView(self.bot), ephemeral=True)

    @app_commands.command(name="ticketconfig", description="Show the ticket configuration for this server.")
    @app_commands.default_permissions(manage_guild=True)
    async def show_config(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.get_guild(interaction.guild.id)
        category = interaction.guild.get_channel(config.get("ticket_category_id", 0))
        priority_category = interaction.guild.get_channel(config.get("priority_ticket_category_id", 0))
        log_channel = interaction.guild.get_channel(config.get("log_channel_id", 0))
        panel_url = get_panel_gif_url(self.bot, interaction.guild.id) or "Not set"

        embed = discord.Embed(title="Ticket Configuration", color=discord.Color.blurple())
        embed.add_field(name="Normal ticket category", value=category.mention if category else "Not set", inline=False)
        embed.add_field(
            name="Priority ticket category",
            value=priority_category.mention if isinstance(priority_category, discord.CategoryChannel) else "Using normal ticket category",
            inline=False,
        )
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
        panel_message = get_ticket_panel_message_config(self.bot, interaction.guild.id)
        embed.add_field(name="Panel Author", value=truncate(panel_message["author"], 1024), inline=True)
        embed.add_field(name="Panel Title", value=truncate(panel_message["title"], 1024), inline=True)
        embed.add_field(name="Panel Color", value=panel_message["color_hex"], inline=True)
        embed.add_field(name="Panel Message Preview", value=truncate(panel_message["description"], 1024), inline=False)
        auto_responses = get_auto_responses(self.bot, interaction.guild.id)
        enabled_auto_responses = [entry for entry in auto_responses.values() if bool(entry.get("enabled", True))]
        embed.add_field(
            name="Ticket Auto-Responses",
            value=f"`{len(enabled_auto_responses)}` enabled / `{len(auto_responses)}` configured",
            inline=False,
        )
        embed.add_field(
            name="In-ticket controls",
            value="Ticket buttons shown in the private ticket: **Ping Team**, **Claim**, **Unclaim**, and **Close**. Ticket owners cannot claim, unclaim, or close their own tickets. Use `/ticketnotes` for staff notes.",
            inline=False,
        )
        embed.add_field(name="Ready", value="Yes" if self.bot.config_store.is_ready(interaction.guild.id) else "No", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ticketpanel", description="Post the ticket panel in the current channel.")
    @app_commands.default_permissions(manage_guild=True)
    async def ticket_panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        if not self.bot.config_store.is_ready(interaction.guild.id):
            await interaction.response.send_message(
                "This server is not configured yet. Run `/ticketsetup` and configure normal access roles first.",
                ephemeral=True,
            )
            return

        embed = build_ticket_panel_embed(self.bot, interaction.guild)

        await interaction.response.send_message("Ticket panel posted.", ephemeral=True)
        await interaction.followup.send(embed=embed, view=TicketPanelView(self.bot))

    @app_commands.command(name="ticketping", description="Check if the ticket bot is online.")
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
                "For `/ticketsetup` and `/ticketprioritycategory`:\n"
                "- `category` must be a real Discord category\n"
                "- `log_channel` must be a normal text channel for closed ticket logs/transcripts"
            )
        else:
            raise error

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class AutoResponseCommands(commands.Cog):
    def __init__(self, bot: "TicketBot"):
        self.bot = bot

    async def _require_manager(self, interaction: discord.Interaction) -> Optional[discord.Member]:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return None
        if not member_can_manage_auto_responses(self.bot, interaction.user):
            await interaction.response.send_message("You need ticket-admin permissions to manage auto-responses.", ephemeral=True)
            return None
        return interaction.user

    @app_commands.command(name="autoresponseadd", description="Create or update a ticket keyword auto-response.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        trigger="Word or phrase to match inside ticket messages.",
        response="Message the bot should reply with when the trigger matches.",
        mode="Optional: contains by default. Use exact to require the whole message to match.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="contains", value="contains"),
            app_commands.Choice(name="exact", value="exact"),
        ]
    )
    async def autoresponse_add(
        self,
        interaction: discord.Interaction,
        trigger: str,
        response: str,
        mode: str = "contains",
    ) -> None:
        manager = await self._require_manager(interaction)
        if manager is None or interaction.guild is None:
            return

        clean_trigger = normalize_auto_response_text(trigger)
        clean_response = str(response or "").strip()
        clean_mode = normalize_auto_response_mode(mode)

        if not clean_trigger:
            await interaction.response.send_message("Trigger cannot be empty.", ephemeral=True)
            return
        if len(clean_trigger) > 100:
            await interaction.response.send_message("Trigger must be 100 characters or fewer.", ephemeral=True)
            return
        if not clean_response:
            await interaction.response.send_message("Response cannot be empty.", ephemeral=True)
            return
        if len(clean_response) > 1900:
            await interaction.response.send_message("Response must be 1900 characters or fewer.", ephemeral=True)
            return

        entry, existed = set_auto_response(
            self.bot,
            interaction.guild.id,
            trigger=clean_trigger,
            response=clean_response,
            mode=clean_mode,
        )
        await log_auto_response_change(
            self.bot,
            interaction.guild,
            action="updated" if existed else "created",
            admin=manager,
            entry=entry,
        )

        verb = "Updated" if existed else "Created"
        await interaction.response.send_message(
            f"{verb} auto-response for trigger `{entry['trigger']}`. Mode: `{entry['mode']}`. Enabled: `{entry['enabled']}`.",
            ephemeral=True,
        )

    @app_commands.command(name="autoresponselist", description="List this server's ticket auto-responses.")
    @app_commands.default_permissions(manage_guild=True)
    async def autoresponse_list(self, interaction: discord.Interaction) -> None:
        manager = await self._require_manager(interaction)
        if manager is None or interaction.guild is None:
            return

        responses = get_auto_responses(self.bot, interaction.guild.id)
        embed = discord.Embed(
            title="Ticket Auto-Responses",
            color=discord.Color(STARZ_COLOR_BLUE),
            timestamp=now_utc(),
        )
        if not responses:
            embed.description = "No auto-responses are configured yet."
        else:
            lines: list[str] = []
            for entry in responses.values():
                state = "enabled" if bool(entry.get("enabled", True)) else "disabled"
                lines.append(
                    f"**{entry.get('trigger', '')}** — `{state}`, `{normalize_auto_response_mode(entry.get('mode', 'contains'))}`\n"
                    f"{truncate(str(entry.get('response') or ''), 180)}"
                )
            embed.description = truncate("\n\n".join(lines), 4000)
            embed.set_footer(text=f"{len(responses)} configured auto-response(s). Cooldown: {AUTO_RESPONSE_COOLDOWN_SECONDS}s per trigger per ticket.")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="autoresponsedelete", description="Delete a ticket auto-response trigger.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.autocomplete(trigger=auto_response_trigger_autocomplete)
    async def autoresponse_delete(self, interaction: discord.Interaction, trigger: str) -> None:
        manager = await self._require_manager(interaction)
        if manager is None or interaction.guild is None:
            return

        removed = delete_auto_response(self.bot, interaction.guild.id, trigger)
        if removed is None:
            await interaction.response.send_message(f"No auto-response trigger named `{truncate(trigger, 100)}` was found.", ephemeral=True)
            return

        await log_auto_response_change(self.bot, interaction.guild, action="deleted", admin=manager, entry=removed)
        await interaction.response.send_message(f"Deleted auto-response trigger `{removed['trigger']}`.", ephemeral=True)

    @app_commands.command(name="autoresponsetoggle", description="Enable or disable a ticket auto-response trigger.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.autocomplete(trigger=auto_response_trigger_autocomplete)
    async def autoresponse_toggle(self, interaction: discord.Interaction, trigger: str, enabled: bool) -> None:
        manager = await self._require_manager(interaction)
        if manager is None or interaction.guild is None:
            return

        entry = toggle_auto_response(self.bot, interaction.guild.id, trigger, enabled)
        if entry is None:
            await interaction.response.send_message(f"No auto-response trigger named `{truncate(trigger, 100)}` was found.", ephemeral=True)
            return

        await log_auto_response_change(
            self.bot,
            interaction.guild,
            action="enabled" if enabled else "disabled",
            admin=manager,
            entry=entry,
        )
        await interaction.response.send_message(
            f"Auto-response trigger `{entry['trigger']}` is now `{'enabled' if enabled else 'disabled'}`.",
            ephemeral=True,
        )

    @app_commands.command(name="autoresponsetest", description="Test which ticket auto-response would match a message.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(message="Message text to test against configured auto-response triggers.")
    async def autoresponse_test(self, interaction: discord.Interaction, message: str) -> None:
        manager = await self._require_manager(interaction)
        if manager is None or interaction.guild is None:
            return

        match = find_auto_response_match(self.bot, interaction.guild.id, message, include_disabled=False)
        disabled_match = None
        if match is None:
            candidate = find_auto_response_match(self.bot, interaction.guild.id, message, include_disabled=True)
            if candidate is not None and not bool(candidate.get("enabled", True)):
                disabled_match = candidate

        embed = discord.Embed(
            title="Auto-Response Test",
            color=discord.Color(STARZ_COLOR_BLUE),
            timestamp=now_utc(),
        )
        embed.add_field(name="Test Message", value=truncate(message, 1024) or "None", inline=False)

        if match is not None:
            embed.description = "This message would trigger an enabled auto-response."
            embed.add_field(name="Matched Trigger", value=f"`{match.get('trigger', '')}`", inline=True)
            embed.add_field(name="Mode", value=f"`{normalize_auto_response_mode(match.get('mode', 'contains'))}`", inline=True)
            embed.add_field(name="Response Preview", value=truncate(str(match.get("response") or ""), 1024), inline=False)
        elif disabled_match is not None:
            embed.description = "This message matches a trigger, but that trigger is disabled."
            embed.add_field(name="Disabled Trigger", value=f"`{disabled_match.get('trigger', '')}`", inline=True)
            embed.add_field(name="Mode", value=f"`{normalize_auto_response_mode(disabled_match.get('mode', 'contains'))}`", inline=True)
            embed.add_field(name="Response Preview", value=truncate(str(disabled_match.get("response") or ""), 1024), inline=False)
        else:
            embed.description = "No configured auto-response would match this message."

        await interaction.response.send_message(embed=embed, ephemeral=True)


class TagCreateModal(discord.ui.Modal, title="Create Tag"):
    name_input = discord.ui.TextInput(
        label="Tag name",
        placeholder="Example: rules, payment, unlink",
        max_length=40,
    )
    response_input = discord.ui.TextInput(
        label="Tag response",
        placeholder="Message to send when staff uses /tagsend",
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
            "Use `/tagsend` inside a ticket to send one of these saved responses."
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


class TagCommands(commands.Cog):
    def __init__(self, bot: "TicketBot"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="tagadmin", description="Open the tag admin panel.")
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

    @app_commands.command(name="tagsend", description="Send a saved tag response inside a ticket.")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.autocomplete(name=saved_tag_autocomplete)
    async def tag_send(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("Use `/tagsend` inside a ticket channel.", ephemeral=True)
            return
        clean_name = clean_tag_name(name)
        response = get_tags(self.bot, interaction.guild.id).get(clean_name)
        if not response:
            await interaction.response.send_message(f"No tag named `{clean_name}` was found. Use `/taglist` to view saved tags.", ephemeral=True)
            return
        await interaction.response.send_message(response, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="taglist", description="List saved tag responses for this server.")
    @app_commands.default_permissions(manage_messages=True)
    async def tag_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        tags = get_tags(self.bot, interaction.guild.id)
        await interaction.response.send_message(format_tag_list_for_embed(tags), ephemeral=True)


class StatsCommands(commands.Cog):
    def __init__(self, bot: "TicketBot"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="statsuser", description="Show ticket stats for a staff member.")
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

    @app_commands.command(name="statsleaderboard", description="Show ticket staff leaderboard stats.")
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


class StarzShortcutCommands(commands.Cog):
    """Top-level STARZ-prefixed slash shortcuts to avoid generic command-name conflicts."""

    def __init__(self, bot: "TicketBot"):
        self.bot = bot

    @app_commands.command(name="sclose", description="Close the current STARZ ticket.")
    @app_commands.guild_only()
    async def sclose(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        ticket_channel = resolve_ticket_channel_from_context(interaction.guild, interaction.channel)
        if ticket_channel is None:
            await interaction.response.send_message(
                "Use `/sclose` inside a ticket channel, staff-notes thread, or legacy staff-notes channel.",
                ephemeral=True,
            )
            return

        allowed_to_close, close_denial = can_member_close_ticket(self.bot, interaction.user, ticket_channel)
        if not allowed_to_close:
            await interaction.response.send_message(close_denial, ephemeral=True)
            return

        await interaction.response.send_modal(CloseTicketModal(self.bot, ticket_channel))

    @app_commands.command(name="snotes", description="Create or open the STARZ staff-notes thread for this ticket.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def snotes(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        ticket_channel = resolve_ticket_channel_from_context(interaction.guild, interaction.channel)
        if ticket_channel is None:
            await interaction.response.send_message(
                "Use `/snotes` inside a ticket channel, staff-notes thread, or legacy staff-notes channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        thread, status = await create_or_get_notes_thread(self.bot, interaction.guild, ticket_channel, interaction.user)
        if thread is None:
            await interaction.followup.send(status, ephemeral=True)
            return

        legacy_notes_channels = [
            notes_channel
            for notes_channel in find_legacy_notes_channels_for_ticket(interaction.guild, ticket_channel)
            if int(getattr(notes_channel, "id", 0) or 0) != int(getattr(thread, "id", 0) or 0)
        ]
        legacy_notice = ""
        if legacy_notes_channels:
            legacy_notice = (
                "\n\nI also found old separate notes channel(s) for this ticket: "
                + ", ".join(notes_channel.mention for notes_channel in legacy_notes_channels)
                + "\nThey will be included in the transcript and left alone for manual cleanup."
            )

        msg = "Created" if status == "created" else "Opened existing"
        await interaction.followup.send(
            f"{msg} staff notes thread: {thread.mention}\n"
            f"Notes will be appended under a **STAFF NOTES** divider in the ticket transcript."
            f"{legacy_notice}",
            ephemeral=True,
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You do not have permission to use that command."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        raise error


class TicketBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = ENABLE_MEMBERS_INTENT
        intents.message_content = ENABLE_MESSAGE_CONTENT_INTENT

        super().__init__(command_prefix="!", intents=intents)
        self.config_store = GuildConfigStore(CONFIG_PATH)
        self.stats_store = TicketStatsStore(TICKET_STATS_PATH)
        self.ticket_ping_cooldowns: dict[int, datetime] = {}
        self.auto_response_cooldowns: dict[tuple[int, int, str], datetime] = {}
        self.closing_ticket_ids: set[int] = set()

    async def setup_hook(self) -> None:
        self.add_view(TicketPanelView(self))
        self.add_view(TicketChannelView(self))
        await self.add_cog(TicketCommands(self))
        await self.add_cog(StarzShortcutCommands(self))
        await self.add_cog(AutoResponseCommands(self))
        await self.add_cog(TagCommands(self))
        await self.add_cog(StatsCommands(self))
        await self.tree.sync()

    async def on_ready(self) -> None:
        if self.user is not None:
            print(f"Logged in as {self.user} (ID: {self.user.id})")
            if ENABLE_MESSAGE_CONTENT_INTENT:
                print("Message Content Intent requested: ON")
            else:
                print("Message Content Intent requested: OFF - ticket transcripts may show [message text unavailable].")
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
            await maybe_send_auto_response(self, message)

        await self.process_commands(message)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("Missing required configuration value: DISCORD_TOKEN")

    bot = TicketBot()
    asyncio.run(bot.start(TOKEN))


if __name__ == "__main__":
    main()
