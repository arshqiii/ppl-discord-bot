import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("scrum_bot")

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "bot_state.json"

TOKEN = os.getenv("DISCORD_TOKEN")
TIMEZONE_NAME = os.getenv("BOT_TIMEZONE", "Asia/Jakarta")
CHANNEL_ID = int(os.getenv("REMINDER_CHANNEL_ID", "0"))
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")

DAILY_SCRUM_DAYS = {
    day.strip().lower()
    for day in os.getenv("DAILY_SCRUM_DAYS", "monday,tuesday,wednesday,thursday,friday").split(",")
    if day.strip()
}
DAILY_SCRUM_TIME = os.getenv("DAILY_SCRUM_TIME", "09:00")
DSM_REMINDER_MINUTES_BEFORE = int(os.getenv("DSM_REMINDER_MINUTES_BEFORE", "30"))

THREE_P_DAYS = {
    day.strip().lower()
    for day in os.getenv("THREE_P_DAYS", "friday").split(",")
    if day.strip()
}
THREE_P_TIME = os.getenv("THREE_P_TIME", "16:00")

KELOMPOK6_ROLE_MENTION = os.getenv("KELOMPOK6_ROLE_MENTION")
ASDOS_MENTION = os.getenv("ASDOS_MENTION")
THREE_P_ROLE_ID = int(os.getenv("THREE_P_ROLE_ID"))
THREE_P_MEMBER_IDS = {
    int(member_id.strip())
    for member_id in os.getenv("THREE_P_MEMBER_IDS", "").split(",")
    if member_id.strip()
}

WEEKDAYS = [
    "Senin",
    "Selasa",
    "Rabu",
    "Kamis",
    "Jumat",
    "Sabtu",
    "Minggu",
]

def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {
            "last_sent": {},
            "event_windows": {},
            "three_p_rounds": {},
        }

    try:
        state_data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("State file is invalid JSON. Starting with empty state.")
        return {
            "last_sent": {},
            "event_windows": {},
            "three_p_rounds": {},
        }

    state_data.setdefault("last_sent", {})
    state_data.setdefault("event_windows", {})
    state_data.setdefault("three_p_rounds", {})
    return state_data


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


state = load_state()
timezone = ZoneInfo(TIMEZONE_NAME)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.members = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)


def now_local() -> datetime:
    return datetime.now(timezone)


def get_today_name(current_time: datetime) -> str:
    return WEEKDAYS[current_time.weekday()]


def should_send(event_name: str, event_days: set[str], event_time: str, current_time: datetime) -> bool:
    if get_today_name(current_time) not in event_days:
        return False

    if current_time.strftime("%H:%M") != event_time:
        return False

    event_date_key = current_time.strftime("%Y-%m-%d")
    return state["last_sent"].get(event_name) != event_date_key


def get_dsm_reminder_time(meeting_time: str) -> str:
    meeting_datetime = datetime.strptime(meeting_time, "%H:%M")
    reminder_datetime = meeting_datetime - timedelta(minutes=DSM_REMINDER_MINUTES_BEFORE)
    return reminder_datetime.strftime("%H:%M")


def open_event_window(event_name: str, channel_id: int, current_time: datetime) -> None:
    state["event_windows"][event_name] = {
        "start_time": current_time.isoformat(),
        "channel_id": channel_id,
    }
    save_state(state)


def get_channel_by_id(channel_id: int) -> discord.abc.Messageable | None:
    if channel_id == 0:
        return None
    return bot.get_channel(channel_id)


def get_active_three_p_round() -> dict[str, Any] | None:
    round_data = state["three_p_rounds"].get("active")
    if not round_data or round_data.get("completed"):
        return None
    return round_data


def normalize_three_p_item(line: str) -> str:
    cleaned = line.strip()
    if cleaned.startswith("- "):
        return cleaned[2:].strip()
    if cleaned.startswith("* "):
        return cleaned[2:].strip()
    return cleaned


def parse_three_p_header(line: str) -> tuple[str | None, str]:
    if ":" not in line:
        return None, ""

    key, value = line.split(":", 1)
    normalized_key = key.strip().lower()
    if normalized_key not in {"progress", "problem", "plan"}:
        return None, ""
    return normalized_key, value


