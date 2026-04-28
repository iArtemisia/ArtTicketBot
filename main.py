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


def build_ticket_topic(*, owner_id: int, ticket_type: str, status: str = "open", claimed_by: int = 0) -> str:
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
    ticket_type = data.get("ticket_type", "ticket")
    status = data.get("status", "open")
    await channel.edit(
        topic=build_ticket_topic(owner_id=owner_id, ticket_type=ticket_type, status=status, claimed_by=claimed_by)
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

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            topic=build_ticket_topic(owner_id=interaction.user.id, ticket_type="ticket"),
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

    mention_roles = " ".join(f"<@&{role.id}>" for role in get_staff_roles(bot, guild))
    opening_ping = f"{interaction.user.mention} {mention_roles}".strip()

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
            content=opening_ping,
            embed=embed,
            view=TicketChannelView(bot),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
    except discord.Forbidden:
        return None, "I created the channel but couldn't post the starter message. Check the category/channel permissions."
    except discord.HTTPException:
        return None, "I created the channel but Discord rejected the starter message."

    try:
        await log_event(
            bot,
            guild,
            title="Ticket Opened",
            description=f"Channel: {channel.mention}\nUser: {interaction.user.mention}",
            color=discord.Color.green(),
        )
    except discord.HTTPException:
        pass

    return channel, "created"


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
        ticket_type = topic_data.get("ticket_type", "ticket")
        claimed_by = int(topic_data.get("claimed_by", "0") or 0)

        await self.channel.edit(
            topic=build_ticket_topic(owner_id=owner_id, ticket_type=ticket_type, status="closed", claimed_by=claimed_by)
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


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: "TicketBot"):
        super().__init__(timeout=None)
        self.add_item(TicketOpenButton(bot))


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


@app_commands.guild_only()
class TicketCommands(commands.GroupCog, group_name="ticket", group_description="Ticket bot commands"):
    def __init__(self, bot: "TicketBot"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="setup", description="Set the ticket category and log channel for this server.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        category="Category where private ticket channels will be created",
        log_channel="Text channel where ticket logs and transcripts will be sent",
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
                    "This is where ticket open/close logs and transcripts are sent.\n\n"
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
            panel_gif_url=current.get("panel_gif_url", ""),
        )

        await interaction.response.send_message(
            (
                "Saved ticket setup for this server.\n\n"
                f"**Ticket category:** {real_category.name}\n"
                "Used for newly created private ticket channels.\n\n"
                f"**Log channel:** {real_log_channel.mention}\n"
                "Used for ticket logs and transcripts.\n\n"
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

        panel_url = get_panel_gif_url(self.bot, interaction.guild.id) or "Not set"

        embed = discord.Embed(title="Ticket Configuration", color=discord.Color.blurple())
        embed.add_field(name="Ticket category", value=category.mention if category else "Not set", inline=False)
        embed.add_field(name="Log channel", value=log_channel.mention if log_channel else "Not set", inline=False)
        embed.add_field(name="Staff roles", value="\n".join(role_mentions) if role_mentions else "None", inline=False)
        embed.add_field(name="Panel GIF/Image", value=truncate(panel_url, 1024), inline=False)
        embed.add_field(
            name="Where does the panel go?",
            value="Run `/ticket panel` in the text channel where you want the embed posted.",
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
                "- `log_channel` must be a normal text channel\n\n"
                "Then use `/ticket panel` in the channel where you want the ticket embed posted."
            )
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
            message = (
                "The bot was blocked by channel or category permissions.\n\n"
                "Make sure it can:\n"
                "• View Channel\n"
                "• Send Messages\n"
                "• Embed Links\n"
                "• Manage Channels"
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
