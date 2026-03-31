"""Pokemon prediction and auto-detection"""
import json
import os
import discord
import asyncio
from discord.ext import commands
from utils import (
    format_pokemon_prediction,
    get_image_url_from_message,
    normalize_pokemon_name,
    get_pokemon_with_variants,
    is_rare_pokemon,
    load_pokemon_data
)
from config import POKETWO_USER_ID, PREDICTION_CONFIDENCE

# Hardcoded channel ID where any image will be auto-predicted
AUTO_PREDICT_CHANNEL_ID = 1453015934393651272

# ---------------------------------------------------------------------------
# Constants – all 18 types and 9 main regions (lowercase, canonical)
# ---------------------------------------------------------------------------
ALL_TYPES = [
    "normal", "fire", "water", "electric", "grass", "ice",
    "fighting", "poison", "ground", "flying", "psychic", "bug",
    "rock", "ghost", "dragon", "dark", "steel", "fairy"
]

ALL_REGIONS = [
    "kanto", "johto", "hoenn", "sinnoh", "unova",
    "kalos", "alola", "galar", "paldea", "kitakami", "unknown"
]

SAFE_MENTIONS = discord.AllowedMentions(
    everyone=True,
    roles=True,
    users=True   # keep @user mentions for hunters/collectors
)

# ---------------------------------------------------------------------------
# Best names loader (cached at module level — zero repeated I/O)
# ---------------------------------------------------------------------------
_BEST_NAMES: dict = {}

