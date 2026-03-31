"""Server and user settings management"""
import discord
from discord.ext import commands
from config import EMBED_COLOR, Emojis

# ---------------------------------------------------------------------------
# AFK view – now has 4 toggles: ShinyHunt, Collection, TypePings, RegionPings
# ---------------------------------------------------------------------------
class AFKView(discord.ui.View):
    """AFK toggle buttons (global)"""

    def __init__(self, user_id, collection_afk, shiny_hunt_afk, type_ping_afk, region_ping_afk, cog):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.cog = cog
        self.update_buttons(collection_afk, shiny_hunt_afk, type_ping_afk, region_ping_afk)

    def update_buttons(self, collection_afk, shiny_hunt_afk, type_ping_afk, region_ping_afk):
        self.clear_items()

        def _btn(label, afk, custom_id):
            """Red = currently AFK (pings suppressed). Green = active (pings on)."""
            b = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.red if afk else discord.ButtonStyle.green,
                custom_id=custom_id
            )
            return b

        shiny_btn = _btn("ShinyHunt", shiny_hunt_afk, "afk_shiny")
        shiny_btn.callback = self.toggle_shiny_hunt_afk
        self.add_item(shiny_btn)

        col_btn = _btn("Collection", collection_afk, "afk_collection")
        col_btn.callback = self.toggle_collection_afk
        self.add_item(col_btn)

        type_btn = _btn("TypePings", type_ping_afk, "afk_type")
        type_btn.callback = self.toggle_type_ping_afk
        self.add_item(type_btn)

        rgn_btn = _btn("RegionPings", region_ping_afk, "afk_region")
        rgn_btn.callback = self.toggle_region_ping_afk
        self.add_item(rgn_btn)

    def _create_embed(self, collection_afk, shiny_hunt_afk, type_ping_afk, region_ping_afk):
        def _dot(afk):
            return Emojis.GREY_DOT if afk else Emojis.GREEN_DOT

        embed = discord.Embed(
            title="Global AFK Status",
            description=(
                f"✨ ShinyHunt Pings: {_dot(shiny_hunt_afk)}\n"
                f"📚 Collection Pings: {_dot(collection_afk)}\n"
                f"🔷 Type Pings: {_dot(type_ping_afk)}\n"
                f"🌏 Region Pings: {_dot(region_ping_afk)}\n\n"
                "*AFK status applies across all servers*"
            ),
            color=EMBED_COLOR
        )
        return embed

    async def _check_user(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This button is not for you!", ephemeral=True)
            return False
        return True

    async def toggle_collection_afk(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        new_col  = await self.cog.db.toggle_collection_afk(self.user_id)
        new_shy  = await self.cog.db.is_shiny_hunt_afk(self.user_id)
        new_type = await self.cog.db.is_type_ping_afk(self.user_id)
        new_rgn  = await self.cog.db.is_region_ping_afk(self.user_id)
        self.update_buttons(new_col, new_shy, new_type, new_rgn)
        await interaction.response.edit_message(embed=self._create_embed(new_col, new_shy, new_type, new_rgn), view=self)

    async def toggle_shiny_hunt_afk(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        new_shy  = await self.cog.db.toggle_shiny_hunt_afk(self.user_id)
        new_col  = await self.cog.db.is_collection_afk(self.user_id)
        new_type = await self.cog.db.is_type_ping_afk(self.user_id)
        new_rgn  = await self.cog.db.is_region_ping_afk(self.user_id)
        self.update_buttons(new_col, new_shy, new_type, new_rgn)
        await interaction.response.edit_message(embed=self._create_embed(new_col, new_shy, new_type, new_rgn), view=self)

    async def toggle_type_ping_afk(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        new_type = await self.cog.db.toggle_type_ping_afk(self.user_id)
        new_col  = await self.cog.db.is_collection_afk(self.user_id)
        new_shy  = await self.cog.db.is_shiny_hunt_afk(self.user_id)
        new_rgn  = await self.cog.db.is_region_ping_afk(self.user_id)
        self.update_buttons(new_col, new_shy, new_type, new_rgn)
        await interaction.response.edit_message(embed=self._create_embed(new_col, new_shy, new_type, new_rgn), view=self)

    async def toggle_region_ping_afk(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        new_rgn  = await self.cog.db.toggle_region_ping_afk(self.user_id)
        new_col  = await self.cog.db.is_collection_afk(self.user_id)
        new_shy  = await self.cog.db.is_shiny_hunt_afk(self.user_id)
        new_type = await self.cog.db.is_type_ping_afk(self.user_id)
        self.update_buttons(new_col, new_shy, new_type, new_rgn)
        await interaction.response.edit_message(embed=self._create_embed(new_col, new_shy, new_type, new_rgn), view=self)


# ---------------------------------------------------------------------------
# Settings cog
# ---------------------------------------------------------------------------
class Settings(commands.Cog):
    """Server and user settings"""

    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    # ------------------------------------------------------------------
    # p!afk
    # ------------------------------------------------------------------
    @commands.command(name="afk", aliases=["away"])
    async def afk_command(self, ctx):
        """Toggle global AFK status for collection, shiny hunt, type, and region pings"""
        col_afk  = await self.db.is_collection_afk(ctx.author.id)
        shy_afk  = await self.db.is_shiny_hunt_afk(ctx.author.id)
        type_afk = await self.db.is_type_ping_afk(ctx.author.id)
        rgn_afk  = await self.db.is_region_ping_afk(ctx.author.id)

        def _dot(afk):
            return Emojis.GREY_DOT if afk else Emojis.GREEN_DOT

        embed = discord.Embed(
            title="Global AFK Status",
            description=(
                f"✨ ShinyHunt Pings: {_dot(shy_afk)}\n"
                f"📚 Collection Pings: {_dot(col_afk)}\n"
                f"🔷 Type Pings: {_dot(type_afk)}\n"
                f"🌏 Region Pings: {_dot(rgn_afk)}\n\n"
                "*AFK status applies across all servers*"
            ),
            color=EMBED_COLOR
        )

        view = AFKView(ctx.author.id, col_afk, shy_afk, type_afk, rgn_afk, self)
        await ctx.reply(embed=embed, view=view, mention_author=False)

    # ------------------------------------------------------------------
    # Server role settings (admin only)
    # ------------------------------------------------------------------
    @commands.command(name="rare-role", aliases=["rr", "rarerole"])
    @commands.has_permissions(administrator=True)
    async def rare_role_command(self, ctx, role: discord.Role = None):
        """Set or clear the rare Pokemon ping role for this server"""
        if role is None:
            await self.db.set_rare_role(ctx.guild.id, None)
            await ctx.reply("✅ Rare role cleared", mention_author=False)
        else:
            await self.db.set_rare_role(ctx.guild.id, role.id)
            await ctx.reply(f"✅ Rare role set to {role.mention}", mention_author=False)

    @rare_role_command.error
    async def rare_role_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("❌ You need administrator permissions to use this command.", mention_author=False)
        elif isinstance(error, commands.BadArgument):
            if ctx.message.content.lower().endswith(" none"):
                await self.db.set_rare_role(ctx.guild.id, None)
                await ctx.reply("✅ Rare role cleared", mention_author=False)
            else:
                await ctx.reply("❌ Invalid role mention or ID. Use @role, role ID, or 'none' to clear.", mention_author=False)

    @commands.command(name="regional-role", aliases=["regrole", "regional", "regionrole"])
    @commands.has_permissions(administrator=True)
    async def regional_role_command(self, ctx, role: discord.Role = None):
        """Set or clear the regional Pokemon ping role for this server"""
        if role is None:
            await self.db.set_regional_role(ctx.guild.id, None)
            await ctx.reply("✅ Regional role cleared", mention_author=False)
        else:
            await self.db.set_regional_role(ctx.guild.id, role.id)
            await ctx.reply(f"✅ Regional role set to {role.mention}", mention_author=False)

    @regional_role_command.error
    async def regional_role_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("❌ You need administrator permissions to use this command.", mention_author=False)
        elif isinstance(error, commands.BadArgument):
            if ctx.message.content.lower().endswith(" none"):
                await self.db.set_regional_role(ctx.guild.id, None)
                await ctx.reply("✅ Regional role cleared", mention_author=False)
            else:
                await ctx.reply("❌ Invalid role mention or ID. Use @role, role ID, or 'none' to clear.", mention_author=False)

    @commands.command(name="server-settings", aliases=["ss", "ssettings", "serversettings"])
    async def server_settings_command(self, ctx):
        """View current server settings"""
        settings = await self.db.get_guild_settings(ctx.guild.id)

        embed = discord.Embed(
            title=f"Server Settings for {ctx.guild.name}",
            color=EMBED_COLOR
        )

        rare_role_id = settings.get('rare_role_id')
        embed.add_field(name="Rare Role", value=f"<@&{rare_role_id}>" if rare_role_id else "Not set", inline=True)

        regional_role_id = settings.get('regional_role_id')
        embed.add_field(name="Regional Role", value=f"<@&{regional_role_id}>" if regional_role_id else "Not set", inline=True)

        best_name_enabled = settings.get('best_name_enabled', False)
        embed.add_field(name="Best Name", value="Enabled ✅" if best_name_enabled else "Disabled ❌", inline=True)

        embed.add_field(
            name="⭐ Starboard Settings",
            value="Use `p!starboard-settings` to view starboard channel configuration",
            inline=False
        )

        embed.set_footer(text=f"Guild ID: {ctx.guild.id}")
        await ctx.reply(embed=embed, mention_author=False)

    # ------------------------------------------------------------------
    # p!toggle best_name (server owners / admins)
    # ------------------------------------------------------------------
    @commands.command(name="toggle")
    @commands.has_permissions(administrator=True)
    async def toggle_command(self, ctx, feature: str):
        """Toggle server features.

        Examples:
            p!toggle best_name
        """
        feature = feature.lower().replace("-", "_")

        if feature == "best_name":
            current = await self.db.get_best_name(ctx.guild.id)
            new_val = not current
            await self.db.set_best_name(ctx.guild.id, new_val)
            status = "enabled ✅" if new_val else "disabled ❌"
            await ctx.reply(f"Best Name display is now **{status}**", mention_author=False)
        else:
            await ctx.reply(
                f"❌ Unknown feature `{feature}`. Available: `best_name`",
                mention_author=False
            )

    @toggle_command.error
    async def toggle_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("❌ You need administrator permissions to use this command.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("❌ Usage: `p!toggle <feature>` (e.g. `p!toggle best_name`)", mention_author=False)

    # ------------------------------------------------------------------
    # Global settings (bot owner only)
    # ------------------------------------------------------------------
    @commands.command(name="set-low-prediction-channel", aliases=["setlowpred", "lowpredchannel"])
    @commands.is_owner()
    async def set_low_prediction_channel_command(self, ctx, channel: discord.TextChannel):
        """Set the global channel for low confidence predictions (bot owner only)"""
        await self.db.set_low_prediction_channel(channel.id)
        await ctx.reply(f"✅ Low prediction channel set to {channel.mention}", mention_author=False)

    @set_low_prediction_channel_command.error
    async def set_low_prediction_channel_error(self, ctx, error):
        if isinstance(error, commands.NotOwner):
            await ctx.reply("❌ Only the bot owner can use this command.", mention_author=False)
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("❌ Invalid channel mention or ID.", mention_author=False)

    @commands.command(name="only-pings", aliases=["op", "onlypings"])
    @commands.has_permissions(administrator=True)
    async def only_pings_command(self, ctx, enabled: bool = None):
        """Toggle or view only-pings mode (Admin only)"""
        if enabled is None:
            current_status = await self.db.get_only_pings(ctx.guild.id)
            status_text = "enabled ✅" if current_status else "disabled ❌"
            embed = discord.Embed(
                title="Only-Pings Mode",
                description=f"Current status: **{status_text}**\n\nWhen enabled, predictions are only sent when there are collectors, hunters, or rare/regional/type/region pings.",
                color=EMBED_COLOR
            )
            embed.set_footer(text="Use 'p!only-pings true' or 'p!only-pings false' to change")
            await ctx.reply(embed=embed, mention_author=False)
            return

        await self.db.set_only_pings(ctx.guild.id, enabled)
        status = "enabled" if enabled else "disabled"
        await ctx.reply(f"✅ Only-pings mode {status}", mention_author=False)

    @only_pings_command.error
    async def only_pings_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("❌ You need administrator permissions to use this command.", mention_author=False)
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("❌ Invalid argument. Use `true` or `false`", mention_author=False)

    @commands.command(name="set-secondary-model-channel", aliases=["setsecondary", "secondarychannel"])
    @commands.is_owner()
    async def set_secondary_model_channel_command(self, ctx, channel: discord.TextChannel):
        """Set the global channel for secondary model predictions (bot owner only)"""
        await self.db.set_secondary_model_channel(channel.id)
        await ctx.reply(f"✅ Secondary model channel set to {channel.mention}", mention_author=False)

    @set_secondary_model_channel_command.error
    async def set_secondary_model_channel_error(self, ctx, error):
        if isinstance(error, commands.NotOwner):
            await ctx.reply("❌ Only the bot owner can use this command.", mention_author=False)
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("❌ Invalid channel mention or ID.", mention_author=False)


async def setup(bot):
    await bot.add_cog(Settings(bot))
