"""Pokemon prediction using dual ONNX models"""
# Heavy ML dependencies are intentionally NOT imported at module load time.
# They are lazy-loaded in _ensure_heavy_imports() so that simply importing
# this file (e.g. at bot startup) does not consume ~400 MB of RAM.
# They are released again in unload_models() so RAM returns to startup baseline.
import aiohttp
import io
import os
import json
import time
import hashlib
import asyncio
import gc
import re
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Lazy-loaded heavy dependencies — populated by _ensure_heavy_imports()
# ---------------------------------------------------------------------------
ort   = None  # onnxruntime
np    = None  # numpy
Image = None  # PIL.Image


def _ensure_heavy_imports():
    """Import RAM-heavy libraries only when models are about to be loaded."""
    global ort, np, Image
    if ort is not None:
        return  # already imported
    import onnxruntime as _ort
    import numpy as _np
    from PIL import Image as _Image
    ort   = _ort
    np    = _np
    Image = _Image


def _release_heavy_imports():
    """
    Drop references to the heavy modules so Python can (partially) unload
    their native memory.  onnxruntime's C++ allocator may keep a small pool,
    but the bulk of session + model weights is freed by nullifying the sessions
    before calling this.
    """
    global ort, np, Image
    import sys
    ort   = None
    np    = None
    Image = None
    for mod_name in list(sys.modules.keys()):
        if mod_name == 'onnxruntime' or mod_name.startswith('onnxruntime.'):
            sys.modules.pop(mod_name, None)
        elif mod_name == 'numpy' or mod_name.startswith('numpy.'):
            sys.modules.pop(mod_name, None)
        elif mod_name == 'PIL' or mod_name.startswith('PIL.'):
            sys.modules.pop(mod_name, None)


# Discord CDN URLs contain rotating query params (?ex=...&hm=...&is=...) that
# change every message even for the same image file.  Strip them so the same
# Pokémon image always hits the prediction cache regardless of URL refresh.
_DISCORD_CDN_RE = re.compile(
    r'^(https?://(?:cdn\.discordapp\.com|media\.discordapp\.net)/[^?#]+)'
)

def _stable_cache_key(url: str) -> str:
    m = _DISCORD_CDN_RE.match(url)
    stable = m.group(1) if m else url
    return hashlib.md5(stable.encode()).hexdigest()

# GitHub raw content URLs for models
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
PRIMARY_REPO_BASE   = "https://media.githubusercontent.com/media/cynthiaofpower/myfinalmodel/main"
SECONDARY_REPO_BASE = "https://raw.githubusercontent.com/teamrocket43434/jessmodel/main"

PRIMARY_ONNX_URL      = f"{PRIMARY_REPO_BASE}/myfinalmodel.onnx"
PRIMARY_ONNX_DATA_URL = f"{PRIMARY_REPO_BASE}/myfinalmodel.onnx.data"
PRIMARY_LABELS_URL    = f"{PRIMARY_REPO_BASE}/labels.json"
SECONDARY_ONNX_URL      = f"{SECONDARY_REPO_BASE}/poketwo_pokemon_model.onnx"
SECONDARY_ONNX_DATA_URL = f"{SECONDARY_REPO_BASE}/poketwo_pokemon_model.onnx.data"
SECONDARY_METADATA_URL  = f"{SECONDARY_REPO_BASE}/model_metadata.json"

CACHE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "model_cache")
PRIMARY_ONNX_PATH      = os.path.join(CACHE_DIR, "myfinalmodel.onnx")
PRIMARY_ONNX_DATA_PATH = os.path.join(CACHE_DIR, "myfinalmodel.onnx.data")
PRIMARY_LABELS_PATH    = os.path.join(CACHE_DIR, "labels.json")
SECONDARY_ONNX_PATH      = os.path.join(CACHE_DIR, "poketwo_pokemon_model.onnx")
SECONDARY_ONNX_DATA_PATH = os.path.join(CACHE_DIR, "poketwo_pokemon_model.onnx.data")
SECONDARY_METADATA_PATH  = os.path.join(CACHE_DIR, "model_metadata.json")

