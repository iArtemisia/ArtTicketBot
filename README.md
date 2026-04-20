# Discord Ticket Bot (Python)

A starter Discord ticket bot built with `discord.py`.

## Features

- Ticket panel embed with a ticket-type dropdown
- Modal for subject + details
- Private ticket channels for the user and staff
- Buttons inside each ticket:
  - Claim
  - Add User
  - Remove User
  - Close
- Ticket transcript sent to the log channel when closed
- One open ticket per user

## Setup

1. Create a Discord application and bot in the Discord Developer Portal.
2. Invite the bot to your server with both `bot` and `applications.commands` scopes.
3. Create:
   - a category for tickets
   - a channel for logs
   - one or more staff roles
4. Copy `.env.example` to `.env` and fill in your real values.
5. Install dependencies:

```bash
pip install -r requirements.txt
```

6. Run the bot:

```bash
python main.py
```

## Commands

- `/ticketpanel` — posts the ticket panel embed
- `/ticketping` — simple health check

## Notes

- This project is set up for one main guild/server through `GUILD_ID`.
- The bot does not require message content intent.
- The bot does use member lookups for add/remove ticket member actions, so `members` intent is enabled in code.
