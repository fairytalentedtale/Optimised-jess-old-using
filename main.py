"""Main bot file"""
import os
import discord
import asyncio
import aiohttp
import psutil
import gc
from discord.ext import commands
from database import Database
from predict import Prediction
from config import TOKEN, BOT_PREFIX

# Custom prefix function for case-insensitive prefixes
def get_prefix(bot, message):
    content_lower = message.content.lower()

    for prefix in BOT_PREFIX:
        prefix_lower = prefix.lower()
        if content_lower.startswith(prefix_lower):
            return message.content[:len(prefix)]

    return BOT_PREFIX

# Bot setup
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
    case_insensitive=True
)

# Global instances
bot.db = None
bot.predictor = None
bot.http_session = None

# Memory tracking
bot.process = psutil.Process(os.getpid())
bot.prediction_count = 0


async def initialize_predictor():
    """
    Create the Prediction object — does NOT load models into RAM.
    Models are loaded on demand via the !loadmodel command.
    """
    try:
        bot.predictor = Prediction()
        print("✅ Predictor object created (models not loaded — use !loadmodel when ready)")
    except Exception as e:
        print(f"❌ Failed to create predictor: {e}")


async def initialize_database():
    """Initialize MongoDB connection"""
    bot.db = Database()
    success = await bot.db.connect()
    return success


async def initialize_http_session():
    """Initialize aiohttp session"""
    timeout = aiohttp.ClientTimeout(total=10, connect=3)
    connector = aiohttp.TCPConnector(
        limit=50,
        limit_per_host=10,
        keepalive_timeout=30,
        enable_cleanup_closed=True
    )

    bot.http_session = aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
        headers={'User-Agent': 'Pokemon-Helper-Bot/1.0'}
    )
    print("✅ HTTP session initialized")


async def memory_monitor():
    """Monitor and log memory usage periodically"""
    await asyncio.sleep(10)  # Wait for bot to fully start

    while True:
        try:
            mem_info = bot.process.memory_info()
            mem_mb = mem_info.rss / 1024 / 1024

            models_loaded = bot.predictor and bot.predictor.models_initialized
            model_status = "loaded" if models_loaded else "not loaded"

            print(f"[MEMORY] {mem_mb:.1f} MB | Models: {model_status} | Predictions: {bot.prediction_count}")

            # Force aggressive GC if memory > 400 MB
            if mem_mb > 400:
                print(f"[MEMORY] ⚠️ High usage ({mem_mb:.1f} MB), forcing GC...")
                gc.collect()
                await asyncio.sleep(1)
                new_mem_mb = bot.process.memory_info().rss / 1024 / 1024
                print(f"[MEMORY] After GC: {new_mem_mb:.1f} MB (freed {mem_mb - new_mem_mb:.1f} MB)")

            await asyncio.sleep(60)

        except Exception as e:
            print(f"[MEMORY] Monitor error: {e}")
            await asyncio.sleep(60)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"Bot prefix: {', '.join(BOT_PREFIX)}")

    initial_mem = bot.process.memory_info().rss / 1024 / 1024
    print(f"[MEMORY] Initial: {initial_mem:.1f} MB")

    # Initialize HTTP session
    await initialize_http_session()

    # Create predictor object (no model loading yet)
    await initialize_predictor()

    # Initialize database
    await initialize_database()

    # Load cogs
    cogs_to_load = [
        'cogs.collection',
        'cogs.type_region',
        'cogs.shiny_hunt',
        'cogs.settings',
        'cogs.prediction',
        'cogs.category',
        'cogs.starboard_settings',
        'cogs.starboard_catch',
        'cogs.starboard_egg',
        'cogs.starboard_unbox',
        'cogs.help',
        'cogs.model_control',   # ← new cog
    ]

    try:
        await bot.load_extension('jishaku')
        print("✅ Jishaku loaded")
    except Exception as e:
        print(f"⚠️ Could not load Jishaku: {e}")

    loaded_count = 0
    failed_count = 0

    for cog in cogs_to_load:
        try:
            await bot.load_extension(cog)
            print(f"✅ Loaded {cog}")
            loaded_count += 1
        except Exception as e:
            print(f"❌ Failed to load {cog}: {e}")
            failed_count += 1

    post_startup_mem = bot.process.memory_info().rss / 1024 / 1024

    print(f"\n{'='*50}")
    print(f"✅ Bot ready!")
    print(f"📊 Loaded {loaded_count}/{len(cogs_to_load)} cogs")
    if failed_count > 0:
        print(f"⚠️ Failed to load {failed_count} cogs")
    print(f"🌐 Serving {len(bot.guilds)} guilds")
    print(f"👥 Serving {sum(g.member_count for g in bot.guilds)} users")
    print(f"💾 RAM at startup: {post_startup_mem:.1f} MB (models not loaded)")
    print(f"💡 Use !loadmodel to load prediction models when starting an incense session")
    print(f"{'='*50}\n")

    # Start memory monitor
    asyncio.create_task(memory_monitor())


@bot.event
async def on_message_edit(before, after):
    """Process edited messages as commands"""
    if after.author.bot:
        return

    if before.content == after.content:
        return

    await bot.process_commands(after)


@bot.event
async def on_command_error(ctx, error):
    """Global error handler"""
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.CommandOnCooldown):
        await ctx.reply(f"⏳ This command is on cooldown. Try again in {error.retry_after:.1f}s", mention_author=False)
        return

    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ You don't have permission to use this command.", mention_author=False)
        return

    if isinstance(error, commands.BotMissingPermissions):
        await ctx.reply("❌ I don't have the necessary permissions to execute this command.", mention_author=False)
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"❌ Missing required argument: `{error.param.name}`\nUse `m!help` for command usage.", mention_author=False)
        return

    if isinstance(error, commands.BadArgument):
        await ctx.reply(f"❌ Invalid argument provided.\nUse `m!help` for command usage.", mention_author=False)
        return

    print(f"Unexpected error in command {ctx.command}: {error}")
    await ctx.reply("❌ An unexpected error occurred. Please try again later.", mention_author=False)


async def cleanup():
    """Clean up resources on shutdown"""
    if bot.http_session:
        await bot.http_session.close()

    if bot.db:
        bot.db.close()


def main():
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN environment variable not set")
        return

    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("❌ Error: Invalid Discord token")
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
    finally:
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(cleanup())
        except Exception:
            pass


if __name__ == "__main__":
    main()
