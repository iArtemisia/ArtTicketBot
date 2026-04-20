import asyncio
import io
import os
import re
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
STAFF_ROLE_IDS = [
    int(role_id.strip())
    for role_id in os.getenv("STAFF_ROLE_IDS", "").split(",")
    if role_id.strip().isdigit()
]

# Change or add ticket types here.
TICKET_TYPES = {
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


# -----------------------------
# Helpers
# -----------------------------
def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-") or "ticket"


def truncate(value: str, limit: int = 1024) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def parse_csv_ids(raw_value: str) -> list[int]:
    results: list[int] = []
    for part in raw_value.split(","):
        part = part.strip()
        if part.isdigit():
            results.append(int(part))
    return results


def extract_id(raw_value: str) -> Optional[int]:
    match = re.search(r"(\d{15,25})", raw_value)
    if not match:
        return None
    return int(match.group(1))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


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


async def fetch_member_safe(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(user_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        return None
    except discord.HTTPException:
        return None


async def resolve_target_member(guild: discord.Guild, raw_value: str) -> Optional[discord.Member]:
    user_id = extract_id(raw_value)
    if user_id is None:
        return None
    return await fetch_member_safe(guild, user_id)


async def get_ticket_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    channel = guild.get_channel(TICKET_CATEGORY_ID)
    if isinstance(channel, discord.CategoryChannel):
        return channel
    return None


async def get_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(LOG_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        return channel
    return None


def get_staff_roles(guild: discord.Guild) -> list[discord.Role]:
    roles: list[discord.Role] = []
    for role_id in STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        if role is not None:
            roles.append(role)
    return roles


def member_is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
        return True

    member_role_ids = {role.id for role in member.roles}
    return any(role_id in member_role_ids for role_id in STAFF_ROLE_IDS)


def is_ticket_channel(channel: discord.abc.GuildChannel) -> bool:
    return isinstance(channel, discord.TextChannel) and "ticket_owner:" in (channel.topic or "")


def get_ticket_owner_id(channel: discord.TextChannel) -> Optional[int]:
    data = parse_ticket_topic(channel.topic)
    value = data.get("ticket_owner")
    return int(value) if value and value.isdigit() else None


def find_open_ticket_for_user(category: discord.CategoryChannel, user_id: int) -> Optional[discord.TextChannel]:
    for channel in category.text_channels:
        data = parse_ticket_topic(channel.topic)
        if data.get("ticket_owner") == str(user_id) and data.get("status") == "open":
            return channel
    return None


async def update_claimed_by(channel: discord.TextChannel, claimed_by: int) -> None:
    data = parse_ticket_topic(channel.topic)
    owner_id = int(data.get("ticket_owner", "0"))
    ticket_type = data.get("ticket_type", "support")
    status = data.get("status", "open")
    await channel.edit(topic=build_ticket_topic(owner_id=owner_id, ticket_type=ticket_type, status=status, claimed_by=claimed_by))


async def log_event(
    guild: discord.Guild,
    *,
    title: str,
    description: str,
    color: discord.Color,
    file: Optional[discord.File] = None,
) -> None:
    log_channel = await get_log_channel(guild)
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
                lines.append(f"    [Attachment] {attachment.filename} -> {attachment.url}")

        if message.embeds:
            for index, embed in enumerate(message.embeds, start=1):
                lines.append(
                    f"    [Embed {index}] title={embed.title!r} description={truncate(embed.description or '', 300)!r}"
                )

    payload = "\n".join(lines).encode("utf-8")
    filename = f"transcript-{channel.name}.txt"
    return discord.File(io.BytesIO(payload), filename=filename)


# -----------------------------
# Ticket modals
# -----------------------------
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

    def __init__(self, bot: commands.Bot, ticket_key: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.ticket_key = ticket_key

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
            return

        category = await get_ticket_category(interaction.guild)
        if category is None:
            await interaction.response.send_message(
                "Ticket category is not configured yet. Set TICKET_CATEGORY_ID first.",
                ephemeral=True,
            )
            return

        existing = find_open_ticket_for_user(category, interaction.user.id)
        if existing is not None:
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

        for role in get_staff_roles(guild):
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

        mention_roles = " ".join(f"<@&{role.id}>" for role in get_staff_roles(guild))
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
            guild,
            title="Ticket Opened",
            description=(
                f"Channel: {channel.mention}\n"
                f"User: {interaction.user.mention}\n"
                f"Type: {config['label']}"
            ),
            color=discord.Color.green(),
        )


class AddUserModal(discord.ui.Modal, title="Add User to Ticket"):
    member_value = discord.ui.TextInput(
        label="User mention or ID",
        placeholder="Example: @User or 123456789012345678",
        max_length=64,
    )

    def __init__(self, channel: discord.TextChannel):
        super().__init__(timeout=300)
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not member_is_staff(interaction.user):
            await interaction.response.send_message("Only ticket staff can add users.", ephemeral=True)
            return

        member = await resolve_target_member(interaction.guild, str(self.member_value))
        if member is None:
            await interaction.response.send_message(
                "I could not find that member in this server.",
                ephemeral=True,
            )
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


class RemoveUserModal(discord.ui.Modal, title="Remove User from Ticket"):
    member_value = discord.ui.TextInput(
        label="User mention or ID",
        placeholder="Example: @User or 123456789012345678",
        max_length=64,
    )

    def __init__(self, channel: discord.TextChannel):
        super().__init__(timeout=300)
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        if not member_is_staff(interaction.user):
            await interaction.response.send_message("Only ticket staff can remove users.", ephemeral=True)
            return

        member = await resolve_target_member(interaction.guild, str(self.member_value))
        if member is None:
            await interaction.response.send_message(
                "I could not find that member in this server.",
                ephemeral=True,
            )
            return

        owner_id = get_ticket_owner_id(self.channel)
        if owner_id == member.id:
            await interaction.response.send_message(
                "You cannot remove the ticket owner with this button.",
                ephemeral=True,
            )
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

    def __init__(self, bot: commands.Bot, channel: discord.TextChannel):
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

        if interaction.user.id != owner_id and not member_is_staff(interaction.user):
            await interaction.response.send_message(
                "Only the ticket owner or ticket staff can close this ticket.",
                ephemeral=True,
            )
            return

        reason_text = str(self.reason.value).strip() or "No reason provided."
        await interaction.response.send_message("Closing the ticket and saving transcript...", ephemeral=True)

        await update_claimed_by(self.channel, int(parse_ticket_topic(self.channel.topic).get("claimed_by", "0") or 0))
        topic_data = parse_ticket_topic(self.channel.topic)
        ticket_type = topic_data.get("ticket_type", "support")
        await self.channel.edit(topic=build_ticket_topic(owner_id=owner_id, ticket_type=ticket_type, status="closed", claimed_by=int(topic_data.get("claimed_by", "0") or 0)))

        transcript = await build_transcript_file(self.channel)
        await log_event(
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


# -----------------------------
# Views / components
# -----------------------------
class TicketTypeSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot):
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
            custom_id="tickets:type-select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TicketOpenModal(self.bot, self.values[0]))


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect(bot))


class TicketChannelView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, custom_id="tickets:claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        if not member_is_staff(interaction.user):
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

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.secondary, custom_id="tickets:add-user")
    async def add_user_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        await interaction.response.send_modal(AddUserModal(channel))

    @discord.ui.button(label="Remove User", style=discord.ButtonStyle.secondary, custom_id="tickets:remove-user")
    async def remove_user_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        await interaction.response.send_modal(RemoveUserModal(channel))

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="tickets:close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
            await interaction.response.send_message("This only works in a ticket channel.", ephemeral=True)
            return

        await interaction.response.send_modal(CloseTicketModal(self.bot, channel))


# -----------------------------
# Bot
# -----------------------------
class TicketBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        self.add_view(TicketPanelView(self))
        self.add_view(TicketChannelView(self))

        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
        else:
            await self.tree.sync()


bot = TicketBot()


@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


@bot.tree.command(name="ticketpanel", description="Post the ticket panel.")
@app_commands.default_permissions(administrator=True)
async def ticketpanel(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("This command only works inside a server.", ephemeral=True)
        return

    if GUILD_ID and interaction.guild_id != GUILD_ID:
        await interaction.response.send_message("This bot is currently configured for a different server.", ephemeral=True)
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

    await interaction.channel.send(embed=embed, view=TicketPanelView(bot))
    await interaction.response.send_message("Ticket panel posted.", ephemeral=True)


@bot.tree.command(name="ticketping", description="Check if the ticket bot is online.")
async def ticketping(interaction: discord.Interaction) -> None:
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong. Bot latency: {latency_ms} ms", ephemeral=True)


def validate_config() -> None:
    missing: list[str] = []
    if not TOKEN:
        missing.append("DISCORD_TOKEN")
    if not GUILD_ID:
        missing.append("GUILD_ID")
    if not TICKET_CATEGORY_ID:
        missing.append("TICKET_CATEGORY_ID")
    if not LOG_CHANNEL_ID:
        missing.append("LOG_CHANNEL_ID")
    if not STAFF_ROLE_IDS:
        missing.append("STAFF_ROLE_IDS")

    if missing:
        names = ", ".join(missing)
        raise RuntimeError(f"Missing required configuration values: {names}")


if __name__ == "__main__":
    validate_config()
    bot.run(TOKEN)
