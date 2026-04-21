from __future__ import annotations

import asyncio
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "data/guild_configs.json"))

TICKET_TYPES: dict[str, dict[str, str]] = {
    "support": {
        "label": "Support",
        "emoji": "🛠️",
        "description": "General help, setup issues, and questions.",
        "channel_prefix": "support",
    },
    "report": {
        "label": "Report User",
        "emoji": "🚨",
        "description": "Report a member, scam, abuse, or rule issue.",
        "channel_prefix": "report",
    },
    "billing": {
        "label": "Billing",
        "emoji": "💳",
        "description": "Payments, refunds, purchases, or account billing.",
        "channel_prefix": "billing",
    },
}


# --------------------------------------------------
# Per-guild config store
# --------------------------------------------------
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

    def is_ready(self, guild_id: int) -> bool:
        config = self.get_guild(guild_id)
        return bool(
            config.get("ticket_category_id")
            and config.get("log_channel_id")
            and config.get("staff_role_ids")
        )


# --------------------------------------------------
# Helpers
# --------------------------------------------------
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


def build_ticket_topic(
    *,
    owner_id: int,
    ticket_type: str,
    status: str = "open",
    claimed_by: int = 0,
) -> str:
    return (
        f"ticket_owner:{owner_id}|"
        f"ticket_type:{ticket_type}|"
        f"status:{status}|"
        f"claimed_by:{claimed_by}"
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


def is_ticket_channel(channel: discord.abc.GuildChannel) -> bool:
    return isinstance(channel, discord.TextChannel) and "ticket_owner:" in (channel.topic or "")


def get_ticket_owner_id(channel: discord.TextChannel) -> Optional[int]:
    data = parse_ticket_topic(channel.topic)
    value = data.get("ticket_owner")
    return int(value) if value and value.isdigit() else None


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


async def update_claimed_by(channel: discord.TextChannel, claimed_by: int) -> None:
    data = parse_ticket_topic(channel.topic)
    owner_id = int(data.get("ticket_owner", "0") or 0)
    ticket_type = data.get("ticket_type", "support")
    status = data.get("status", "open")
    await channel.edit(
        topic=build_ticket_topic(
            owner_id=owner_id,
            ticket_type=ticket_type,
            status=status,
            claimed_by=claimed_by,
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

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=now_utc(),
    )
    await log_channel.send(embed=embed, file=file)


async def build_transcript_file(channel: discord.TextChannel) -> discord.File:
    lines: list[str] = []
    lines.append(f"Transcript for #{channel.name}")
    lines.append(f"Channel ID: {channel.id}")
    lines.append(f"Generated: {now_utc().isoformat()}")
    lines.append("-" * 60)

    async for message in channel.history(limit=None, oldest_first=True):
        created = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = f"{message.author} ({message.author.id})"
        content = message.content or ""
        lines.append(f"[{created}] {author}: {content}")

        if message.attachments:
            for attachment in message.attachments:
                lines.append(f"    attachment: {attachment.url}")

        if message.embeds:
            lines.append(f"    embeds: {len(message.embeds)}")

    payload = "\n".join(lines).encode("utf-8")
    return discord.File(io.BytesIO(payload), filename=f"{channel.name}-transcript.txt")


# --------------------------------------------------
# Ticket modals
# --------------------------------------------------
class TicketOpenModal(discord.ui.Modal, title="Open a Ticket"):
    subject = discord.ui.TextInput(
        label="Subject",
        placeholder="Short summary of the issue",
        max_length=100,
    )

    details = discord.ui.TextInput(
        label="Details",
        placeholder="Explain what happened and what you need help with.",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, bot: "TicketBot", ticket_key: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.ticket_key = ticket_key

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
            return

        if not self.bot.config_store.is_ready(interaction.guild.id):
            await interaction.response.send_message(
                "This server is not configured yet. An admin should run /ticket setup first.",
                ephemeral=True,
            )
            return

        category = await get_ticket_category(self.bot, interaction.guild)
        if category is None:
            await interaction.response.send_message(
                "The configured ticket category no longer exists. Run /ticket setup again.",
                ephemeral=True,
            )
            return

        existing = find_open_ticket_for_user(category, interaction.user.id)
        if existing:
            await interaction.response.send_message(
                f"You already have an open ticket: {existing.mention}",
                ephemeral=True,
            )
            return

        config = TICKET_TYPES[self.ticket_key]
        display_name = slugify(interaction.user.display_name)[:18]
        channel_name = f"{config['channel_prefix']}-{display_name}-{str(interaction.user.id)[-4:]}"
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

        for role in get_staff_roles(self.bot, guild):
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            )

        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            topic=build_ticket_topic(owner_id=interaction.user.id, ticket_type=self.ticket_key),
            overwrites=overwrites,
            reason=f"Ticket opened by {interaction.user} ({interaction.user.id})",
        )

        mention_roles = " ".join(f"<@&{role.id}>" for role in get_staff_roles(self.bot, guild))
        opening_ping = f"{interaction.user.mention} {mention_roles}".strip()

        embed = discord.Embed(
            title=f"{config['emoji']} {config['label']} Ticket",
            description=(
                "Thanks for opening a ticket. A staff member will help you soon.\n\n"
                "Use the buttons below to manage the ticket."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="User", value=interaction.user.mention, inline=False)
        embed.add_field(name="Subject", value=truncate(str(self.subject.value), 1024), inline=False)
        embed.add_field(name="Details", value=truncate(str(self.details.value), 1024), inline=False)
        embed.set_footer(text=f"User ID: {interaction.user.id}")

        await channel.send(
            content=opening_ping,
            embed=embed,
            view=TicketChannelView(self.bot),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )

        await interaction.response.send_message(
            f"Your ticket has been created: {channel.mention}",
            ephemeral=True,
        )

        await log_event(
            self.bot,
            guild,
            title="Ticket Opened",
            description=(
                f"Channel: {channel.mention}\n"
                f"User: {interaction.user.mention}\n"
                f"Type: {config['label']}"
            ),
            color=discord.Color.green(),
        )


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

        await self.channel.set_permissions(
            member,
            overwrite=overwrite,
            reason=f"Added to ticket by {interaction.user}",
        )

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

        await self.channel.set_permissions(
            member,
            overwrite=None,
            reason=f"Removed from ticket by {interaction.user}",
        )

        await self.channel.send(f"➖ {member.mention} was removed from this ticket by {interaction.user.mention}.")
        await interaction.response.send_message(f"Removed {member.mention} from the ticket.", ephemeral=True)


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
        ticket_type = topic_data.get("ticket_type", "support")
        claimed_by = int(topic_data.get("claimed_by", "0") or 0)

        await self.channel.edit(
            topic=build_ticket_topic(
                owner_id=owner_id,
                ticket_type=ticket_type,
                status="closed",
                claimed_by=claimed_by,
            )
        )

        transcript = await build_transcript_file(self.channel)

        await log_event(
            self.bot,
            interaction.guild,
            title="Ticket Closed",
            description=(
                f"Channel: #{self.channel.name}\n"
                f"Closed by: {interaction.user.mention}\n"
                f"Owner ID: {owner_id}\n"
                f"Reason: {truncate(reason_text, 1000)}"
            ),
            color=discord.Color.red(),
            file=transcript,
        )

        await self.channel.send(f"🔒 Ticket closed by {interaction.user.mention}. Reason: {reason_text}")
        await asyncio.sleep(4)
        await self.channel.delete(reason=f"Ticket closed by {interaction.user} ({interaction.user.id})")


# --------------------------------------------------
# Views
# --------------------------------------------------
class TicketTypeSelect(discord.ui.Select):
    def __init__(self, bot: "TicketBot"):
        self.bot = bot

        options = [
            discord.SelectOption(
                label=config["label"],
                value=key,
                description=config["description"],
                emoji=config["emoji"],
            )
            for key, config in TICKET_TYPES.items()
        ]

        super().__init__(
            placeholder="Choose a ticket type...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket:type_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not self.bot.config_store.is_ready(interaction.guild.id):
            await interaction.response.send_message(
                "This server is not configured yet. An admin should run /ticket setup first.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(TicketOpenModal(self.bot, self.values[0]))


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect(bot))


class TicketChannelView(discord.ui.View):
    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, custom_id="ticket:claim")
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

        await update_claimed_by(channel, interaction.user.id)
        await channel.send(f"📌 Ticket claimed by {interaction.user.mention}.")
        await interaction.response.send_message("You claimed this ticket.", ephemeral=True)

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.secondary, custom_id="ticket:add_user")
    async def add_user_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        await interaction.response.send_modal(AddUserModal(self.bot, channel))

    @discord.ui.button(label="Remove User", style=discord.ButtonStyle.secondary, custom_id="ticket:remove_user")
    async def remove_user_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        await interaction.response.send_modal(RemoveUserModal(self.bot, channel))

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket:close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        await interaction.response.send_modal(CloseTicketModal(self.bot, channel))


# --------------------------------------------------
# Commands
# --------------------------------------------------
@app_commands.guild_only()
class TicketCommands(commands.GroupCog, group_name="ticket", group_description="Ticket bot commands"):
    def __init__(self, bot: "TicketBot"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="setup", description="Set the ticket category and log channel for this server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_ticket(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel,
        log_channel: discord.TextChannel,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        current = self.bot.config_store.get_guild(interaction.guild.id)
        config = self.bot.config_store.update_guild(
            interaction.guild.id,
            ticket_category_id=category.id,
            log_channel_id=log_channel.id,
            staff_role_ids=current.get("staff_role_ids", []),
        )

        await interaction.response.send_message(
            (
                "Saved ticket setup for this server.\n"
                f"Category: {category.mention}\n"
                f"Log channel: {log_channel.mention}\n"
                f"Staff roles configured: {len(config.get('staff_role_ids', []))}"
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

    @app_commands.command(name="config", description="Show the ticket configuration for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def show_config(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        config = self.bot.config_store.get_guild(interaction.guild.id)
        category = interaction.guild.get_channel(config.get("ticket_category_id", 0))
        log_channel = interaction.guild.get_channel(config.get("log_channel_id", 0))
        role_mentions = []

        for role_id in config.get("staff_role_ids", []):
            role = interaction.guild.get_role(role_id)
            role_mentions.append(role.mention if role else f"`{role_id}`")

        embed = discord.Embed(title="Ticket Configuration", color=discord.Color.blurple())
        embed.add_field(name="Category", value=category.mention if category else "Not set", inline=False)
        embed.add_field(name="Log channel", value=log_channel.mention if log_channel else "Not set", inline=False)
        embed.add_field(name="Staff roles", value="\n".join(role_mentions) if role_mentions else "None", inline=False)
        embed.add_field(
            name="Ready",
            value="Yes" if self.bot.config_store.is_ready(interaction.guild.id) else "No",
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="panel", description="Post the ticket panel in the current channel.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def ticket_panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        if not self.bot.config_store.is_ready(interaction.guild.id):
            await interaction.response.send_message(
                "This server is not configured yet. Run /ticket setup and /ticket addstaff first.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Open a Ticket",
            description=(
                "Choose a ticket type from the menu below and fill out the form.\n\n"
                "A private channel will be created for you and the ticket staff."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="Ticket Types",
            value="\n".join(
                f"{config['emoji']} **{config['label']}** — {config['description']}"
                for config in TICKET_TYPES.values()
            ),
            inline=False,
        )
        embed.add_field(
            name="Inside the ticket",
            value="Buttons included: **Claim**, **Add User**, **Remove User**, **Close**",
            inline=False,
        )
        embed.set_footer(text="One open ticket per user.")

        assert interaction.channel is not None
        await interaction.channel.send(embed=embed, view=TicketPanelView(self.bot))
        await interaction.response.send_message("Ticket panel posted.", ephemeral=True)

    @app_commands.command(name="ping", description="Check if the ticket bot is online.")
    async def ticket_ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"Pong. Bot latency: {latency_ms} ms", ephemeral=True)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You do not have permission to use that command."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        raise error


# --------------------------------------------------
# Bot
# --------------------------------------------------
class TicketBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix="!", intents=intents)
        self.config_store = GuildConfigStore(CONFIG_PATH)

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
