import os
import re
import sqlite3
import asyncio
from datetime import datetime, timedelta

import discord
from discord.ext import commands
from aiohttp import web

TOKEN = os.getenv("TOKEN")

SEF_CHANNEL_ID = 1439301092520463524
REPORT_CHANNEL_ID = 1439030955979106464

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

conn = sqlite3.connect("sef.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    amount INTEGER NOT NULL,
    created_at TEXT NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
""")

conn.commit()

pattern = re.compile(r"^([+-])\s*(\d+)$")


def now_str() -> str:
    return datetime.now().isoformat()


def start_of_week(dt=None):
    if dt is None:
        dt = datetime.now()
    monday = dt - timedelta(days=dt.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def end_of_week(dt=None):
    return start_of_week(dt) + timedelta(days=7)


def week_label(dt=None):
    start = start_of_week(dt)
    end = end_of_week(dt) - timedelta(seconds=1)
    return f"{start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}"


def add_transaction(user_id: str, username: str, amount: int):
    cursor.execute(
        "INSERT INTO transactions (user_id, username, amount, created_at) VALUES (?, ?, ?, ?)",
        (user_id, username, amount, now_str())
    )
    conn.commit()


def get_user_total(user_id: str) -> int:
    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM transactions
        WHERE user_id = ? AND created_at >= ?
    """, (user_id, start_of_week().isoformat()))
    row = cursor.fetchone()
    return row[0] if row else 0


def get_all_totals():
    cursor.execute("""
        SELECT user_id, username, COALESCE(SUM(amount), 0) as total
        FROM transactions
        WHERE created_at >= ?
        GROUP BY user_id, username
        ORDER BY total DESC
    """, (start_of_week().isoformat(),))
    return cursor.fetchall()


def get_last_transactions(limit=10):
    cursor.execute("""
        SELECT username, amount, created_at
        FROM transactions
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    return cursor.fetchall()


def get_grand_total() -> int:
    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM transactions
        WHERE created_at >= ?
    """, (start_of_week().isoformat(),))
    row = cursor.fetchone()
    return row[0] if row else 0


