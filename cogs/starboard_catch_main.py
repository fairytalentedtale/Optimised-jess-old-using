"""Starboard logging for Pokemon catches"""
import discord
import re
from datetime import datetime
from discord.ext import commands
from config import POKETWO_USER_ID, EMBED_COLOR, HIGH_IV_THRESHOLD, LOW_IV_THRESHOLD
from starboard_utils import (
    get_gender_emoji,
    find_pokemon_image_url,
    format_iv_display,
    create_jump_button_view
)

class StarboardCatch(commands.Cog):
    """Automatic logging of Pokemon catches to starboard channels"""
    
    def __init__(self, bot):
        self.bot = bot
    
    @property
    def db(self):
        """Get database from bot"""
        return self.bot.db
    
    def parse_poketwo_catch_message(self, message_content: str) -> dict:
        """Parse Poketwo catch message to extract information"""
        catch_pattern = r"Congratulations <@!?(\d+)>! You caught a Level (\d+) (.+?)(?:\s+\((\d+\.?\d*)%\))?!"
        
        match = re.search(catch_pattern, message_content)
        if not match:
            return None
        
        user_id = match.group(1)
        level = match.group(2)
        pokemon_name_with_gender = match.group(3).strip()
        iv_str = match.group(4)
        
        iv = iv_str if iv_str else "Hidden"
        
        gender = None
        pokemon_name = pokemon_name_with_gender
        
        if re.search(r'<:male:\d+>', message_content):
            gender = 'male'
            pokemon_name = re.sub(r'<:male:\d+>', '', pokemon_name_with_gender).strip()
        elif re.search(r'<:female:\d+>', message_content):
            gender = 'female'
            pokemon_name = re.sub(r'<:female:\d+>', '', pokemon_name_with_gender).strip()
        elif re.search(r'<:unknown:\d+>', message_content):
            gender = 'unknown'
            pokemon_name = re.sub(r'<:unknown:\d+>', '', pokemon_name_with_gender).strip()
        
        is_shiny = "These colors seem unusual... ✨" in message_content
        is_gigantamax = "Woah! It seems that this pokémon has the Gigantamax Factor..." in message_content
        
        shiny_chain = None
        chain_pattern = r"Shiny streak reset\. \(\*\*(\d+)\*\*\)"
        chain_match = re.search(chain_pattern, message_content)
        if chain_match:
            shiny_chain = chain_match.group(1)
        
        return {
            'user_id': user_id,
            'level': level,
            'pokemon_name': pokemon_name,
            'iv': iv,
            'is_shiny': is_shiny,
            'is_gigantamax': is_gigantamax,
            'shiny_chain': shiny_chain,
            'gender': gender,
            'message_type': 'catch'
        }
    
    def parse_poketwo_missingno_message(self, message_content: str) -> dict:
        """Parse Poketwo MissingNo catch message"""
        missingno_pattern1 = r"Congratulations <@!?(\d+)>! You caught a Level \?\?\? MissingNo\.(?:<:[^:]+:\d+>)? \(\?\?\?%\)!"
        missingno_pattern2 = r"Congratulations <@!?(\d+)>! You caught a Level \?\?\? MissingNo\.(?:<:[^:]+:\d+>)!"
        
        match = re.search(missingno_pattern1, message_content) or re.search(missingno_pattern2, message_content)
        if not match:
            return None
        
        user_id = match.group(1)
        
        gender = None
        if re.search(r'<:male:\d+>', message_content):
            gender = 'male'
        elif re.search(r'<:female:\d+>', message_content):
            gender = 'female'
        elif re.search(r'<:unknown:\d+>', message_content):
            gender = 'unknown'
        
        is_shiny = "These colors seem unusual... ✨" in message_content
        
        return {
            'user_id': user_id,
            'level': '???',
            'pokemon_name': 'MissingNo.',
            'iv': '???',
            'is_shiny': is_shiny,
            'is_gigantamax': False,
            'gender': gender,
            'message_type': 'missingno'
        }
    
    def create_catch_embed(self, catch_data: dict, original_message: discord.Message = None) -> discord.Embed:
        """Create embed for catch"""
        pokemon_name = catch_data['pokemon_name']
        level = catch_data['level']
        iv = catch_data['iv']
        is_shiny = catch_data['is_shiny']
        is_gigantamax = catch_data['is_gigantamax']
        gender = catch_data.get('gender')
        user_id = catch_data['user_id']
        shiny_chain = catch_data.get('shiny_chain')
        message_type = catch_data.get('message_type', 'catch')
        
        iv_display = format_iv_display(iv)
        gender_emoji = get_gender_emoji(gender)
        
        # Build display name
        if is_gigantamax:
            # Eternatus with Gigantamax factor is called Eternamax Eternatus
            if pokemon_name.lower() == "eternatus":
                display_pokemon_name = "Eternamax Eternatus"
            else:
                display_pokemon_name = f"Gigantamax {pokemon_name}"
        else:
            display_pokemon_name = pokemon_name
        
        if gender_emoji:
            pokemon_display = f"{display_pokemon_name} {gender_emoji}"
        else:
            pokemon_display = display_pokemon_name
        
        # find_pokemon_image_url handles gigantamax/eternamax lookup internally
        image_url = find_pokemon_image_url(pokemon_name, is_shiny, gender, is_gigantamax)
        
        embed = discord.Embed(color=EMBED_COLOR, timestamp=datetime.utcnow())
        
        if message_type == 'missingno':
            if is_shiny:
                embed.title = "✨ Shiny MissingNo. Detected ✨"
            else:
                from config import Emojis
                embed.title = f"{Emojis.MISSINGNO} MissingNo. Detected {Emojis.MISSINGNO}"
            embed.description = f"**Caught By:** <@{user_id}>\n**Pokémon:** {pokemon_display}\n**Level:** ???\n**IV:** {iv_display}"
        
        else:
            title_parts = []
            
            if is_shiny:
                title_parts.append("✨ Shiny")
            
            if is_gigantamax:
                from config import Emojis
                if pokemon_name.lower() == "eternatus":
                    title_parts.append(f"{Emojis.GIGANTAMAX} Eternamax")
                else:
                    title_parts.append(f"{Emojis.GIGANTAMAX} Gigantamax")
            
            if iv != "Hidden" and iv != "???":
                try:
                    iv_value = float(iv)
                    if iv_value >= HIGH_IV_THRESHOLD:
                        title_parts.append("📈 High IV")
                    elif iv_value <= LOW_IV_THRESHOLD:
                        title_parts.append("📉 Low IV")
                except ValueError:
                    pass
            
            embed.title = (" ".join(title_parts) + " Catch Detected") if title_parts else "Rare Catch Detected"
            embed.description = f"**Caught By:** <@{user_id}>\n**Pokémon:** {pokemon_display}\n**Level:** {level}\n**IV:** {iv_display}"
            
            if shiny_chain:
                embed.description += f"\n**Chain:** {shiny_chain}"
        
        if image_url:
            embed.set_thumbnail(url=image_url)
        
        return embed
    
    async def send_to_starboard_channels(self, guild: discord.Guild, catch_data: dict, original_message: discord.Message = None):
        """Send catch to appropriate starboard channels"""
        is_shiny = catch_data['is_shiny']
        is_gigantamax = catch_data['is_gigantamax']
        iv = catch_data['iv']
        message_type = catch_data.get('message_type', 'catch')
        pokemon_name = catch_data['pokemon_name']
        
        settings = await self.db.get_guild_settings(guild.id)
        channels_to_send = []
        
        if message_type == 'missingno':
            missingno_channel_id = settings.get('starboard_missingno_channel_id')
            if missingno_channel_id:
                channels_to_send.append(missingno_channel_id)
        else:
            catch_channel_id = settings.get('starboard_catch_channel_id')
            if catch_channel_id:
                channels_to_send.append(catch_channel_id)
            
            if is_shiny:
                shiny_channel_id = settings.get('starboard_shiny_channel_id')
                if shiny_channel_id and shiny_channel_id not in channels_to_send:
                    channels_to_send.append(shiny_channel_id)
            
            if is_gigantamax:
                gmax_channel_id = settings.get('starboard_gigantamax_channel_id')
                if gmax_channel_id and gmax_channel_id not in channels_to_send:
                    channels_to_send.append(gmax_channel_id)
            
            # Eternatus doesn't show IV alongside Gigantamax factor
            if pokemon_name.lower() != "eternatus" and iv not in ["Hidden", "???"]:
                try:
                    iv_value = float(iv)
                    if iv_value >= HIGH_IV_THRESHOLD:
                        highiv_channel_id = settings.get('starboard_highiv_channel_id')
                        if highiv_channel_id and highiv_channel_id not in channels_to_send:
                            channels_to_send.append(highiv_channel_id)
                    elif iv_value <= LOW_IV_THRESHOLD:
                        lowiv_channel_id = settings.get('starboard_lowiv_channel_id')
                        if lowiv_channel_id and lowiv_channel_id not in channels_to_send:
                            channels_to_send.append(lowiv_channel_id)
                except ValueError:
                    pass
        
        embed = self.create_catch_embed(catch_data, original_message)
        view = create_jump_button_view(original_message)
        
        for channel_id in channels_to_send:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(embed=embed, view=view)
                except Exception as e:
                    print(f"Error sending to starboard channel {channel_id}: {e}")
        
        global_catch_channel_id = await self.db.get_global_starboard_catch_channel()
        if global_catch_channel_id:
            global_channel = self.bot.get_channel(global_catch_channel_id)
            if global_channel:
                try:
                    await global_channel.send(embed=embed, view=view)
                except Exception as e:
                    print(f"Error sending to global starboard channel: {e}")
    
    def should_log_catch(self, catch_data: dict) -> bool:
        """Determine if catch should be logged based on criteria"""
        is_shiny = catch_data['is_shiny']
        is_gigantamax = catch_data['is_gigantamax']
        iv = catch_data['iv']
        message_type = catch_data.get('message_type', 'catch')
        
        if message_type == 'missingno':
            return True
        if is_shiny or is_gigantamax:
            return True
        if iv not in ["Hidden", "???"]:
            try:
                iv_value = float(iv)
                if iv_value >= HIGH_IV_THRESHOLD or iv_value <= LOW_IV_THRESHOLD:
                    return True
            except ValueError:
                pass
        return False
    
    @commands.command(name="catchcheck", aliases=["cc", "checkcatch"])
    @commands.has_permissions(administrator=True)
    async def catch_check_command(self, ctx, *, input_data: str = None):
        """Manually check a Poketwo catch message and send to starboard
        
        Usage:
            p!catchcheck (reply to a message)
            p!catchcheck <message_id>
            p!catchcheck Congratulations <@123>! You caught...
        """
        original_message = None
        catch_message = None
        
        if input_data is None:
            if ctx.message.reference and ctx.message.reference.resolved:
                catch_message = ctx.message.reference.resolved.content
                original_message = ctx.message.reference.resolved
            else:
                await ctx.reply(
                    "Please provide a Poketwo catch message, message ID, or reply to one.\n"
                    "Examples:\n"
                    "`p!catchcheck 123456789012345678` (message ID)\n"
                    "Or reply to a message with just `p!catchcheck`",
                    mention_author=False
                )
                return
        else:
            if input_data.strip().isdigit():
                message_id = int(input_data.strip())
                try:
                    try:
                        original_message = await ctx.channel.fetch_message(message_id)
                    except discord.NotFound:
                        found_message = None
                        for channel in ctx.guild.text_channels:
                            if channel.permissions_for(ctx.guild.me).read_message_history:
                                try:
                                    found_message = await channel.fetch_message(message_id)
                                    original_message = found_message
                                    break
                                except (discord.NotFound, discord.Forbidden):
                                    continue
                        if not found_message:
                            await ctx.reply(f"❌ Could not find message with ID `{message_id}` in this server.", mention_author=False)
                            return
                    
                    catch_message = original_message.content
                    if original_message.author.id != POKETWO_USER_ID:
                        await ctx.reply(f"❌ The message with ID `{message_id}` is not from Poketwo.", mention_author=False)
                        return
                except ValueError:
                    await ctx.reply(f"❌ Invalid message ID: `{input_data.strip()}`", mention_author=False)
                    return
                except Exception as e:
                    await ctx.reply(f"❌ Error fetching message: {str(e)}", mention_author=False)
                    return
            else:
                catch_message = input_data
        
        catch_data = self.parse_poketwo_missingno_message(catch_message)
        if not catch_data:
            catch_data = self.parse_poketwo_catch_message(catch_message)
        
        if not catch_data:
            await ctx.reply("❌ Invalid message format. Please make sure it's a proper Poketwo catch message.", mention_author=False)
            return
        
        if not self.should_log_catch(catch_data):
            gender_emoji = get_gender_emoji(catch_data.get('gender'))
            pokemon_display = f"{catch_data['pokemon_name']} {gender_emoji}" if gender_emoji else catch_data['pokemon_name']
            iv_display = format_iv_display(catch_data['iv'])
            await ctx.reply(
                f"❌ This catch doesn't meet starboard criteria.\n"
                f"**Pokémon:** {pokemon_display}\n"
                f"**Level:** {catch_data['level']}\n"
                f"**IV:** {iv_display}\n"
                f"**Shiny:** {'Yes' if catch_data['is_shiny'] else 'No'}\n"
                f"**Gigantamax:** {'Yes' if catch_data['is_gigantamax'] else 'No'}\n\n"
                f"**Criteria:** Shiny, Gigantamax, MissingNo, or IV ≥{HIGH_IV_THRESHOLD}% or ≤{LOW_IV_THRESHOLD}%",
                mention_author=False
            )
            return
        
        await self.send_to_starboard_channels(ctx.guild, catch_data, original_message)
        
        criteria_met = []
        if catch_data.get('message_type') == 'missingno':
            criteria_met.append("❓ MissingNo.")
            if catch_data['is_shiny']:
                criteria_met.append("✨ Shiny")
        else:
            if catch_data['is_shiny']:
                criteria_met.append("✨ Shiny")
            if catch_data['is_gigantamax']:
                from config import Emojis
                criteria_met.append(f"{Emojis.GIGANTAMAX} Gigantamax")
            iv = catch_data['iv']
            if iv not in ["Hidden", "???"]:
                try:
                    iv_value = float(iv)
                    if iv_value >= HIGH_IV_THRESHOLD:
                        criteria_met.append(f"📈 High IV ({iv}%)")
                    elif iv_value <= LOW_IV_THRESHOLD:
                        criteria_met.append(f"📉 Low IV ({iv}%)")
                except ValueError:
                    pass
        
        gender_emoji = get_gender_emoji(catch_data.get('gender'))
        pokemon_display = f"{catch_data['pokemon_name']} {gender_emoji}" if gender_emoji else catch_data['pokemon_name']
        iv_display = format_iv_display(catch_data['iv'])
        
        await ctx.reply(
            f"✅ Catch sent to starboard!\n"
            f"**Criteria met:** {', '.join(criteria_met)}\n"
            f"**Pokémon:** {pokemon_display} (Level {catch_data['level']}, {iv_display})",
            mention_author=False
        )
    
    @catch_check_command.error
    async def catch_check_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("❌ You need administrator permissions to use this command.", mention_author=False)
        else:
            print(f"Unexpected error in catchcheck: {error}")
            await ctx.reply("❌ An unexpected error occurred. Please try again.", mention_author=False)
    
    @commands.Cog.listener()
    async def on_message(self, message):
        """Listen for Poketwo catch messages"""
        if message.author.id != POKETWO_USER_ID:
            return
        
        catch_data = None
        
        if "MissingNo." in message.content:
            catch_data = self.parse_poketwo_missingno_message(message.content)
        elif message.content.startswith("Congratulations"):
            catch_data = self.parse_poketwo_catch_message(message.content)
        
        if not catch_data:
            return
        
        if self.should_log_catch(catch_data):
            await self.send_to_starboard_channels(message.guild, catch_data, message)

async def setup(bot):
    await bot.add_cog(StarboardCatch(bot))