def append_three_p_item(parsed: dict[str, list[str]], section: str, raw_value: str) -> None:
    normalized_value = normalize_three_p_item(raw_value)
    if normalized_value:
        parsed.setdefault(section, []).append(normalized_value)


def parse_three_p_submission(raw_text: str) -> dict[str, list[str]] | None:
    parsed: dict[str, list[str]] = {}
    current_key: str | None = None

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        header_key, header_value = parse_three_p_header(line)
        if header_key:
            current_key = header_key
            parsed[current_key] = []
            append_three_p_item(parsed, current_key, header_value)
            continue

        if current_key:
            append_three_p_item(parsed, current_key, line)

    if all(parsed.get(field) for field in ("progress", "problem", "plan")):
        return parsed
    return None


def get_members_from_role(role: discord.Role | None) -> dict[int, str]:
    if role is None:
        return {}

    return {
        member.id: member.display_name
        for member in role.members
        if not member.bot
    }


def get_members_from_ids(guild: discord.Guild, member_ids: set[int]) -> dict[int, str]:
    members: dict[int, str] = {}
    for member_id in member_ids:
        member = guild.get_member(member_id)
        members[member_id] = member.display_name if member else str(member_id)
    return members


def get_expected_three_p_members(guild: discord.Guild) -> dict[int, str]:
    if THREE_P_ROLE_ID:
        members_from_role = get_members_from_role(guild.get_role(THREE_P_ROLE_ID))
        if members_from_role:
            return members_from_role

    if THREE_P_MEMBER_IDS:
        return get_members_from_ids(guild, THREE_P_MEMBER_IDS)

    return {}


def start_three_p_round(channel_id: int, current_time: datetime, expected_members: dict[int, str]) -> None:
    state["three_p_rounds"]["active"] = {
        "round_id": current_time.strftime("%Y-%m-%d"),
        "channel_id": channel_id,
        "opened_at": current_time.isoformat(),
        "expected_members": {str(member_id): name for member_id, name in expected_members.items()},
        "submissions": {},
        "completed": False,
        "completed_at": None,
    }
    save_state(state)


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
            "• Rencana kerja selanjutnya\n"
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


def build_three_p_status_embed(round_data: dict[str, Any]) -> discord.Embed:
    expected_members = round_data.get("expected_members", {})
    submissions = round_data.get("submissions", {})

    submitted_names = [
        submissions[member_id]["member_name"]
        for member_id in expected_members
        if member_id in submissions
    ]
    pending_names = [
        expected_members[member_id]
        for member_id in expected_members
        if member_id not in submissions
    ]

    embed = discord.Embed(
        title="📊 Weekly 3P Status",
        description=f"Status pengumpulan: {len(submitted_names)}/{len(expected_members)} member sudah submit.",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="✅ Submitted",
        value="\n".join(f"• {name}" for name in submitted_names) if submitted_names else "-",
        inline=False,
    )
    embed.add_field(
        name="⏳ Pending",
        value="\n".join(f"• {name}" for name in pending_names) if pending_names else "-",
        inline=False,
    )
    return embed


def build_three_p_summary_embed(
    round_data: dict[str, Any],
    title: str,
    description: str,
    color: discord.Color,
) -> discord.Embed:
    expected_members = round_data.get("expected_members", {})
    submissions = round_data.get("submissions", {})
    pending_names = [
        expected_members[member_id]
        for member_id in expected_members
        if member_id not in submissions
    ]

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )

    if pending_names:
        embed.add_field(
            name="⏳ Pending",
            value="\n".join(f"• {name}" for name in pending_names),
            inline=False,
        )

    if submissions:
        for _, submission in sorted(
            submissions.items(),
            key=lambda item: item[1].get("member_name", "").lower(),
        ):
            field_value = (
                "Progress:\n"
                + "\n".join(f"- {item}" for item in submission["progress"])
                + "\n\nProblem:\n"
                + "\n".join(f"- {item}" for item in submission["problem"])
                + "\n\nPlan:\n"
                + "\n".join(f"- {item}" for item in submission["plan"])
            )

            if len(field_value) > 1024:
                field_value = field_value[:1021] + "..."

            embed.add_field(
                name=f"👤 {submission['member_name']}",
                value=field_value,
                inline=False,
            )
    else:
        embed.add_field(
            name="📭 Submitted 3P",
            value="Belum ada submission yang diterima.",
            inline=False,
        )
    return embed


