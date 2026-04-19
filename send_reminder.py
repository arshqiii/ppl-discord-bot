import asyncio
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("REMINDER_CHANNEL_ID", "0"))
TIMEZONE_NAME = os.getenv("BOT_TIMEZONE", "Asia/Jakarta")

DAILY_SCRUM_TIME = os.getenv("DAILY_SCRUM_TIME", "21:00")
DSM_REMINDER_MINUTES_BEFORE = int(os.getenv("DSM_REMINDER_MINUTES_BEFORE", "30"))
KELOMPOK6_ROLE_MENTION = os.getenv("KELOMPOK6_ROLE_MENTION", "@here")
ASDOS_MENTION = os.getenv("ASDOS_MENTION", "")
THREE_P_ROLE_MENTION = os.getenv("THREE_P_ROLE_MENTION") or KELOMPOK6_ROLE_MENTION
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")


def get_dsm_reminder_time(meeting_time: str) -> str:
    meeting_datetime = datetime.strptime(meeting_time, "%H:%M")
    reminder_datetime = meeting_datetime - timedelta(minutes=DSM_REMINDER_MINUTES_BEFORE)
    return reminder_datetime.strftime("%H:%M")


def build_scrum_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🔔 Daily Scrum Reminder",
        description=(
            f"Daily Scrum Meeting dimulai pukul {DAILY_SCRUM_TIME}.\n"
            f"Reminder ini dikirim {DSM_REMINDER_MINUTES_BEFORE} menit sebelumnya."
        ),
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="📋 Yang Perlu Disiapkan",
        value=(
            "• Progress terbaru\n"
            "• Rencana kerja hari ini\n"
            "• Blocker kalau ada"
        ),
        inline=False,
    )
    return embed


def build_three_p_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📝 Weekly 3P Reminder",
        description="Jangan lupa kirim 3P kalian hari ini.",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="📌 Format Pengumpulan",
        value=(
            "```text\n"
            f"{COMMAND_PREFIX}3p\n"
            "Progress:\n"
            "- ...\n\n"
            "Problem:\n"
            "- ...\n\n"
            "Plan:\n"
            "- ...\n"
            "```"
        ),
        inline=False,
    )
    embed.add_field(
        name="💡 Catatan",
        value="Boleh isi singkat atau pakai bullet points. Satu orang kirim satu 3P.",
        inline=False,
    )
    return embed


def build_messages(reminder_type: str) -> tuple[str, discord.Embed]:
    if reminder_type == "dsm":
        return (
            f"Halo teman-teman {KELOMPOK6_ROLE_MENTION}, jangan lupa hari ini ada DSM dengan {ASDOS_MENTION}".strip(),
            build_scrum_embed(),
        )

    if reminder_type == "3p":
        return (
            f"Halo teman-teman {THREE_P_ROLE_MENTION}, jangan lupa kirim 3P kalian hari ini ya.",
            build_three_p_embed(),
        )

    raise ValueError("Reminder type must be either 'dsm' or '3p'.")


async def send_reminder(reminder_type: str) -> None:
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing.")
    if CHANNEL_ID == 0:
        raise RuntimeError("REMINDER_CHANNEL_ID is missing or invalid.")

    timezone = ZoneInfo(TIMEZONE_NAME)
    current_time = datetime.now(timezone)
    pre_message, embed = build_messages(reminder_type)

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        channel = client.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await client.fetch_channel(CHANNEL_ID)

        await channel.send(pre_message)
        await channel.send(embed=embed)

        print(
            f"Sent {reminder_type} reminder to channel {CHANNEL_ID} at "
            f"{current_time.isoformat()} ({TIMEZONE_NAME})."
        )
        await client.close()

    await client.start(TOKEN)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python send_reminder.py [dsm|3p]")

    reminder_type = sys.argv[1].lower()
    asyncio.run(send_reminder(reminder_type))


if __name__ == "__main__":
    main()