def _load_best_names() -> dict:
    global _BEST_NAMES
    if _BEST_NAMES:
        return _BEST_NAMES
    path = os.path.join("data", "best_names.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            _BEST_NAMES = json.load(f)
    except Exception as e:
        print(f"[BEST_NAMES] Could not load {path}: {e}")
        _BEST_NAMES = {}
    return _BEST_NAMES


def get_best_name(pokemon_name: str) -> str | None:
    """Return the best/shortest name for a Pokemon, or None if not in map."""
    names = _load_best_names()
    return names.get(pokemon_name)


# ---------------------------------------------------------------------------
# Type & Region lookup — loaded from data/typeandregions.csv
# Structure: {pokemon_name_lower: {"types": ["fire", "flying"], "region": "kanto"}}
# ---------------------------------------------------------------------------
_TYPE_REGION_DATA: dict = {}

def _load_type_region_data() -> dict:
    global _TYPE_REGION_DATA
    if _TYPE_REGION_DATA:
        return _TYPE_REGION_DATA

    path = os.path.join("data", "typeandregions.csv")
    try:
        import csv
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("name", "").strip()
                if not name:
                    continue
                types = []
                if row.get("type1", "").strip():
                    types.append(row["type1"].strip().lower())
                if row.get("type2", "").strip():
                    types.append(row["type2"].strip().lower())
                region = row.get("region", "").strip().lower()
                _TYPE_REGION_DATA[name.lower()] = {
                    "types": types,
                    "region": region,
                }
        print(f"[TYPE_REGION] Loaded {len(_TYPE_REGION_DATA)} entries from {path}")
    except Exception as e:
        print(f"[TYPE_REGION] Could not load {path}: {e}")

    return _TYPE_REGION_DATA


def get_pokemon_types(pokemon_name: str) -> list[str]:
    """Return list of lowercase type strings for a Pokemon name."""
    data = _load_type_region_data()
    entry = data.get(pokemon_name.lower())
    return entry["types"] if entry else []


def get_pokemon_region(pokemon_name: str) -> list[str]:
    """Return list with the lowercase region string for a Pokemon name."""
    data = _load_type_region_data()
    entry = data.get(pokemon_name.lower())
    if not entry or not entry.get("region"):
        return []
    return [entry["region"]]


# ---------------------------------------------------------------------------
# Main cog
# ---------------------------------------------------------------------------
class Prediction(commands.Cog):
    """Pokemon prediction commands and auto-detection"""

    def __init__(self, bot):
        self.bot = bot
        self.pokemon_data = load_pokemon_data()
        _load_best_names()        # warm cache on startup
        _load_type_region_data()  # warm cache on startup
        print(f"[AUTO-PREDICT] Channel ID set to: {AUTO_PREDICT_CHANNEL_ID}")

    @property
    def db(self):
        return self.bot.db

    @property
    def predictor(self):
        return self.bot.predictor

    @property
    def http_session(self):
        return self.bot.http_session

    # ------------------------------------------------------------------
    # Image extraction
    # ------------------------------------------------------------------
    async def extract_image_url(self, message):
        if message.attachments:
            for attachment in message.attachments:
                if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                    return attachment.url

        if message.embeds:
            for embed in message.embeds:
                if embed.image:
                    return embed.image.url
                if embed.thumbnail:
                    return embed.thumbnail.url

        import re
        url_pattern = r'https?://[^\s<>"]+?\.(?:png|jpg|jpeg|gif|webp)'
        urls = re.findall(url_pattern, message.content, re.IGNORECASE)
        if urls:
            return urls[0]

        url = await get_image_url_from_message(message)
        if url:
            return url

        return None

    # ------------------------------------------------------------------
    # Ping information — ALL gathered in a single batched call
    # ------------------------------------------------------------------

    async def _get_all_ping_data(self, pokemon_name: str, guild_id: int) -> dict:
        """
        Single method that fetches ALL ping data with the minimum possible
        number of DB round-trips by:
          - Fetching shiny AFK, collection AFK, and type/region AFK in parallel
          - Reusing the shared AFK maps instead of re-querying per ping type
          - Fetching guild settings once for rare/regional role IDs
        
        Returns a dict with keys:
            hunters, collectors, rare_ping, regional_ping,
            type_pingers, rgn_pingers
        """
        from utils import find_pokemon_by_name
        pokemon = find_pokemon_by_name(pokemon_name, self.pokemon_data)

        types   = get_pokemon_types(pokemon_name)
        regions = get_pokemon_region(pokemon_name)

        # ── Phase 1: fetch all AFK maps + guild settings in parallel ──────────
        # Each of these is a single DB call; running them concurrently means
        # total latency = max(individual latencies) instead of their sum.
        (
            shiny_afk_users,
            collection_afk_users,
            type_region_afk_map,
            guild_settings,
        ) = await asyncio.gather(
            self.db.get_shiny_hunt_afk_users(),
            self.db.get_collection_afk_users(),
            self.db.get_type_region_afk_users(),   # ← fetched ONCE, shared below
            self.db.get_guild_settings(guild_id),
        )

        # Derive per-type AFK sets from the shared map (no extra DB call)
        type_afk   = {uid for uid, flags in type_region_afk_map.items() if flags.get('type')}
        region_afk = {uid for uid, flags in type_region_afk_map.items() if flags.get('region')}

        # ── Phase 2: fetch actual ping lists in parallel ───────────────────────
        search_names = [pokemon_name]

        is_rare = pokemon and is_rare_pokemon(pokemon)

        async def _get_shiny_hunters():
            hunters_data = await self.db.get_shiny_hunters_for_pokemon(
                guild_id, search_names, shiny_afk_users
            )
            formatted = []
            for user_id, is_afk in hunters_data:
                formatted.append(f"{user_id}(AFK)" if is_afk else f"<@{user_id}>")
            return formatted

        async def _get_collectors():
            collectors = await self.db.get_collectors_for_pokemon(
                guild_id, search_names, collection_afk_users
            )
            if is_rare:
                rare_collectors = await self.db.get_rare_collectors(guild_id, collection_afk_users)
                collectors = list(set(collectors + rare_collectors))
            return collectors

        async def _get_type_pingers():
            if not types:
                return []
            return await self.db.get_users_for_types(guild_id, types, type_afk)

        async def _get_region_pingers():
            if not regions:
                return []
            return await self.db.get_users_for_regions(guild_id, regions, region_afk)

        hunters, collectors, type_pingers, rgn_pingers = await asyncio.gather(
            _get_shiny_hunters(),
            _get_collectors(),
            _get_type_pingers(),
            _get_region_pingers(),
        )

        # ── Phase 3: resolve rare/regional role pings from cached settings ────
        rare_ping     = None
        regional_ping = None

        if pokemon:
            rarity_value = pokemon.get('rarity', '')
            rarities = rarity_value if isinstance(rarity_value, list) else [rarity_value]
            rarities = [r.lower() for r in rarities if r]

            if any(r in ['legendary', 'mythical', 'ultra beast'] for r in rarities):
                rare_role_id = guild_settings.get('rare_role_id')
                if rare_role_id:
                    rare_ping = f"<@&{rare_role_id}>"

            if 'regional' in rarities:
                regional_role_id = guild_settings.get('regional_role_id')
                if regional_role_id:
                    regional_ping = f"<@&{regional_role_id}>"

        return {
            "hunters":       hunters,
            "collectors":    collectors,
            "rare_ping":     rare_ping,
            "regional_ping": regional_ping,
            "type_pingers":  type_pingers,
            "rgn_pingers":   rgn_pingers,
        }

    # ------------------------------------------------------------------
    # Output formatting
    # ------------------------------------------------------------------
    async def build_prediction_output(
        self,
        name: str,
        confidence: str,
        guild_id: int,
        *,
        show_best_name: bool = False,
    ) -> str:
        """
        Gather ALL ping data in a single batched call and build the output string.

        Output order:
            <name>: <confidence>%
            Shortest Name: <n>          ← only if best_name enabled
            Rare Ping: <@&role>         ← only if applicable
            Regional Pings: <@&role>    ← only if applicable
            Shiny Hunters: @...
            Collectors: @...
            Type Pings: @...
            Region Pings: @...
        """
        ping_data = await self._get_all_ping_data(name, guild_id)

        lines = [format_pokemon_prediction(name, confidence)]

        if show_best_name:
            best = get_best_name(name)
            if best:
                lines.append(f"Shortest Name: {best}")

        if ping_data["rare_ping"]:
            lines.append(f"Rare Ping: {ping_data['rare_ping']}")
        if ping_data["regional_ping"]:
            lines.append(f"Regional Pings: {ping_data['regional_ping']}")
        if ping_data["hunters"]:
            lines.append(f"Shiny Hunters: {' '.join(ping_data['hunters'])}")
        if ping_data["collectors"]:
            collector_mentions = " ".join([f"<@{uid}>" for uid in ping_data["collectors"]])
            lines.append(f"Collectors: {collector_mentions}")
        if ping_data["type_pingers"]:
            type_mentions = " ".join([f"<@{uid}>" for uid in ping_data["type_pingers"]])
            lines.append(f"Type Pings: {type_mentions}")
        if ping_data["rgn_pingers"]:
            rgn_mentions = " ".join([f"<@{uid}>" for uid in ping_data["rgn_pingers"]])
            lines.append(f"Region Pings: {rgn_mentions}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Core predict helper (used by p!predict command)
    # ------------------------------------------------------------------
    async def _predict_pokemon(self, image_url: str, guild_id: int) -> str:
        if self.predictor is None:
            return "Predictor not initialized, please try again later."
        if self.http_session is None:
            return "HTTP session not available."

        try:
            name, confidence = await self.predictor.predict(image_url, self.http_session)
            if hasattr(self.bot, 'prediction_count'):
                self.bot.prediction_count += 1

            if not name or not confidence:
                return "Could not predict Pokemon from the provided image."

            show_best = await self.db.get_best_name(guild_id)
            return await self.build_prediction_output(name, confidence, guild_id, show_best_name=show_best)

        except ValueError as e:
            error_msg = str(e)
            if "404" in error_msg or "Failed to load image" in error_msg:
                return "Image not accessible (likely expired or deleted)."
            print(f"Prediction error: {e}")
            return f"Error: {str(e)[:100]}"
        except Exception as e:
            print(f"Prediction error: {e}")
            return f"Error: {str(e)[:100]}"

    # ------------------------------------------------------------------
    # should_send_prediction
    # ------------------------------------------------------------------
    def should_send_prediction_from_data(
        self,
        only_pings_enabled: bool,
        ping_data: dict,
    ) -> bool:
        """
        Synchronous check — ping_data already fetched, only_pings already known.
        Avoids an extra DB call for get_only_pings().
        """
        if not only_pings_enabled:
            return True

        return (
            bool(ping_data.get("hunters"))
            or bool(ping_data.get("collectors"))
            or bool(ping_data.get("rare_ping"))
            or bool(ping_data.get("regional_ping"))
            or bool(ping_data.get("type_pingers"))
            or bool(ping_data.get("rgn_pingers"))
        )

    # ------------------------------------------------------------------
    # Secondary model logging
    # ------------------------------------------------------------------
    async def log_secondary_model_prediction(self, name, confidence, model_used, message, image_url):
        if model_used not in ["secondary", "primary_fallback"]:
            return

        secondary_channel_id = await self.db.get_secondary_model_channel()
        if not secondary_channel_id:
            return

        secondary_channel = self.bot.get_channel(secondary_channel_id)
        if not secondary_channel:
            return

        try:
            model_label = (
                "Secondary Model (High Confidence)"
                if model_used == "secondary"
                else "Secondary Model Used (Fallback to Primary)"
            )

            embed = discord.Embed(
                title=f"🔬 {model_label}",
                description=(
                    f"**Pokemon:** {name}\n"
                    f"**Confidence:** {confidence}\n"
                    f"**Server:** {message.guild.name}\n"
                    f"**Channel:** {message.channel.mention}"
                ),
                color=0x00bfff
            )

            if image_url:
                embed.set_thumbnail(url=image_url)

            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Jump to Message",
                url=message.jump_url,
                emoji="🔗",
                style=discord.ButtonStyle.link
            ))

            await secondary_channel.send(embed=embed, view=view)
            print(f"[SECONDARY-MODEL] Logged: {name} ({confidence}) - {model_used}")

        except Exception as e:
            print(f"[SECONDARY-MODEL] Failed to log: {e}")

    # ------------------------------------------------------------------
    # p!predict command
    # ------------------------------------------------------------------
    @commands.command(name="predict", aliases=["pred", "p"])
    async def predict_command(self, ctx, *, image_url: str = None):
        """Predict Pokemon from image URL or replied message"""
        if not image_url and ctx.message.reference:
            try:
                replied_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                image_url = await self.extract_image_url(replied_message)
            except discord.NotFound:
                await ctx.reply("Could not find the replied message.", mention_author=False)
                return
            except discord.Forbidden:
                await ctx.reply("I don't have permission to access that message.", mention_author=False)
                return
            except Exception as e:
                await ctx.reply(f"Error fetching replied message: {str(e)[:100]}", mention_author=False)
                return

        if not image_url:
            await ctx.reply(
                "Please provide an image URL after p!predict or reply to a message with an image.",
                mention_author=False
            )
            return

        result = await self._predict_pokemon(image_url, ctx.guild.id)
        await ctx.reply(result, mention_author=False, allowed_mentions=SAFE_MENTIONS)

    # ------------------------------------------------------------------
    # on_message listener
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return
        if not message.guild:
            return
        if self.predictor is None:
            return

        # ---- Auto-predict channel ----------------------------------------
        if AUTO_PREDICT_CHANNEL_ID and message.channel.id == AUTO_PREDICT_CHANNEL_ID:
            image_url = await self.extract_image_url(message)

            if image_url:
                try:
                    cache_key = self.predictor._generate_cache_key(image_url)
                    cached_result = self.predictor.cache.get(cache_key)

                    if cached_result:
                        name, confidence, model_used = cached_result
                    else:
                        name, confidence = await self.predictor.predict(image_url, self.http_session)
                        if hasattr(self.bot, 'prediction_count'):
                            self.bot.prediction_count += 1
                        cached_result = self.predictor.cache.get(cache_key)
                        model_used = cached_result[2] if cached_result else "unknown"

                    if name and confidence:
                        show_best = await self.db.get_best_name(message.guild.id)
                        output = await self.build_prediction_output(
                            name, confidence, message.guild.id, show_best_name=show_best
                        )
                        await message.reply(output, allowed_mentions=SAFE_MENTIONS)

                        await self.log_secondary_model_prediction(
                            name, confidence, model_used, message, image_url
                        )

                except ValueError as e:
                    error_msg = str(e)
                    if "404" in error_msg or "Failed to load image" in error_msg:
                        print(f"[AUTO-PREDICT] Image not accessible: {image_url[:100]}")
                    else:
                        print(f"[AUTO-PREDICT] ValueError: {e}")
                except Exception as e:
                    print(f"[AUTO-PREDICT] Error: {e}")
                    import traceback
                    traceback.print_exc()

        # ---- Poketwo spawn detection in other channels --------------------
        elif message.author.id == POKETWO_USER_ID:
            if message.embeds:
                embed = message.embeds[0]
                if embed.title:
                    if (embed.title == "A wild pokémon has appeared!" or
                            (embed.title.endswith("A new wild pokémon has appeared!") and
                             "fled." in embed.title)):

                        image_url = await self.extract_image_url(message)

                        if image_url:
                            try:
                                cache_key = self.predictor._generate_cache_key(image_url)
                                cached_result = self.predictor.cache.get(cache_key)

                                if cached_result:
                                    name, confidence, model_used = cached_result
                                else:
                                    name, confidence = await self.predictor.predict(image_url, self.http_session)
                                    if hasattr(self.bot, 'prediction_count'):
                                        self.bot.prediction_count += 1
                                    cached_result = self.predictor.cache.get(cache_key)
                                    model_used = cached_result[2] if cached_result else "unknown"

                                if name and confidence:
                                    confidence_str = str(confidence).rstrip('%')
                                    try:
                                        confidence_value = float(confidence_str)

                                        # ── Fetch everything needed in parallel ──────────────
                                        # ping data (all DB calls batched) + only_pings + best_name
                                        ping_data, only_pings_enabled, show_best = await asyncio.gather(
                                            self._get_all_ping_data(name, message.guild.id),
                                            self.db.get_only_pings(message.guild.id),
                                            self.db.get_best_name(message.guild.id),
                                        )

                                        should_send = self.should_send_prediction_from_data(
                                            only_pings_enabled, ping_data
                                        )

                                        if should_send:
                                            lines = [format_pokemon_prediction(name, confidence)]

                                            if show_best:
                                                best = get_best_name(name)
                                                if best:
                                                    lines.append(f"Shortest Name: {best}")

                                            if ping_data["rare_ping"]:
                                                lines.append(f"Rare Ping: {ping_data['rare_ping']}")
                                            if ping_data["regional_ping"]:
                                                lines.append(f"Regional Pings: {ping_data['regional_ping']}")
                                            if ping_data["hunters"]:
                                                lines.append(f"Shiny Hunters: {' '.join(ping_data['hunters'])}")
                                            if ping_data["collectors"]:
                                                collector_mentions = " ".join(
                                                    [f"<@{uid}>" for uid in ping_data["collectors"]]
                                                )
                                                lines.append(f"Collectors: {collector_mentions}")
                                            if ping_data["type_pingers"]:
                                                type_mentions = " ".join(
                                                    [f"<@{uid}>" for uid in ping_data["type_pingers"]]
                                                )
                                                lines.append(f"Type Pings: {type_mentions}")
                                            if ping_data["rgn_pingers"]:
                                                rgn_mentions = " ".join(
                                                    [f"<@{uid}>" for uid in ping_data["rgn_pingers"]]
                                                )
                                                lines.append(f"Region Pings: {rgn_mentions}")

                                            await message.channel.send(
                                                "\n".join(lines),
                                                reference=message,
                                                mention_author=False,
                                                allowed_mentions=SAFE_MENTIONS
                                            )

                                        # Low confidence channel
                                        if confidence_value < PREDICTION_CONFIDENCE:
                                            low_channel_id = await self.db.get_low_prediction_channel()
                                            if low_channel_id:
                                                low_channel = self.bot.get_channel(low_channel_id)
                                                if low_channel:
                                                    low_embed = discord.Embed(
                                                        title="Low Confidence Prediction",
                                                        description=(
                                                            f"**Pokemon:** {name}\n"
                                                            f"**Confidence:** {confidence}\n"
                                                            f"**Server:** {message.guild.name}\n"
                                                            f"**Channel:** {message.channel.mention}"
                                                        ),
                                                        color=0xff9900
                                                    )
                                                    if image_url:
                                                        low_embed.set_thumbnail(url=image_url)

                                                    low_view = discord.ui.View()
                                                    low_view.add_item(discord.ui.Button(
                                                        label="Jump to Message",
                                                        url=message.jump_url,
                                                        emoji="🔗",
                                                        style=discord.ButtonStyle.link
                                                    ))
                                                    await low_channel.send(embed=low_embed, view=low_view)

                                        await self.log_secondary_model_prediction(
                                            name, confidence, model_used, message, image_url
                                        )

                                    except ValueError:
                                        print(f"Could not parse confidence value: {confidence}")

                            except ValueError as e:
                                error_msg = str(e)
                                if "404" in error_msg or "Failed to load image" in error_msg:
                                    print(f"[POKETWO-SPAWN] Image not accessible: {image_url[:100]}")
                                else:
                                    print(f"[POKETWO-SPAWN] ValueError: {e}")
                            except Exception as e:
                                print(f"Auto-detection error: {e}")


async def setup(bot):
    await bot.add_cog(Prediction(bot))