def format_summary(event_name: str, messages: list[discord.Message]) -> str:
    grouped_messages: dict[str, list[str]] = defaultdict(list)

    for message in messages:
        content = message.content.strip()
        if not content:
            continue
        grouped_messages[message.author.display_name].append(content)

    if not grouped_messages:
        return f"No text messages were found for `{event_name}` in the tracked time window."

    lines = [f"Summary for `{event_name}`:"]
    for author, author_messages in grouped_messages.items():
        latest_message = author_messages[-1].replace("\n", " ")
        if len(latest_message) > 180:
            latest_message = latest_message[:177] + "..."
        lines.append(f"- {author}: {len(author_messages)} message(s). Latest update: {latest_message}")

    return "\n".join(lines)


def format_three_p_completion(round_data: dict[str, Any]) -> str:
    submissions = round_data.get("submissions", {})
    lines = ["3P selesai, berikut ringkasannya:"]

    for _, submission in sorted(
        submissions.items(),
        key=lambda item: item[1].get("member_name", "").lower(),
    ):
        lines.append("")
        lines.append(f"{submission['member_name']}")
        lines.append("Progress:")
        for item in submission["progress"]:
            lines.append(f"- {item}")
        lines.append("Problem:")
        for item in submission["problem"]:
            lines.append(f"- {item}")
        lines.append("Plan:")
        for item in submission["plan"]:
            lines.append(f"- {item}")

    return "\n".join(lines)


def format_three_p_status(round_data: dict[str, Any]) -> str:
    expected_members = round_data.get("expected_members", {})
    submissions = round_data.get("submissions", {})

    submitted_names = [
        submissions[member_id]["member_name"]
        for member_id in expected_members
        if member_id in submissions
    ]
    pending_names = [
        expected_members[member_id]
        for member_id in expected_members
        if member_id not in submissions
    ]

    lines = [
        f"Status 3P: {len(submitted_names)}/{len(expected_members)} submitted.",
        f"Submitted: {', '.join(submitted_names) if submitted_names else '-'}",
        f"Pending: {', '.join(pending_names) if pending_names else '-'}",
    ]
    return "\n".join(lines)


def format_three_p_forced_completion(round_data: dict[str, Any]) -> str:
    expected_members = round_data.get("expected_members", {})
    submissions = round_data.get("submissions", {})
    pending_names = [
        expected_members[member_id]
        for member_id in expected_members
        if member_id not in submissions
    ]

    lines = ["3P has been closed manually."]
    if pending_names:
        lines.append(f"Pending members: {', '.join(pending_names)}")
    else:
        lines.append("All members had already submitted.")

    if submissions:
        lines.append("")
        lines.append("Submitted 3P:")
        for _, submission in sorted(
            submissions.items(),
            key=lambda item: item[1].get("member_name", "").lower(),
        ):
            lines.append("")
            lines.append(f"{submission['member_name']}")
            lines.append("Progress:")
            for item in submission["progress"]:
                lines.append(f"- {item}")
            lines.append("Problem:")
            for item in submission["problem"]:
                lines.append(f"- {item}")
            lines.append("Plan:")
            for item in submission["plan"]:
                lines.append(f"- {item}")
    else:
        lines.append("")
        lines.append("No 3P submissions were received.")

    return "\n".join(lines)

def build_schedule_summary_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🗓️ Current Schedule",
        description=(
            f"Daily Scrum: Every {', '.join(DAILY_SCRUM_DAYS)} at {DAILY_SCRUM_TIME} ({TIMEZONE_NAME})"
            f" with reminder {DSM_REMINDER_MINUTES_BEFORE} minutes before\n"
            f"Weekly 3P: Every {', '.join(THREE_P_DAYS)}"
        ),
        color=discord.Color.blue(),
    )
    return embed

