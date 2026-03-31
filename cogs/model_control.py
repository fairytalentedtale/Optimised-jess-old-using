"""Model control commands — load, unload, and status for prediction models"""
import discord
import gc
import os
import time
import asyncio
import psutil
from discord.ext import commands


def is_admin_or_owner():
    """Custom check: passes for server admins OR bot owner"""
    async def predicate(ctx):
        if await ctx.bot.is_owner(ctx.author):
            return True
        if ctx.guild and ctx.author.guild_permissions.administrator:
            return True
        raise commands.CheckFailure("You need to be a server administrator or bot owner to use this command.")
    return commands.check(predicate)


class ModelControl(commands.Cog):
    """Commands to load, unload, and inspect prediction models"""

    def __init__(self, bot):
        self.bot = bot

    @property
    def predictor(self):
        return self.bot.predictor

    @property
    def http_session(self):
        return self.bot.http_session

    def _get_mem_mb(self) -> float:
        return self.bot.process.memory_info().rss / 1024 / 1024

    # ── Load ──────────────────────────────────────────────────────────────────

    @commands.command(name="loadmodel", aliases=["lm", "modelload", "startmodel"])
    @is_admin_or_owner()
    async def loadmodel_command(self, ctx):
        """Download (if needed) and load prediction models into RAM"""
        if self.predictor is None:
            await ctx.reply("❌ Predictor not initialised — bot may still be starting up.", mention_author=False)
            return

        if self.predictor.models_initialized:
            mem = self._get_mem_mb()
            await ctx.reply(
                f"⚠️ Models are **already loaded**.\n"
                f"RAM usage: `{mem:.1f} MB`\n"
                f"Use `{ctx.prefix}modelstatus` for full details.",
                mention_author=False
            )
            return

        loading_msg = await ctx.reply("⏳ Loading prediction models… this may take a moment.", mention_author=False)

        mem_before = self._get_mem_mb()
        start_time = time.monotonic()

        try:
            await self.predictor.initialize_models(self.http_session)
        except Exception as e:
            await loading_msg.edit(content=f"❌ Failed to load models: `{e}`")
            return

        elapsed = time.monotonic() - start_time
        mem_after = self._get_mem_mb()
        mem_used = mem_after - mem_before

        await loading_msg.edit(
            content=(
                f"✅ **Models loaded successfully!**\n"
                f"> ⏱ Load time: `{elapsed:.1f}s`\n"
                f"> 📦 RAM used by models: `+{mem_used:.1f} MB`\n"
                f"> 💾 Total RAM now: `{mem_after:.1f} MB`"
            )
        )

    # ── Unload ────────────────────────────────────────────────────────────────

    @commands.command(name="unloadmodel", aliases=["um", "modelunload", "stopmodel"])
    @is_admin_or_owner()
    async def unloadmodel_command(self, ctx):
        """Unload prediction models from RAM"""
        if self.predictor is None:
            await ctx.reply("❌ Predictor not initialised.", mention_author=False)
            return

        if not self.predictor.models_initialized:
            await ctx.reply(
                f"⚠️ Models are **not currently loaded** — nothing to unload.",
                mention_author=False
            )
            return

        mem_before = self._get_mem_mb()

        # Unload via the clean method on the Prediction class
        try:
            self.predictor.unload_models()  # handles nullifying + gc.collect() internally
        except Exception as e:
            await ctx.reply(f"❌ Error while unloading: `{e}`", mention_author=False)
            return

        # Second GC pass for anything the first missed
        await asyncio.sleep(0.5)
        gc.collect()

        mem_after = self._get_mem_mb()
        mem_freed = mem_before - mem_after

        await ctx.reply(
            f"✅ **Models unloaded.**\n"
            f"> 🗑 RAM freed: `{mem_freed:.1f} MB`\n"
            f"> 💾 Total RAM now: `{mem_after:.1f} MB`\n"
            f"> Use `{ctx.prefix}loadmodel` to reload when needed.",
            mention_author=False
        )

    # ── Status ────────────────────────────────────────────────────────────────

    @commands.command(name="modelstatus", aliases=["ms", "modelinfo", "modelsinfo"])
    @is_admin_or_owner()
    async def modelstatus_command(self, ctx):
        """Show current model load state, RAM usage, and prediction stats"""
        if self.predictor is None:
            await ctx.reply("❌ Predictor not initialised — bot may still be starting up.", mention_author=False)
            return

        mem_mb = self._get_mem_mb()
        loaded = self.predictor.models_initialized
        prediction_count = getattr(self.bot, 'prediction_count', 0)
        cache_size = len(self.predictor.cache.cache)

        # Primary model info
        if loaded and self.predictor.primary_class_names:
            primary_info = f"`{len(self.predictor.primary_class_names)} classes` — 224×224"
        else:
            primary_info = "_not loaded_"

        # Secondary model info
        if loaded and self.predictor.secondary_class_names:
            meta = self.predictor.secondary_metadata or {}
            w = meta.get("image_width", "?")
            h = meta.get("image_height", "?")
            secondary_info = f"`{len(self.predictor.secondary_class_names)} classes` — {w}×{h}"
        else:
            secondary_info = "_not loaded_"

        # Check model cache files on disk
        from predict import (
            PRIMARY_ONNX_PATH, PRIMARY_LABELS_PATH,
            SECONDARY_ONNX_PATH, SECONDARY_ONNX_DATA_PATH, SECONDARY_METADATA_PATH
        )

        def file_size_str(path):
            if os.path.exists(path):
                size_mb = os.path.getsize(path) / 1024 / 1024
                return f"`{size_mb:.1f} MB`"
            return "`not cached`"

        disk_lines = (
            f"Primary model: {file_size_str(PRIMARY_ONNX_PATH)}\n"
            f"Primary labels: {file_size_str(PRIMARY_LABELS_PATH)}\n"
            f"Secondary model: {file_size_str(SECONDARY_ONNX_PATH)}\n"
            f"Secondary data: {file_size_str(SECONDARY_ONNX_DATA_PATH)}\n"
            f"Secondary meta: {file_size_str(SECONDARY_METADATA_PATH)}"
        )

        status_emoji = "🟢" if loaded else "🔴"
        status_text = "**Loaded**" if loaded else "**Not loaded**"

        embed = discord.Embed(
            title="🤖 Model Status",
            color=discord.Color.green() if loaded else discord.Color.red()
        )
        embed.add_field(
            name="Model State",
            value=f"{status_emoji} {status_text}",
            inline=False
        )
        embed.add_field(
            name="Primary Model",
            value=primary_info,
            inline=True
        )
        embed.add_field(
            name="Secondary Model",
            value=secondary_info,
            inline=True
        )
        embed.add_field(
            name="RAM Usage",
            value=f"`{mem_mb:.1f} MB`",
            inline=True
        )
        embed.add_field(
            name="Predictions This Session",
            value=f"`{prediction_count}`",
            inline=True
        )
        embed.add_field(
            name="Prediction Cache",
            value=f"`{cache_size}` entries",
            inline=True
        )
        embed.add_field(
            name="Model Files on Disk",
            value=disk_lines,
            inline=False
        )

        if not loaded:
            embed.set_footer(text=f"Use {ctx.prefix}loadmodel to load models into RAM")
        else:
            embed.set_footer(text=f"Use {ctx.prefix}unloadmodel to free RAM when done")

        await ctx.reply(embed=embed, mention_author=False)

    # ── Error handler ─────────────────────────────────────────────────────────

    @loadmodel_command.error
    @unloadmodel_command.error
    @modelstatus_command.error
    async def model_command_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("❌ You need to be a server administrator or bot owner to use this command.", mention_author=False)


async def setup(bot):
    await bot.add_cog(ModelControl(bot))