# -----------------------------------------------------------------------
# Confidence thresholds — edit these to tune prediction behaviour
# If primary model confidence >= PRIMARY_CONFIDENCE_THRESHOLD, use it directly.
# Otherwise try secondary model if >= SECONDARY_CONFIDENCE_THRESHOLD.
# If neither meets their threshold, fall back to the primary result anyway.
# -----------------------------------------------------------------------
PRIMARY_CONFIDENCE_THRESHOLD   = 94.0  # %
SECONDARY_CONFIDENCE_THRESHOLD = 90.0  # %

# -----------------------------------------------------------------------
# Pokemon-specific secondary model list
# If the PRIMARY model identifies a Pokemon in this set, ALWAYS also run
# the SECONDARY model and return whichever result has higher confidence.
# Add Pokemon names exactly as they appear in primary model's class labels.
# -----------------------------------------------------------------------
SECONDARY_MODEL_POKEMON = {
    "Pom-pom Oricorio",
}


class PredictionCache:
    """Ultra-lightweight cache - ONLY stores final results"""
    def __init__(self, max_size=100, ttl_seconds=300):  # 100 items, 5min TTL (Poketwo images expire anyway)
        self.cache = {}
        self.timestamps = {}
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._ops_since_gc = 0

    def _cleanup_expired(self):
        current_time = time.time()
        expired_keys = [
            key for key, timestamp in self.timestamps.items()
            if current_time - timestamp > self.ttl_seconds
        ]
        for key in expired_keys:
            self.cache.pop(key, None)
            self.timestamps.pop(key, None)

        # Only GC every 50 ops, not every 20
        self._ops_since_gc += 1
        if self._ops_since_gc >= 50:
            gc.collect()
            self._ops_since_gc = 0

    def get(self, key: str) -> Optional[Tuple[str, str, str]]:
        self._cleanup_expired()
        if key in self.cache:
            current_time = time.time()
            if current_time - self.timestamps[key] <= self.ttl_seconds:
                return self.cache[key]
            else:
                self.cache.pop(key, None)
                self.timestamps.pop(key, None)
        return None

    def set(self, key: str, value: Tuple[str, str, str]):
        self._cleanup_expired()

        if len(self.cache) >= self.max_size:
            sorted_keys = sorted(self.timestamps.items(), key=lambda x: x[1])
            remove_count = max(1, self.max_size // 5)
            for old_key, _ in sorted_keys[:remove_count]:
                self.cache.pop(old_key, None)
                self.timestamps.pop(old_key, None)
            gc.collect()

        self.cache[key] = value
        self.timestamps[key] = time.time()


class ModelDownloader:
    """Handle downloading and caching models from GitHub"""

    @staticmethod
    async def download_file(url: str, dest_path: str, session: aiohttp.ClientSession):
        try:
            timeout = aiohttp.ClientTimeout(total=60, connect=10)

            headers = {}
            if GITHUB_TOKEN:
                headers['Authorization'] = f'token {GITHUB_TOKEN}'

            async with session.get(url, timeout=timeout, headers=headers) as response:
                if response.status == 401:
                    raise ValueError(f"Authentication failed. Check your GITHUB_TOKEN environment variable.")
                if response.status == 404:
                    raise ValueError(f"File not found: {url}. Check repository name and file path.")
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status} error downloading {url}")

                content = await response.read()
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)

                with open(dest_path, 'wb') as f:
                    f.write(content)

                print(f"✅ Downloaded: {os.path.basename(dest_path)}")
                return True
        except Exception as e:
            print(f"❌ Failed to download {url}: {e}")
            return False

    @staticmethod
    async def ensure_models_cached(session: aiohttp.ClientSession):
        os.makedirs(CACHE_DIR, exist_ok=True)

        downloads = [
            (PRIMARY_ONNX_URL,      PRIMARY_ONNX_PATH),
            (PRIMARY_ONNX_DATA_URL, PRIMARY_ONNX_DATA_PATH),
            (PRIMARY_LABELS_URL,    PRIMARY_LABELS_PATH),
            (SECONDARY_ONNX_URL,      SECONDARY_ONNX_PATH),
            (SECONDARY_ONNX_DATA_URL, SECONDARY_ONNX_DATA_PATH),
            (SECONDARY_METADATA_URL,  SECONDARY_METADATA_PATH),
        ]

        download_tasks = []
        for url, path in downloads:
            if not os.path.exists(path):
                print(f"Downloading {os.path.basename(path)}...")
                download_tasks.append(ModelDownloader.download_file(url, path, session))
            else:
                print(f"✓ Cached: {os.path.basename(path)}")

        if download_tasks:
            results = await asyncio.gather(*download_tasks)
            if not all(results):
                raise Exception("Failed to download some model files")