async def collect_event_messages(channel: discord.TextChannel, event_name: str) -> list[discord.Message]:
    event_window = state["event_windows"].get(event_name)
    if not event_window:
        return []

    start_time = datetime.fromisoformat(event_window["start_time"])
    collected: list[discord.Message] = []

    async for message in channel.history(limit=200, after=start_time, oldest_first=True):
        if message.author.bot:
            continue
        collected.append(message)

    return collected

async def send_event_reminder(
    event_name: str,
    pre_message: str,
    embed: discord.Embed,
) -> None:
    if CHANNEL_ID == 0:
        logger.warning("REMINDER_CHANNEL_ID is not configured.")
        return

    channel = get_channel_by_id(CHANNEL_ID)
    if channel is None:
        logger.warning("Channel %s could not be found.", CHANNEL_ID)
        return

    current_time = now_local()
    await channel.send(pre_message)
    await channel.send(embed=embed)

    state["last_sent"][event_name] = current_time.strftime("%Y-%m-%d")
    open_event_window(event_name, CHANNEL_ID, current_time)

    if event_name == "weekly_3p" and isinstance(channel, discord.TextChannel):
        expected_members = get_expected_three_p_members(channel.guild)
        start_three_p_round(CHANNEL_ID, current_time, expected_members)

        if expected_members:
            await channel.send(
                f"3P started for {len(expected_members)} member(s). "
                f"Use `{COMMAND_PREFIX}status_3p` to check progress."
            )
        else:
            await channel.send(
                "3P started, but no expected members are configured yet. "
                "Set `THREE_P_ROLE_ID` or `THREE_P_MEMBER_IDS` in `.env`."
            )

    logger.info("Sent %s reminder to channel %s", event_name, CHANNEL_ID)


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s", bot.user)
    if not reminder_loop.is_running():
        reminder_loop.start()


@tasks.loop(seconds=30)
async def reminder_loop() -> None:
    current_time = now_local()

    if should_send("daily_scrum", DAILY_SCRUM_DAYS, get_dsm_reminder_time(DAILY_SCRUM_TIME), current_time):
        await send_event_reminder(
            "daily_scrum",
            f"Halo teman-teman {KELOMPOK6_ROLE_MENTION}, jangan lupa malam ini ada DSM dengan {ASDOS_MENTION}",
            build_scrum_embed(),
        )

    if should_send("weekly_3p", THREE_P_DAYS, THREE_P_TIME, current_time):
        await send_event_reminder(
            "weekly_3p",
            f"Halo teman-teman {KELOMPOK6_ROLE_MENTION}, jangan lupa kirim 3P kalian hari ini ya.",
            build_three_p_embed(),
        )


@reminder_loop.before_loop
async def before_reminder_loop() -> None:
    await bot.wait_until_ready()

# These commands can be used to manually trigger reminders or manage 3P rounds.

@bot.command(name="ping")
async def ping(ctx: commands.Context) -> None:
    await ctx.send("Bot is running.")


@bot.command(name="trigger_scrum")
async def trigger_scrum(ctx: commands.Context) -> None:
    await ctx.channel.send(
        f"Halo teman-teman {KELOMPOK6_ROLE_MENTION}, jangan lupa malam ini ada DSM dengan {ASDOS_MENTION}"
    )
    await ctx.channel.send(embed=build_scrum_embed())

    current_time = now_local()
    state["last_sent"]["daily_scrum"] = current_time.strftime("%Y-%m-%d")
    open_event_window("daily_scrum", ctx.channel.id, current_time)


