"""Type and Region ping management"""
import discord
from discord.ext import commands
from config import EMBED_COLOR

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALL_TYPES = [
    "normal", "fire", "water", "electric", "grass", "ice",
    "fighting", "poison", "ground", "flying", "psychic", "bug",
    "rock", "ghost", "dragon", "dark", "steel", "fairy"
]

ALL_REGIONS = [
    "kanto", "johto", "hoenn", "sinnoh", "unova",
    "kalos", "alola", "galar", "paldea", "kitakami", "unknown", "hisui"
]

# One emoji per type for the button labels
TYPE_EMOJI = {
    "normal":   "⚪", "fire":     "🔥", "water":    "💧", "electric": "⚡",
    "grass":    "🌿", "ice":      "❄️",  "fighting": "🥊", "poison":   "☠️",
    "ground":   "🏔️",  "flying":   "🕊️",  "psychic":  "🔮", "bug":      "🐛",
    "rock":     "🪨", "ghost":    "👻", "dragon":   "🐉", "dark":     "🌑",
    "steel":    "⚙️",  "fairy":    "🧚",
}

REGION_EMOJI = {
    "kanto":  "1️⃣", "johto":  "2️⃣", "hoenn":  "3️⃣", "sinnoh": "4️⃣",
    "unova":  "5️⃣", "kalos":  "6️⃣", "alola":  "7️⃣", "galar":  "8️⃣", "paldea": "9️⃣", "unknown": "❓", "kitakami": "🌏", "hisui": "🌏",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_type_args(args: str) -> list[str]:
    """Parse space/comma separated type names into canonical lowercase list."""
    raw = args.replace(",", " ").split()
    valid = []
    for t in raw:
        t_low = t.lower()
        if t_low in ALL_TYPES:
            valid.append(t_low)
    return valid


def _parse_region_args(args: str) -> list[str]:
    raw = args.replace(",", " ").split()
    valid = []
    for r in raw:
        r_low = r.lower()
        if r_low in ALL_REGIONS:
            valid.append(r_low)
    return valid


def _type_embed(user: discord.User, enabled_types: list[str]) -> discord.Embed:
    lines = []
    for t in ALL_TYPES:
        emoji = TYPE_EMOJI.get(t, "")
        dot = "🟢" if t in enabled_types else "⚫"
        lines.append(f"{dot} {emoji} {t.capitalize()}")

    # Two-column layout
    half = len(lines) // 2
    col1 = "\n".join(lines[:half])
    col2 = "\n".join(lines[half:])

    embed = discord.Embed(title="🔷 Type Pings", color=EMBED_COLOR)
    embed.add_field(name="\u200b", value=col1, inline=True)
    embed.add_field(name="\u200b", value=col2, inline=True)
    embed.set_footer(text=f"Click a button to toggle • {len(enabled_types)}/{len(ALL_TYPES)} enabled")
    return embed


def _region_embed(user: discord.User, enabled_regions: list[str]) -> discord.Embed:
    lines = []
    for r in ALL_REGIONS:
        emoji = REGION_EMOJI.get(r, "")
        dot = "🟢" if r in enabled_regions else "⚫"
        lines.append(f"{dot} {emoji} {r.capitalize()}")

    embed = discord.Embed(
        title="🌏 Region Pings",
        description="\n".join(lines),
        color=EMBED_COLOR
    )
    embed.set_footer(text=f"Click a button to toggle • {len(enabled_regions)}/{len(ALL_REGIONS)} enabled")
    return embed


# ---------------------------------------------------------------------------
# Type ping button view (18 buttons across 4 rows)
# Discord allows max 5 rows × 5 buttons = 25, we have 18 — fits fine.
# ---------------------------------------------------------------------------
class TypePingView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int, enabled_types: list[str], cog):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.guild_id = guild_id
        self.enabled_types = list(enabled_types)
        self.cog = cog
        self._message: discord.Message | None = None
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        for pokemon_type in ALL_TYPES:
            is_on = pokemon_type in self.enabled_types
            btn = discord.ui.Button(
                label=f"{TYPE_EMOJI.get(pokemon_type, '')} {pokemon_type.capitalize()}",
                style=discord.ButtonStyle.success if is_on else discord.ButtonStyle.secondary,
                custom_id=f"tp_{pokemon_type}",
            )
            btn.callback = self._make_callback(pokemon_type)
            self.add_item(btn)

    def _make_callback(self, pokemon_type: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This isn't yours!", ephemeral=True)
                return

            now_enabled = await self.cog.db.toggle_user_type_ping(
                self.user_id, self.guild_id, pokemon_type
            )

            if now_enabled:
                if pokemon_type not in self.enabled_types:
                    self.enabled_types.append(pokemon_type)
            else:
                if pokemon_type in self.enabled_types:
                    self.enabled_types.remove(pokemon_type)

            # Invalidate cache so next spawn sees the updated type pings
            if hasattr(self.cog, 'gcache'):
                self.cog.gcache.invalidate_type_pingers(self.guild_id)

            self._build_buttons()
            embed = _type_embed(interaction.user, self.enabled_types)
            await interaction.response.edit_message(embed=embed, view=self)

        return callback

    async def on_timeout(self):
        """Disable all buttons when the view expires."""
        for item in self.children:
            item.disabled = True
        if self._message:
            try:
                await self._message.edit(view=self)
            except discord.HTTPException:
                pass


# ---------------------------------------------------------------------------
# Region ping button view
# ---------------------------------------------------------------------------
class RegionPingView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int, enabled_regions: list[str], cog):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.guild_id = guild_id
        self.enabled_regions = list(enabled_regions)
        self.cog = cog
        self._message: discord.Message | None = None
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        for region in ALL_REGIONS:
            is_on = region in self.enabled_regions
            btn = discord.ui.Button(
                label=f"{REGION_EMOJI.get(region, '')} {region.capitalize()}",
                style=discord.ButtonStyle.success if is_on else discord.ButtonStyle.secondary,
                custom_id=f"rp_{region}",
            )
            btn.callback = self._make_callback(region)
            self.add_item(btn)

    def _make_callback(self, region: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This isn't yours!", ephemeral=True)
                return

            now_enabled = await self.cog.db.toggle_user_region_ping(
                self.user_id, self.guild_id, region
            )

            if now_enabled:
                if region not in self.enabled_regions:
                    self.enabled_regions.append(region)
            else:
                if region in self.enabled_regions:
                    self.enabled_regions.remove(region)

            # Invalidate cache so next spawn sees the updated region pings
            if hasattr(self.cog, 'gcache'):
                self.cog.gcache.invalidate_region_pingers(self.guild_id)

            self._build_buttons()
            embed = _region_embed(interaction.user, self.enabled_regions)
            await interaction.response.edit_message(embed=embed, view=self)

        return callback

    async def on_timeout(self):
        """Disable all buttons when the view expires."""
        for item in self.children:
            item.disabled = True
        if self._message:
            try:
                await self._message.edit(view=self)
            except discord.HTTPException:
                pass


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class TypeRegionPings(commands.Cog):
    """Type and Region ping management"""

    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    @property
    def gcache(self):
        pred_cog = self.bot.get_cog('Prediction')
        return pred_cog.gcache if pred_cog else None

    # ------------------------------------------------------------------
    # p!tp / p!typepings
    # ------------------------------------------------------------------
    @commands.command(name="tp", aliases=["typepings", "typeping"])
    async def type_pings_command(self, ctx, *, args: str = None):
        """Manage your type pings for this server.

        With no args: opens the interactive button menu.
        With args: toggles the listed types directly.

        Examples:
            p!tp                          → open menu
            p!tp bug                      → toggle Bug
            p!tp bug grass fire           → toggle Bug, Grass, Fire
        """
        enabled = await self.db.get_user_type_pings(ctx.author.id, ctx.guild.id)

        # Direct toggle via arguments
        if args:
            types_to_toggle = _parse_type_args(args)

            if not types_to_toggle:
                invalid = args.strip()
                await ctx.reply(
                    f"❌ No valid types found in `{invalid}`.\n"
                    f"Valid types: {', '.join(ALL_TYPES)}",
                    mention_author=False
                )
                return

            toggled = []
            for t in types_to_toggle:
                now_on = await self.db.toggle_user_type_ping(ctx.author.id, ctx.guild.id, t)
                state = "✅" if now_on else "❌"
                toggled.append(f"{state} {TYPE_EMOJI.get(t, '')} {t.capitalize()}")

            # Refresh enabled list
            enabled = await self.db.get_user_type_pings(ctx.author.id, ctx.guild.id)
            embed = _type_embed(ctx.author, enabled)
            toggle_text = "\n".join(toggled)
            await ctx.reply(f"Toggled:\n{toggle_text}", embed=embed, mention_author=False)
            return

        # Interactive menu
        view = TypePingView(ctx.author.id, ctx.guild.id, enabled, self)
        embed = _type_embed(ctx.author, enabled)
        msg = await ctx.reply(embed=embed, view=view, mention_author=False)
        view._message = msg

    # ------------------------------------------------------------------
    # p!rp / p!regionpings
    # ------------------------------------------------------------------
    @commands.command(name="rp", aliases=["regionpings", "regionping"])
    async def region_pings_command(self, ctx, *, args: str = None):
        """Manage your region pings for this server.

        With no args: opens the interactive button menu.
        With args: toggles the listed regions directly.

        Examples:
            p!rp                          → open menu
            p!rp kanto                    → toggle Kanto
            p!rp kanto johto hoenn        → toggle Kanto, Johto, Hoenn
        """
        enabled = await self.db.get_user_region_pings(ctx.author.id, ctx.guild.id)

        if args:
            regions_to_toggle = _parse_region_args(args)

            if not regions_to_toggle:
                invalid = args.strip()
                await ctx.reply(
                    f"❌ No valid regions found in `{invalid}`.\n"
                    f"Valid regions: {', '.join(ALL_REGIONS)}",
                    mention_author=False
                )
                return

            toggled = []
            for r in regions_to_toggle:
                now_on = await self.db.toggle_user_region_ping(ctx.author.id, ctx.guild.id, r)
                state = "✅" if now_on else "❌"
                toggled.append(f"{state} {REGION_EMOJI.get(r, '')} {r.capitalize()}")

            enabled = await self.db.get_user_region_pings(ctx.author.id, ctx.guild.id)
            embed = _region_embed(ctx.author, enabled)
            toggle_text = "\n".join(toggled)
            await ctx.reply(f"Toggled:\n{toggle_text}", embed=embed, mention_author=False)
            return

        # Interactive menu
        view = RegionPingView(ctx.author.id, ctx.guild.id, enabled, self)
        embed = _region_embed(ctx.author, enabled)
        msg = await ctx.reply(embed=embed, view=view, mention_author=False)
        view._message = msg


async def setup(bot):
    await bot.add_cog(TypeRegionPings(bot))