def get_setting(key: str, default: str = "") -> str:
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str):
    cursor.execute("""
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()


def was_report_sent_this_week() -> bool:
    return get_setting("last_report_week", "") == start_of_week().date().isoformat()


def mark_report_sent():
    set_setting("last_report_week", start_of_week().date().isoformat())


async def send_weekly_report():
    channel = bot.get_channel(REPORT_CHANNEL_ID)
    if channel is None:
        print("Report kanal nije pronađen.")
        return

    rows = get_all_totals()
    total = get_grand_total()

    if not rows:
        await channel.send(
            f"📊 **Tjedni izvještaj sefa** ({week_label()})\nNema unosa ovaj tjedan."
        )
        mark_report_sent()
        return

    lines = [f"📊 **Tjedni izvještaj sefa** ({week_label()})"]
    medals = ["🥇", "🥈", "🥉"]

    for i, (_, username, amount) in enumerate(rows[:10]):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {username}: **{amount}**")

    lines.append(f"\n💰 **Ukupno: {total}**")
    await channel.send("\n".join(lines))
    mark_report_sent()


async def weekly_report_loop():
    await bot.wait_until_ready()

    while not bot.is_closed():
        now = datetime.now()

        if now.weekday() == 6 and now.hour >= 20 and not was_report_sent_this_week():
            try:
                await send_weekly_report()
            except Exception as e:
                print(f"Greška kod weekly reporta: {e}")

        await asyncio.sleep(300)


@bot.event
async def on_ready():
    print(f"Bot je online kao {bot.user}")
    if not hasattr(bot, "report_task_started"):
        asyncio.create_task(weekly_report_loop())
        bot.report_task_started = True


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.channel.id != SEF_CHANNEL_ID:
        await bot.process_commands(message)
        return

    text = message.content.strip()
    match = pattern.match(text)

    if match:
        sign, number = match.groups()
        amount = int(number)

        if sign == "-":
            amount = -amount

        add_transaction(
            user_id=str(message.author.id),
            username=message.author.display_name,
            amount=amount
        )

        total = get_user_total(str(message.author.id))
        await message.reply(
            f"Zabilježeno **{amount:+}**. Tvoj ukupni unos ovaj tjedan je **{total}**.",
            mention_author=False
        )

    await bot.process_commands(message)


@bot.command()
async def sef(ctx):
    rows = get_all_totals()

    if not rows:
        await ctx.send(f"**Stanje sefa** ({week_label()})\nNema unosa za ovaj tjedan.")
        return

    lines = [f"**Stanje sefa** ({week_label()})"]
    total = 0

    for _, username, amount in rows:
        lines.append(f"- {username}: **{amount}**")
        total += amount

    lines.append(f"\n**Ukupno: {total}**")
    await ctx.send("\n".join(lines))


@bot.command()
async def mojsef(ctx):
    total = get_user_total(str(ctx.author.id))
    await ctx.send(f"{ctx.author.mention}, ti si ovaj tjedan ubacio **{total}**.")


@bot.command()
async def top(ctx):
    rows = get_all_totals()

    if not rows:
        await ctx.send("Nema unosa za ovaj tjedan.")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = [f"**TOP lista sefa** ({week_label()})"]

    for i, (_, username, amount) in enumerate(rows[:10]):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {username}: **{amount}**")

    await ctx.send("\n".join(lines))


@bot.command()
async def zadnjih10(ctx):
    rows = get_last_transactions(10)

    if not rows:
        await ctx.send("Nema unosa.")
        return

    lines = ["**Zadnjih 10 unosa:**"]
    for username, amount, created_at in rows:
        dt = datetime.fromisoformat(created_at)
        lines.append(f"- {dt.strftime('%d.%m %H:%M')} | {username}: **{amount:+}**")

    await ctx.send("\n".join(lines))


@bot.command()
@commands.has_permissions(administrator=True)
async def reportnow(ctx):
    await send_weekly_report()
    await ctx.send("Tjedni report je poslan.")


@bot.command()
@commands.has_permissions(administrator=True)
async def resetweek(ctx):
    cursor.execute(
        "DELETE FROM transactions WHERE created_at >= ?",
        (start_of_week().isoformat(),)
    )
    conn.commit()
    await ctx.send("Obrisani su svi unosi za ovaj tjedan.")


@bot.command()
@commands.has_permissions(administrator=True)
async def delete_last(ctx):
    cursor.execute("SELECT id FROM transactions ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()

    if not row:
        await ctx.send("Nema unosa za brisanje.")
        return

    cursor.execute("DELETE FROM transactions WHERE id = ?", (row[0],))
    conn.commit()
    await ctx.send("Zadnji unos je obrisan.")


@sef.error
@mojsef.error
@top.error
@zadnjih10.error
@reportnow.error
@resetweek.error
@delete_last.error
async def command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("Nemaš dozvolu za tu komandu.")
    else:
        await ctx.send("Dogodila se greška.")
        print(error)


async def handle_root(request):
    return web.Response(text="SefBot radi.")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"Web server pokrenut na portu {port}")


async def main():
    if not TOKEN:
        raise ValueError("TOKEN nije postavljen u Render Environment Variables.")

    await start_web_server()

    print("Čekam 60 sekundi prije prvog Discord logina...")
    await asyncio.sleep(60)

    while True:
        try:
            print("Pokušavam spojiti bota na Discord...")
            await bot.start(TOKEN)
        except discord.HTTPException as e:
            if e.status == 429:
                print("Discord rate limit. Čekam 120 sekundi pa pokušavam opet...")
                await asyncio.sleep(120)
            else:
                raise
        except Exception as e:
            print(f"Greška: {e}")
            await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
