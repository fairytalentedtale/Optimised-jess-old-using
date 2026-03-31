"""Database operations and connection management"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from typing import List, Optional
from config import MONGODB_URI, DB_TIMEOUT_MS, DB_MAX_POOL_SIZE, DB_MIN_POOL_SIZE

class Database:
    def __init__(self):
        self.client = None
        self.db = None

    async def connect(self):
        """Initialize MongoDB connection"""
        try:
            if not MONGODB_URI:
                print("Warning: MONGODB_URI not set, database features disabled")
                return False

            connection_config = {
                "serverSelectionTimeoutMS": DB_TIMEOUT_MS,
                "connectTimeoutMS": 5000,
                "socketTimeoutMS": 10000,
                "maxPoolSize": DB_MAX_POOL_SIZE,
                "minPoolSize": DB_MIN_POOL_SIZE,
                "maxIdleTimeMS": 30000,
                "retryWrites": True,
                "w": "majority"
            }

            self.client = AsyncIOMotorClient(MONGODB_URI, **connection_config)
            await asyncio.wait_for(self.client.admin.command('ping'), timeout=3)
            self.db = self.client.pokemon_collector

            await self._create_indexes()
            print("✅ Database connected successfully")
            return True

        except asyncio.TimeoutError:
            print("❌ Database connection timeout - features disabled")
            return False
        except Exception as e:
            print(f"❌ Database connection failed: {str(e)[:100]}")
            return False

    async def _create_indexes(self):
        """Create database indexes for better performance"""
        try:
            # Collections
            await self.db.collections.create_index([("user_id", 1), ("guild_id", 1)])
            await self.db.collections.create_index("pokemon")

            # Shiny hunts
            await self.db.shiny_hunts.create_index([("user_id", 1), ("guild_id", 1)])
            await self.db.shiny_hunts.create_index("pokemon")

            # Global AFK users (user_id only, no guild_id)
            await self.db.collection_afk_users.create_index("user_id", unique=True)
            await self.db.shiny_hunt_afk_users.create_index("user_id", unique=True)

            # Rare pings
            await self.db.rare_pings.create_index([("user_id", 1), ("guild_id", 1)])

            # Guild settings
            await self.db.guild_settings.create_index("guild_id", unique=True)

            # Categories
            await self.db.categories.create_index([("guild_id", 1), ("name_lower", 1)], unique=True)

            # Type pings — stored per-user, per-guild: {user_id, guild_id, types: [...]}
            await self.db.type_pings.create_index([("user_id", 1), ("guild_id", 1)], unique=True)
            await self.db.type_pings.create_index("types")

            # Region pings — stored per-user, per-guild: {user_id, guild_id, regions: [...]}
            await self.db.region_pings.create_index([("user_id", 1), ("guild_id", 1)], unique=True)
            await self.db.region_pings.create_index("regions")

            # Unified user prefs AFK (type/region AFK stored in a single user_prefs collection)
            await self.db.user_prefs.create_index("user_id", unique=True)

            print("✅ Database indexes created")
        except Exception as e:
            print(f"Warning: Could not create indexes: {e}")

    def close(self):
        """Close database connection"""
        if self.client:
            self.client.close()

    # -------------------------------------------------------------------------
    # Collection operations
    # -------------------------------------------------------------------------
    async def add_pokemon_to_collection(self, user_id: int, guild_id: int, pokemon_names: List[str]):
        """Add Pokemon to user's collection"""
        await self.db.collections.update_one(
            {"user_id": user_id, "guild_id": guild_id},
            {"$addToSet": {"pokemon": {"$each": pokemon_names}}},
            upsert=True
        )

    async def remove_pokemon_from_collection(self, user_id: int, guild_id: int, pokemon_names: List[str]):
        """Remove Pokemon from user's collection"""
        result = await self.db.collections.update_one(
            {"user_id": user_id, "guild_id": guild_id},
            {"$pullAll": {"pokemon": pokemon_names}}
        )
        return result.modified_count > 0

    async def clear_collection(self, user_id: int, guild_id: int):
        """Clear user's entire collection"""
        result = await self.db.collections.delete_one(
            {"user_id": user_id, "guild_id": guild_id}
        )
        return result.deleted_count > 0

    async def get_user_collection(self, user_id: int, guild_id: int) -> List[str]:
        """Get user's collection"""
        collection = await self.db.collections.find_one(
            {"user_id": user_id, "guild_id": guild_id}
        )
        return collection.get('pokemon', []) if collection else []

    async def get_collectors_for_pokemon(self, guild_id: int, pokemon_names: List[str], afk_users: List[int]) -> List[int]:
        """Get all users who have collected any of the Pokemon names"""
        afk_users_set = set(afk_users)
        collectors = []

        collections = await self.db.collections.find(
            {
                "guild_id": guild_id,
                "pokemon": {"$in": pokemon_names}
            },
            {"user_id": 1}
        ).to_list(length=None)

        for collection in collections:
            user_id = collection['user_id']
            if user_id not in afk_users_set:
                collectors.append(user_id)

        return collectors

    # -------------------------------------------------------------------------
    # Shiny hunt operations
    # -------------------------------------------------------------------------
    async def set_shiny_hunt(self, user_id: int, guild_id: int, pokemon_names):
        """Set user's shiny hunt - supports single Pokemon or list of variants"""
        if isinstance(pokemon_names, str):
            pokemon_names = [pokemon_names]

        await self.db.shiny_hunts.update_one(
            {"user_id": user_id, "guild_id": guild_id},
            {"$set": {"pokemon": pokemon_names}},
            upsert=True
        )

    async def clear_shiny_hunt(self, user_id: int, guild_id: int):
        """Clear user's shiny hunt"""
        result = await self.db.shiny_hunts.delete_one(
            {"user_id": user_id, "guild_id": guild_id}
        )
        return result.deleted_count > 0

    async def get_user_shiny_hunt(self, user_id: int, guild_id: int):
        """Get user's current shiny hunt"""
        hunt = await self.db.shiny_hunts.find_one(
            {"user_id": user_id, "guild_id": guild_id}
        )
        if not hunt:
            return None

        pokemon = hunt.get('pokemon')
        if isinstance(pokemon, str):
            return [pokemon]

        return pokemon if pokemon else None

    async def get_shiny_hunters_for_pokemon(self, guild_id: int, pokemon_names: List[str], afk_users: List[int]) -> List[tuple]:
        """Get all users hunting any of the Pokemon names"""
        afk_users_set = set(afk_users)
        pokemon_names_set = set(pokemon_names)
        hunters = []

        hunts = await self.db.shiny_hunts.find(
            {"guild_id": guild_id},
            {"user_id": 1, "pokemon": 1}
        ).to_list(length=None)

        for hunt in hunts:
            user_id = hunt['user_id']
            hunt_pokemon = hunt.get('pokemon', [])

            if isinstance(hunt_pokemon, str):
                hunt_pokemon = [hunt_pokemon]

            if any(spawned in hunt_pokemon for spawned in pokemon_names_set):
                hunters.append((user_id, user_id in afk_users_set))

        return hunters

    # -------------------------------------------------------------------------
    # Global AFK operations
    # -------------------------------------------------------------------------
    async def get_collection_afk_users(self) -> List[int]:
        """Get list of global collection AFK users"""
        afk_docs = await self.db.collection_afk_users.find(
            {"afk": True},
            {"user_id": 1}
        ).to_list(length=None)
        return [doc['user_id'] for doc in afk_docs]

    async def get_shiny_hunt_afk_users(self) -> List[int]:
        """Get list of global shiny hunt AFK users"""
        afk_docs = await self.db.shiny_hunt_afk_users.find(
            {"afk": True},
            {"user_id": 1}
        ).to_list(length=None)
        return [doc['user_id'] for doc in afk_docs]

    async def toggle_collection_afk(self, user_id: int) -> bool:
        """Toggle global collection AFK status. Returns new state"""
        current = await self.db.collection_afk_users.find_one({"user_id": user_id})

        if current and current.get('afk'):
            await self.db.collection_afk_users.delete_one({"user_id": user_id})
            return False
        else:
            await self.db.collection_afk_users.update_one(
                {"user_id": user_id},
                {"$set": {"afk": True}},
                upsert=True
            )
            return True

    async def toggle_shiny_hunt_afk(self, user_id: int) -> bool:
        """Toggle global shiny hunt AFK status. Returns new state"""
        current = await self.db.shiny_hunt_afk_users.find_one({"user_id": user_id})

        if current and current.get('afk'):
            await self.db.shiny_hunt_afk_users.delete_one({"user_id": user_id})
            return False
        else:
            await self.db.shiny_hunt_afk_users.update_one(
                {"user_id": user_id},
                {"$set": {"afk": True}},
                upsert=True
            )
            return True

    async def is_collection_afk(self, user_id: int) -> bool:
        """Check if user is globally collection AFK"""
        afk_doc = await self.db.collection_afk_users.find_one({"user_id": user_id})
        return afk_doc and afk_doc.get('afk', False)

    async def is_shiny_hunt_afk(self, user_id: int) -> bool:
        """Check if user is globally shiny hunt AFK"""
        afk_doc = await self.db.shiny_hunt_afk_users.find_one({"user_id": user_id})
        return afk_doc and afk_doc.get('afk', False)

    # -------------------------------------------------------------------------
    # Type/Region pings AFK (stored in user_prefs)
    # -------------------------------------------------------------------------
    async def _get_user_prefs(self, user_id: int) -> dict:
        doc = await self.db.user_prefs.find_one({"user_id": user_id})
        return doc or {}

    async def is_type_ping_afk(self, user_id: int) -> bool:
        prefs = await self._get_user_prefs(user_id)
        return prefs.get('type_ping_afk', False)

    async def is_region_ping_afk(self, user_id: int) -> bool:
        prefs = await self._get_user_prefs(user_id)
        return prefs.get('region_ping_afk', False)

    async def toggle_type_ping_afk(self, user_id: int) -> bool:
        """Toggle type ping AFK. Returns new state."""
        prefs = await self._get_user_prefs(user_id)
        new_val = not prefs.get('type_ping_afk', False)
        await self.db.user_prefs.update_one(
            {"user_id": user_id},
            {"$set": {"type_ping_afk": new_val}},
            upsert=True
        )
        return new_val

    async def toggle_region_ping_afk(self, user_id: int) -> bool:
        """Toggle region ping AFK. Returns new state."""
        prefs = await self._get_user_prefs(user_id)
        new_val = not prefs.get('region_ping_afk', False)
        await self.db.user_prefs.update_one(
            {"user_id": user_id},
            {"$set": {"region_ping_afk": new_val}},
            upsert=True
        )
        return new_val

    async def get_type_region_afk_users(self) -> dict:
        """Return {user_id: {'type': bool, 'region': bool}} for all users with any AFK set."""
        docs = await self.db.user_prefs.find(
            {"$or": [{"type_ping_afk": True}, {"region_ping_afk": True}]},
            {"user_id": 1, "type_ping_afk": 1, "region_ping_afk": 1}
        ).to_list(length=None)
        result = {}
        for doc in docs:
            result[doc['user_id']] = {
                'type': doc.get('type_ping_afk', False),
                'region': doc.get('region_ping_afk', False)
            }
        return result

    # -------------------------------------------------------------------------
    # Type pings
    # -------------------------------------------------------------------------
    async def get_user_type_pings(self, user_id: int, guild_id: int) -> List[str]:
        """Get types a user wants pings for in this guild"""
        doc = await self.db.type_pings.find_one({"user_id": user_id, "guild_id": guild_id})
        return doc.get('types', []) if doc else []

    async def set_user_type_pings(self, user_id: int, guild_id: int, types: List[str]):
        """Replace user's type ping list"""
        if types:
            await self.db.type_pings.update_one(
                {"user_id": user_id, "guild_id": guild_id},
                {"$set": {"types": types}},
                upsert=True
            )
        else:
            await self.db.type_pings.delete_one({"user_id": user_id, "guild_id": guild_id})

    async def toggle_user_type_ping(self, user_id: int, guild_id: int, pokemon_type: str) -> bool:
        """Toggle a single type. Returns True if now enabled, False if disabled."""
        doc = await self.db.type_pings.find_one({"user_id": user_id, "guild_id": guild_id})
        current = doc.get('types', []) if doc else []

        if pokemon_type in current:
            current.remove(pokemon_type)
            enabled = False
        else:
            current.append(pokemon_type)
            enabled = True

        await self.set_user_type_pings(user_id, guild_id, current)
        return enabled

    async def get_users_for_types(self, guild_id: int, pokemon_types: List[str], afk_user_ids: set) -> List[int]:
        """Get users in guild who want pings for any of the given types, excluding AFK users."""
        if not pokemon_types:
            return []
        docs = await self.db.type_pings.find(
            {"guild_id": guild_id, "types": {"$in": pokemon_types}},
            {"user_id": 1}
        ).to_list(length=None)
        return [d['user_id'] for d in docs if d['user_id'] not in afk_user_ids]

    # -------------------------------------------------------------------------
    # Region pings
    # -------------------------------------------------------------------------
    async def get_user_region_pings(self, user_id: int, guild_id: int) -> List[str]:
        """Get regions a user wants pings for in this guild"""
        doc = await self.db.region_pings.find_one({"user_id": user_id, "guild_id": guild_id})
        return doc.get('regions', []) if doc else []

    async def set_user_region_pings(self, user_id: int, guild_id: int, regions: List[str]):
        """Replace user's region ping list"""
        if regions:
            await self.db.region_pings.update_one(
                {"user_id": user_id, "guild_id": guild_id},
                {"$set": {"regions": regions}},
                upsert=True
            )
        else:
            await self.db.region_pings.delete_one({"user_id": user_id, "guild_id": guild_id})

    async def toggle_user_region_ping(self, user_id: int, guild_id: int, region: str) -> bool:
        """Toggle a single region. Returns True if now enabled, False if disabled."""
        doc = await self.db.region_pings.find_one({"user_id": user_id, "guild_id": guild_id})
        current = doc.get('regions', []) if doc else []

        if region in current:
            current.remove(region)
            enabled = False
        else:
            current.append(region)
            enabled = True

        await self.set_user_region_pings(user_id, guild_id, current)
        return enabled

    async def get_users_for_regions(self, guild_id: int, pokemon_regions: List[str], afk_user_ids: set) -> List[int]:
        """Get users in guild who want pings for any of the given regions, excluding AFK users."""
        if not pokemon_regions:
            return []
        docs = await self.db.region_pings.find(
            {"guild_id": guild_id, "regions": {"$in": pokemon_regions}},
            {"user_id": 1}
        ).to_list(length=None)
        return [d['user_id'] for d in docs if d['user_id'] not in afk_user_ids]

    # -------------------------------------------------------------------------
    # Rare pings
    # -------------------------------------------------------------------------
    async def get_rare_collectors(self, guild_id: int, afk_users: List[int]) -> List[int]:
        """Get users who want rare pings"""
        afk_users_set = set(afk_users)
        collectors = []

        rare_users = await self.db.rare_pings.find(
            {"guild_id": guild_id, "enabled": True},
            {"user_id": 1}
        ).to_list(length=None)

        for user_doc in rare_users:
            user_id = user_doc['user_id']
            if user_id not in afk_users_set:
                collectors.append(user_id)

        return collectors

    # -------------------------------------------------------------------------
    # Guild settings
    # -------------------------------------------------------------------------
    async def get_guild_settings(self, guild_id: int) -> dict:
        """Get all guild settings"""
        settings = await self.db.guild_settings.find_one({"guild_id": guild_id})
        return settings or {}

    async def set_rare_role(self, guild_id: int, role_id: Optional[int]):
        """Set or clear rare ping role"""
        if role_id is None:
            await self.db.guild_settings.update_one(
                {"guild_id": guild_id},
                {"$unset": {"rare_role_id": ""}},
                upsert=True
            )
        else:
            await self.db.guild_settings.update_one(
                {"guild_id": guild_id},
                {"$set": {"rare_role_id": role_id}},
                upsert=True
            )

    async def set_regional_role(self, guild_id: int, role_id: Optional[int]):
        """Set or clear regional ping role"""
        if role_id is None:
            await self.db.guild_settings.update_one(
                {"guild_id": guild_id},
                {"$unset": {"regional_role_id": ""}},
                upsert=True
            )
        else:
            await self.db.guild_settings.update_one(
                {"guild_id": guild_id},
                {"$set": {"regional_role_id": role_id}},
                upsert=True
            )

    async def set_low_prediction_channel(self, channel_id: int):
        """Set global low prediction channel"""
        await self.db.global_settings.update_one(
            {"_id": "prediction"},
            {"$set": {"low_prediction_channel_id": channel_id}},
            upsert=True
        )

    async def get_low_prediction_channel(self) -> Optional[int]:
        """Get global low prediction channel"""
        settings = await self.db.global_settings.find_one({"_id": "prediction"})
        return settings.get('low_prediction_channel_id') if settings else None

    # -------------------------------------------------------------------------
    # Starboard channel settings
    # -------------------------------------------------------------------------
    async def set_starboard_catch_channel(self, guild_id: int, channel_id: int):
        await self.db.guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {"starboard_catch_channel_id": channel_id}},
            upsert=True
        )

    async def set_starboard_egg_channel(self, guild_id: int, channel_id: int):
        await self.db.guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {"starboard_egg_channel_id": channel_id}},
            upsert=True
        )

    async def set_starboard_unbox_channel(self, guild_id: int, channel_id: int):
        await self.db.guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {"starboard_unbox_channel_id": channel_id}},
            upsert=True
        )

    async def set_starboard_shiny_channel(self, guild_id: int, channel_id: int):
        await self.db.guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {"starboard_shiny_channel_id": channel_id}},
            upsert=True
        )

    async def set_starboard_gigantamax_channel(self, guild_id: int, channel_id: int):
        await self.db.guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {"starboard_gigantamax_channel_id": channel_id}},
            upsert=True
        )

    async def set_starboard_highiv_channel(self, guild_id: int, channel_id: int):
        await self.db.guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {"starboard_highiv_channel_id": channel_id}},
            upsert=True
        )

    async def set_starboard_lowiv_channel(self, guild_id: int, channel_id: int):
        await self.db.guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {"starboard_lowiv_channel_id": channel_id}},
            upsert=True
        )

    async def set_starboard_missingno_channel(self, guild_id: int, channel_id: int):
        await self.db.guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {"starboard_missingno_channel_id": channel_id}},
            upsert=True
        )

    # Global starboard channels
    async def set_global_starboard_catch_channel(self, channel_id: int):
        await self.db.global_settings.update_one(
            {"_id": "starboard_catch"},
            {"$set": {"global_channel_id": channel_id}},
            upsert=True
        )

    async def get_global_starboard_catch_channel(self) -> Optional[int]:
        settings = await self.db.global_settings.find_one({"_id": "starboard_catch"})
        return settings.get('global_channel_id') if settings else None

    async def set_global_starboard_egg_channel(self, channel_id: int):
        await self.db.global_settings.update_one(
            {"_id": "starboard_egg"},
            {"$set": {"global_channel_id": channel_id}},
            upsert=True
        )

    async def get_global_starboard_egg_channel(self) -> Optional[int]:
        settings = await self.db.global_settings.find_one({"_id": "starboard_egg"})
        return settings.get('global_channel_id') if settings else None

    async def set_global_starboard_unbox_channel(self, channel_id: int):
        await self.db.global_settings.update_one(
            {"_id": "starboard_unbox"},
            {"$set": {"global_channel_id": channel_id}},
            upsert=True
        )

    async def get_global_starboard_unbox_channel(self) -> Optional[int]:
        settings = await self.db.global_settings.find_one({"_id": "starboard_unbox"})
        return settings.get('global_channel_id') if settings else None

    # -------------------------------------------------------------------------
    # Category operations
    # -------------------------------------------------------------------------
    async def create_category(self, guild_id: int, name: str, pokemon_list: List[str]):
        await self.db.categories.insert_one({
            "guild_id": guild_id,
            "name": name,
            "name_lower": name.lower(),
            "pokemon": pokemon_list
        })

    async def get_category(self, guild_id: int, name: str) -> Optional[dict]:
        return await self.db.categories.find_one({
            "guild_id": guild_id,
            "name_lower": name.lower()
        })

    async def update_category(self, guild_id: int, name: str, pokemon_list: List[str]):
        await self.db.categories.update_one(
            {"guild_id": guild_id, "name_lower": name.lower()},
            {"$set": {"pokemon": pokemon_list}}
        )

    async def delete_category(self, guild_id: int, name: str) -> bool:
        result = await self.db.categories.delete_one({
            "guild_id": guild_id,
            "name_lower": name.lower()
        })
        return result.deleted_count > 0

    async def get_all_categories(self, guild_id: int) -> List[dict]:
        categories = await self.db.categories.find(
            {"guild_id": guild_id},
            {"name": 1, "pokemon": 1, "_id": 0}
        ).to_list(length=None)
        return categories

    # -------------------------------------------------------------------------
    # Only-pings setting
    # -------------------------------------------------------------------------
    async def set_only_pings(self, guild_id: int, enabled: bool):
        await self.db.guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {"only_pings": enabled}},
            upsert=True
        )

    async def get_only_pings(self, guild_id: int) -> bool:
        settings = await self.db.guild_settings.find_one({"guild_id": guild_id})
        return settings.get('only_pings', False) if settings else False

    # -------------------------------------------------------------------------
    # Best name toggle (per guild)
    # -------------------------------------------------------------------------
    async def set_best_name(self, guild_id: int, enabled: bool):
        """Enable or disable best name display for a guild"""
        await self.db.guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {"best_name_enabled": enabled}},
            upsert=True
        )

    async def get_best_name(self, guild_id: int) -> bool:
        """Get best name setting for a guild (default: False)"""
        settings = await self.db.guild_settings.find_one({"guild_id": guild_id})
        return settings.get('best_name_enabled', False) if settings else False

    # -------------------------------------------------------------------------
    # Secondary model channel
    # -------------------------------------------------------------------------
    async def set_secondary_model_channel(self, channel_id: int):
        await self.db.global_settings.update_one(
            {"_id": "secondary_model"},
            {"$set": {"channel_id": channel_id}},
            upsert=True
        )

    async def get_secondary_model_channel(self) -> Optional[int]:
        settings = await self.db.global_settings.find_one({"_id": "secondary_model"})
        return settings.get('channel_id') if settings else None