@bot.command(name="trigger_3p")
async def trigger_3p(ctx: commands.Context) -> None:
    await ctx.channel.send(
        f"Halo teman-teman {KELOMPOK6_ROLE_MENTION}, jangan lupa kirim 3P kalian hari ini ya."
    )
    await ctx.channel.send(embed=build_three_p_embed())

    current_time = now_local()
    state["last_sent"]["weekly_3p"] = current_time.strftime("%Y-%m-%d")
    open_event_window("weekly_3p", ctx.channel.id, current_time)

    expected_members = get_expected_three_p_members(ctx.guild)
    start_three_p_round(ctx.channel.id, current_time, expected_members)

    if expected_members:
        await ctx.send(
            embed=discord.Embed(
                title="🚀 3P Started",
                description=(
                    f"3P dimulai untuk {len(expected_members)} member.\n"
                    f"Gunakan `{COMMAND_PREFIX}status_3p` untuk cek progress."
                ),
                color=discord.Color.gold(),
            )
        )
    else:
        await ctx.send(
            "3P started, but no expected members are configured yet. "
            "Set `THREE_P_ROLE_ID` or `THREE_P_MEMBER_IDS` in `.env`."
        )


@bot.command(name="3p")
async def submit_three_p(ctx: commands.Context, *, submission_text: str | None = None) -> None:
    active_round = get_active_three_p_round()
    if not active_round:
        await ctx.send(
            f"There is no active 3P right now. Start one with `{COMMAND_PREFIX}trigger_3p` "
            "or wait for the scheduled reminder."
        )
        return

    if ctx.channel.id != active_round["channel_id"]:
        await ctx.send("Please send your 3P in the active 3P channel.")
        return

    if not submission_text:
        await ctx.send(
            "Use this format:\n"
            f"`{COMMAND_PREFIX}3p`\n"
            "`Progress: ...`\n"
            "`Problem: ...`\n"
            "`Plan: ...`"
        )
        return

    parsed_submission = parse_three_p_submission(submission_text)
    if not parsed_submission:
        await ctx.send(
            "Your 3P format is incomplete. Please use:\n"
            f"`{COMMAND_PREFIX}3p`\n"
            "`Progress: ...`\n"
            "`Problem: ...`\n"
            "`Plan: ...`"
        )
        return

    expected_members = active_round.get("expected_members", {})
    member_id = str(ctx.author.id)

    if expected_members and member_id not in expected_members:
        await ctx.send("You are not listed in the current 3P member group.")
        return

    active_round["submissions"][member_id] = {
        "member_name": ctx.author.display_name,
        "progress": parsed_submission["progress"],
        "problem": parsed_submission["problem"],
        "plan": parsed_submission["plan"],
        "submitted_at": now_local().isoformat(),
    }
    save_state(state)

    await ctx.send(
        embed=discord.Embed(
            title="✅ 3P Received",
            description=f"Submission dari {ctx.author.display_name} sudah diterima.",
            color=discord.Color.green(),
        )
    )

    if expected_members:
        submitted_count = len(active_round["submissions"])
        expected_count = len(expected_members)
        await ctx.send(embed=build_three_p_status_embed(active_round))

        if submitted_count >= expected_count:
            active_round["completed"] = True
            active_round["completed_at"] = now_local().isoformat()
            save_state(state)
            await ctx.send(
                embed=build_three_p_summary_embed(
                    active_round,
                    title="🎉 Weekly 3P Complete",
                    description="Semua member sudah submit 3P. Berikut ringkasannya.",
                    color=discord.Color.green(),
                )
            )


@bot.command(name="status_3p")
async def status_three_p(ctx: commands.Context) -> None:
    active_round = get_active_three_p_round()
    if not active_round:
        await ctx.send("There is no active 3P right now.")
        return

    await ctx.send(embed=build_three_p_status_embed(active_round))


@bot.command(name="end_3p")
async def end_three_p(ctx: commands.Context) -> None:
    active_round = get_active_three_p_round()
    if not active_round:
        await ctx.send("There is no active 3P right now.")
        return

    active_round["completed"] = True
    active_round["completed_at"] = now_local().isoformat()
    save_state(state)

    await ctx.send(
        embed=build_three_p_summary_embed(
            active_round,
            title="🛑 Weekly 3P Closed",
            description="3P ditutup secara manual. Berikut status terakhirnya.",
            color=discord.Color.orange(),
        )
    )


@bot.command(name="schedule")
async def schedule(ctx: commands.Context) -> None:
    await ctx.send(embed=build_schedule_summary_embed())

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from the environment variables.")


bot.run(TOKEN)