class Prediction:
    def __init__(self):
        self.cache = PredictionCache()
        self.primary_session = None
        self.secondary_session = None
        self.primary_class_names = None
        self.secondary_class_names = None
        self.secondary_metadata = None
        self.models_initialized = False
        self.allow_auto_load = False
        self._cdn_semaphore = asyncio.Semaphore(3)
        self._last_cdn_request = 0
        self._cdn_min_interval = 0.01  # was 0.1 — Discord CDN rarely rate-limits; 10ms is enough spacing
        self._prediction_counter = 0
        self._loop = None  # cached event loop reference

    async def initialize_models(self, session: aiohttp.ClientSession):
        """Download and initialize both models - ONLY ONCE"""
        if self.models_initialized:
            print("[INIT] Models already initialized, skipping...")
            return

        # Import onnxruntime / numpy / Pillow NOW (not at module load time)
        _ensure_heavy_imports()

        print("Initializing prediction models...")
        self._loop = asyncio.get_event_loop()

        await ModelDownloader.ensure_models_cached(session)

        # Load class names
        with open(PRIMARY_LABELS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                sorted_keys = sorted(data.keys(), key=lambda x: int(x))
                self.primary_class_names = [data[k].strip('"') for k in sorted_keys]
            elif isinstance(data, list):
                self.primary_class_names = [name.strip('"') for name in data]
            else:
                raise ValueError("labels_v2.json must be a list or dict")

        with open(SECONDARY_METADATA_PATH, "r", encoding="utf-8") as f:
            self.secondary_metadata = json.load(f)
            self.secondary_class_names = self.secondary_metadata["class_names"]

        sess_opts = ort.SessionOptions()
        sess_opts.intra_op_num_threads = 1
        sess_opts.inter_op_num_threads = 1
        sess_opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_opts.enable_mem_pattern = False
        sess_opts.enable_cpu_mem_arena = False
        providers = ["CPUExecutionProvider"]

        self.primary_session = ort.InferenceSession(
            PRIMARY_ONNX_PATH,
            sess_options=sess_opts,
            providers=providers
        )
        print(f"✅ Primary model initialized: {len(self.primary_class_names)} classes")

        # Separate SessionOptions instance — ORT may mutate the object during init
        sess_opts2 = ort.SessionOptions()
        sess_opts2.intra_op_num_threads = 1
        sess_opts2.inter_op_num_threads = 1
        sess_opts2.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_opts2.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_opts2.enable_mem_pattern = False
        sess_opts2.enable_cpu_mem_arena = False

        self.secondary_session = ort.InferenceSession(
            SECONDARY_ONNX_PATH,
            sess_options=sess_opts2,
            providers=providers
        )
        print(f"✅ Secondary model initialized: {len(self.secondary_class_names)} classes")

        self.models_initialized = True
        self.allow_auto_load = True

        gc.collect()

    def unload_models(self):
        """Release ONNX sessions and all model data from memory."""
        self.primary_session = None
        self.secondary_session = None
        self.primary_class_names = None
        self.secondary_class_names = None
        self.secondary_metadata = None
        self.models_initialized = False
        self.allow_auto_load = False

        self.cache.cache.clear()
        self.cache.timestamps.clear()

        # Release heavy native libraries so RAM returns to startup baseline.
        # Must be called AFTER sessions are nullified (above) so ORT's own
        # reference count drops to zero before we remove the module.
        _release_heavy_imports()

        print("[UNLOAD] Model sessions and data cleared. Use !loadmodel to reload.")
        gc.collect()

        # Tell glibc to release freed heap pages back to the OS immediately.
        # Without this, Linux keeps the pages in the process address space and
        # RSS stays high even though the memory is logically free.
        import ctypes
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
            print("[UNLOAD] malloc_trim called — heap returned to OS.")
        except Exception as e:
            print(f"[UNLOAD] malloc_trim unavailable: {e}")

    def _generate_cache_key(self, url: str) -> str:
        return _stable_cache_key(url)

    async def _rate_limit_cdn_request(self):
        async with self._cdn_semaphore:
            now = time.time()
            time_since_last = now - self._last_cdn_request
            if time_since_last < self._cdn_min_interval:
                await asyncio.sleep(self._cdn_min_interval - time_since_last)
            self._last_cdn_request = time.time()

    # ------------------------------------------------------------------
    # FIX #1: Fetch raw bytes ONCE, reuse for both model sizes
    # ------------------------------------------------------------------
    async def _fetch_raw_bytes(self, url: str, session: aiohttp.ClientSession, max_retries: int = 2) -> bytes:
        """
        Download image bytes once. max_retries reduced from 4 → 2 to avoid
        long stalls on genuinely missing images (fix #3).
        """
        is_discord_cdn = 'cdn.discordapp.com' in url or 'media.discordapp.net' in url

        if is_discord_cdn:
            await self._rate_limit_cdn_request()

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        for attempt in range(max_retries):
            try:
                # Tighter timeouts — most Poketwo images are small (fix #3)
                timeout_total = 8 + (attempt * 4)
                timeout_connect = 4
                timeout = aiohttp.ClientTimeout(total=timeout_total, connect=timeout_connect)

                async with session.get(url, timeout=timeout, headers=headers) as response:
                    if response.status == 429:
                        retry_after = int(response.headers.get('Retry-After', 2))
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_after)
                            continue
                        raise ValueError("Rate limited by Discord CDN")

                    if response.status == 404:
                        raise ValueError(f"Image not found (404): {url[:80]}")

                    if response.status in [502, 503, 504]:
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2.0 * (2 ** attempt))
                            continue
                        raise ValueError(f"Server error {response.status}")

                    if response.status != 200:
                        raise ValueError(f"HTTP {response.status} error")

                    data = await response.read()

                if len(data) < 100:
                    raise ValueError("Invalid/empty image data")

                return data

            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue
                raise ValueError("Timeout fetching image")

            except aiohttp.ClientError as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue
                raise ValueError(f"Network error: {e}")

            except ValueError:
                raise

            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise ValueError(f"Failed to load image: {e}")

        raise ValueError(f"Failed to load image after {max_retries} attempts")

    def _preprocess_from_bytes(self, raw_bytes: bytes, width: int, height: int):
        """
        Resize + normalise from already-downloaded bytes.
        Called twice (primary + secondary) with ZERO extra network I/O.
        """
        img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        img = img.resize((width, height), Image.BICUBIC)  # faster than LANCZOS, negligible quality diff for CNN
        image_array = np.array(img, dtype=np.float32)
        img.close()

        image_array /= 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std  = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        image_array = (image_array - mean) / std
        image_array = np.transpose(image_array, (2, 0, 1))
        image_array = np.expand_dims(image_array, axis=0)
        return image_array

    def softmax(self, x):
        exp_x = np.exp(x - np.max(x))
        return exp_x / np.sum(exp_x)

    # ------------------------------------------------------------------
    # FIX #2: Run blocking ONNX inference in a thread pool executor
    #         so it never blocks the Discord event loop
    # ------------------------------------------------------------------
    def _run_inference(self, session, image,
                       class_names: list) -> Tuple[str, float]:
        """Synchronous inference — called via run_in_executor."""
        inputs = {session.get_inputs()[0].name: image}
        outputs = session.run(None, inputs)
        logits = outputs[0][0]

        pred_idx = int(np.argmax(logits))
        probabilities = self.softmax(logits)
        prob = float(probabilities[pred_idx])
        name = class_names[pred_idx] if pred_idx < len(class_names) else f"unknown_{pred_idx}"
        return name, prob

    async def predict_with_model(self, image, session,
                                  class_names: list) -> Tuple[str, float]:
        """Async wrapper: offloads blocking inference to thread pool (fix #2)."""
        loop = self._loop or asyncio.get_event_loop()
        name, prob = await loop.run_in_executor(
            None, self._run_inference, session, image, class_names
        )
        return name, prob

    # ------------------------------------------------------------------
    # Core predict — single download, dual resize (fix #1)
    # ------------------------------------------------------------------
    async def predict(self, url: str, session: aiohttp.ClientSession = None) -> Tuple[str, str]:
        """
        Run prediction.
        - Downloads image bytes ONCE regardless of which model(s) are used.
        - ONNX inference runs off the event loop via run_in_executor.
        - Raises RuntimeError if models are not loaded.
        """
        # Use stable key — Discord CDN rotates ?ex=/hm= but path is permanent
        cache_key = _stable_cache_key(url)
        cached_result = self.cache.get(cache_key)
        if cached_result:
            return cached_result[0], cached_result[1]

        if not self.models_initialized:
            raise RuntimeError(
                "Prediction models are not loaded. "
                "Use `!loadmodel` to load them before running predictions."
            )

        if session is None:
            import __main__
            session = getattr(__main__, 'http_session', None)
            if session is None:
                raise ValueError("HTTP session not available")

        # ----- Single download (fix #1) --------------------------------
        raw_bytes = await self._fetch_raw_bytes(url, session)

        try:
            # Always kick off both inferences concurrently — if primary comes back
            # >= 85% we use it immediately; otherwise secondary result is already done.
            loop = self._loop or asyncio.get_event_loop()
            primary_image = self._preprocess_from_bytes(raw_bytes, 224, 224)
            sw = self.secondary_metadata["image_width"]
            sh = self.secondary_metadata["image_height"]
            secondary_image = self._preprocess_from_bytes(raw_bytes, sw, sh)

            (primary_name, primary_prob), (secondary_name, secondary_prob) = await asyncio.gather(
                loop.run_in_executor(None, self._run_inference, self.primary_session, primary_image, self.primary_class_names),
                loop.run_in_executor(None, self._run_inference, self.secondary_session, secondary_image, self.secondary_class_names),
            )
            del primary_image, secondary_image

            primary_confidence_pct   = primary_prob   * 100
            secondary_confidence_pct = secondary_prob * 100

            # ── Pokemon-specific override ────────────────────────────────────
            # If the primary model identified a Pokemon in the watch list,
            # always pick whichever model had higher confidence — regardless
            # of the normal threshold rules.
            if primary_name in SECONDARY_MODEL_POKEMON:
                if secondary_confidence_pct >= primary_confidence_pct:
                    confidence = f"{secondary_confidence_pct:.2f}%"
                    self.cache.set(cache_key, (secondary_name, confidence, "secondary_override"))
                    self._maybe_gc()
                    return secondary_name, confidence
                else:
                    confidence = f"{primary_confidence_pct:.2f}%"
                    self.cache.set(cache_key, (primary_name, confidence, "primary_override"))
                    self._maybe_gc()
                    return primary_name, confidence

            # ── Normal threshold logic ───────────────────────────────────────
            if primary_confidence_pct >= PRIMARY_CONFIDENCE_THRESHOLD:
                confidence = f"{primary_confidence_pct:.2f}%"
                self.cache.set(cache_key, (primary_name, confidence, "primary"))
                self._maybe_gc()
                return primary_name, confidence

            if secondary_confidence_pct >= SECONDARY_CONFIDENCE_THRESHOLD:
                confidence = f"{secondary_confidence_pct:.2f}%"
                self.cache.set(cache_key, (secondary_name, confidence, "secondary"))
                self._maybe_gc()
                return secondary_name, confidence

            # Both models below threshold — fall back to primary result
            confidence = f"{primary_confidence_pct:.2f}%"
            self.cache.set(cache_key, (primary_name, confidence, "primary_fallback"))
            self._maybe_gc()
            return primary_name, confidence

        finally:
            # raw_bytes is the only large allocation; release it ASAP
            del raw_bytes

    # ------------------------------------------------------------------
    # FIX #4: GC only every 50 predictions, not every 10
    # ------------------------------------------------------------------
    def _maybe_gc(self):
        self._prediction_counter += 1
        if self._prediction_counter >= 50:
            gc.collect()
            self._prediction_counter = 0


def main():
    """Test function for development"""

    async def test_predict():
        predictor = Prediction()

        async with aiohttp.ClientSession() as session:
            await predictor.initialize_models(session)

            while True:
                url = input("Enter Pokémon image URL (or 'q' to quit): ").strip()
                if url.lower() == 'q':
                    break

                try:
                    name, confidence = await predictor.predict(url, session)
                    print(f"Predicted Pokémon: {name} (confidence: {confidence})")
                except Exception as e:
                    print(f"Error: {e}")

    asyncio.run(test_predict())


if __name__ == "__main__":
    main()
