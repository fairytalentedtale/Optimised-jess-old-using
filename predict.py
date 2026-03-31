"""Pokemon prediction using dual ONNX models"""
import onnxruntime as ort
import numpy as np
import aiohttp
from PIL import Image
import io
import os
import json
import time
import hashlib
import asyncio
import gc
from typing import Optional, Tuple

# GitHub raw content URLs for models
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
MODEL_REPO_BASE = "https://raw.githubusercontent.com/teamrocket43434/jessmodel/main"

PRIMARY_ONNX_URL = f"{MODEL_REPO_BASE}/pokemon_cnn_v2.onnx"
PRIMARY_LABELS_URL = f"{MODEL_REPO_BASE}/labels_v2.json"
SECONDARY_ONNX_URL = f"{MODEL_REPO_BASE}/poketwo_pokemon_model.onnx"
SECONDARY_ONNX_DATA_URL = f"{MODEL_REPO_BASE}/poketwo_pokemon_model.onnx.data"
SECONDARY_METADATA_URL = f"{MODEL_REPO_BASE}/model_metadata.json"

CACHE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "model_cache")
PRIMARY_ONNX_PATH = os.path.join(CACHE_DIR, "pokemon_cnn_v2.onnx")
PRIMARY_LABELS_PATH = os.path.join(CACHE_DIR, "labels_v2.json")
SECONDARY_ONNX_PATH = os.path.join(CACHE_DIR, "poketwo_pokemon_model.onnx")
SECONDARY_ONNX_DATA_PATH = os.path.join(CACHE_DIR, "poketwo_pokemon_model.onnx.data")
SECONDARY_METADATA_PATH = os.path.join(CACHE_DIR, "model_metadata.json")


class PredictionCache:
    """Ultra-lightweight cache - ONLY stores final results"""
    def __init__(self, max_size=200, ttl_seconds=900):  # 200 items, 15min TTL
        self.cache = {}
        self.timestamps = {}
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cleanup_counter = 0

    def _cleanup_expired(self):
        """Remove expired entries - aggressive cleanup"""
        current_time = time.time()
        expired_keys = [
            key for key, timestamp in self.timestamps.items()
            if current_time - timestamp > self.ttl_seconds
        ]
        for key in expired_keys:
            self.cache.pop(key, None)
            self.timestamps.pop(key, None)

        self._cleanup_counter += 1
        if self._cleanup_counter >= 20:
            gc.collect()
            self._cleanup_counter = 0

    def get(self, key: str) -> Optional[Tuple[str, str, str]]:
        """Get cached prediction if valid"""
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
        """Cache a prediction - ONLY the result tuple"""
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
        """Download a file from URL to destination path"""
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
        """Download all required models if not cached"""
        os.makedirs(CACHE_DIR, exist_ok=True)

        downloads = [
            (PRIMARY_ONNX_URL, PRIMARY_ONNX_PATH),
            (PRIMARY_LABELS_URL, PRIMARY_LABELS_PATH),
            (SECONDARY_ONNX_URL, SECONDARY_ONNX_PATH),
            (SECONDARY_ONNX_DATA_URL, SECONDARY_ONNX_DATA_PATH),
            (SECONDARY_METADATA_URL, SECONDARY_METADATA_PATH),
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
        # Guard: only allow predict() to load models after explicit !loadmodel call
        self.allow_auto_load = False
        self._cdn_semaphore = asyncio.Semaphore(3)
        self._last_cdn_request = 0
        self._cdn_min_interval = 0.1
        self._prediction_counter = 0

    async def initialize_models(self, session: aiohttp.ClientSession):
        """Download and initialize both models - ONLY ONCE"""
        if self.models_initialized:
            print("[INIT] Models already initialized, skipping...")
            return

        print("Initializing prediction models...")

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
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        sess_opts.enable_mem_pattern = False
        sess_opts.enable_cpu_mem_arena = False
        providers = ["CPUExecutionProvider"]

        self.primary_session = ort.InferenceSession(
            PRIMARY_ONNX_PATH,
            sess_options=sess_opts,
            providers=providers
        )
        print(f"✅ Primary model initialized: {len(self.primary_class_names)} classes")

        self.secondary_session = ort.InferenceSession(
            SECONDARY_ONNX_PATH,
            sess_options=sess_opts,
            providers=providers
        )
        print(f"✅ Secondary model initialized: {len(self.secondary_class_names)} classes")

        self.models_initialized = True
        self.allow_auto_load = True  # Keep allowing predictions now that models are loaded

        gc.collect()

    def unload_models(self):
        """
        Release ONNX sessions and all model data from memory.
        Also resets allow_auto_load so models won't reload automatically.
        """
        self.primary_session = None
        self.secondary_session = None
        self.primary_class_names = None
        self.secondary_class_names = None
        self.secondary_metadata = None
        self.models_initialized = False
        self.allow_auto_load = False  # Require explicit !loadmodel to reload

        self.cache.cache.clear()
        self.cache.timestamps.clear()

        print("[UNLOAD] Model sessions and data cleared. Use !loadmodel to reload.")
        gc.collect()

    def _generate_cache_key(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    async def _rate_limit_cdn_request(self):
        async with self._cdn_semaphore:
            now = time.time()
            time_since_last = now - self._last_cdn_request
            if time_since_last < self._cdn_min_interval:
                await asyncio.sleep(self._cdn_min_interval - time_since_last)
            self._last_cdn_request = time.time()

    async def preprocess_image(self, url: str, session: aiohttp.ClientSession,
                               width=224, height=224, max_retries=4):
        """ULTRA MEMORY OPTIMIZED: Async image preprocessing"""
        is_discord_cdn = 'cdn.discordapp.com' in url or 'media.discordapp.net' in url

        for attempt in range(max_retries):
            image_data = None
            try:
                if is_discord_cdn:
                    await self._rate_limit_cdn_request()

                if is_discord_cdn:
                    timeout_total = 15 + (attempt * 5)
                    timeout_connect = 5 + (attempt * 2)
                else:
                    timeout_total = 10 + (attempt * 3)
                    timeout_connect = 3 + attempt

                timeout = aiohttp.ClientTimeout(total=timeout_total, connect=timeout_connect)

                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache',
                }

                async with session.get(url, timeout=timeout, headers=headers) as response:
                    if response.status == 429:
                        retry_after = int(response.headers.get('Retry-After', 2))
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_after)
                            continue
                        raise ValueError(f"Rate limited by Discord CDN")

                    if response.status == 404:
                        if is_discord_cdn and attempt < max_retries - 1:
                            await asyncio.sleep(1.0 * (2 ** attempt))
                            continue
                        raise ValueError(f"Image not found (404)")

                    if response.status in [502, 503, 504]:
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2.0 * (2 ** attempt))
                            continue
                        raise ValueError(f"Server error {response.status}")

                    if response.status != 200:
                        raise ValueError(f"HTTP {response.status} error")

                    image_data = await response.read()

                if len(image_data) < 100:
                    raise ValueError("Invalid/empty image data")

                img = Image.open(io.BytesIO(image_data))
                img = img.convert("RGB")
                img = img.resize((width, height), Image.LANCZOS)

                image_array = np.array(img, dtype=np.float32)

                img.close()
                del img
                del image_data
                image_data = None

                image_array /= 255.0
                mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
                std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
                image_array -= mean
                image_array /= std

                image_array = np.transpose(image_array, (2, 0, 1))
                image_array = np.expand_dims(image_array, axis=0)

                return image_array

            except asyncio.TimeoutError:
                if image_data:
                    del image_data
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue
                raise ValueError(f"Timeout fetching image")

            except aiohttp.ClientError as e:
                if image_data:
                    del image_data
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue
                raise ValueError(f"Network error: {e}")

            except ValueError:
                if image_data:
                    del image_data
                raise

            except Exception as e:
                if image_data:
                    del image_data
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise ValueError(f"Failed to load image: {e}")

        raise ValueError(f"Failed to load image after {max_retries} attempts")

    def softmax(self, x):
        exp_x = np.exp(x - np.max(x))
        return exp_x / np.sum(exp_x)

    async def predict_with_model(self, image: np.ndarray, session: ort.InferenceSession,
                                class_names: list) -> Tuple[str, float]:
        """Run prediction with a specific model"""
        inputs = {session.get_inputs()[0].name: image}
        outputs = session.run(None, inputs)
        logits = outputs[0][0]

        pred_idx = int(np.argmax(logits))
        probabilities = self.softmax(logits)
        prob = float(probabilities[pred_idx])

        name = class_names[pred_idx] if pred_idx < len(class_names) else f"unknown_{pred_idx}"

        del outputs
        del logits
        del probabilities
        del inputs

        return name, prob

    async def predict(self, url: str, session: aiohttp.ClientSession = None) -> Tuple[str, str]:
        """
        Run prediction. Raises RuntimeError if models are not loaded.
        Models must be explicitly loaded via !loadmodel — no auto-loading.
        """
        # Check cache first
        cache_key = self._generate_cache_key(url)
        cached_result = self.cache.get(cache_key)
        if cached_result:
            return cached_result[0], cached_result[1]

        # Hard guard — never auto-load; require explicit !loadmodel
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

        primary_image = None
        secondary_image = None

        try:
            primary_image = await self.preprocess_image(url, session, width=224, height=224)

            primary_name, primary_prob = await self.predict_with_model(
                primary_image,
                self.primary_session,
                self.primary_class_names
            )

            del primary_image
            primary_image = None

            primary_confidence_pct = primary_prob * 100

            if primary_confidence_pct >= 85.0:
                confidence = f"{primary_confidence_pct:.2f}%"
                result = (primary_name, confidence, "primary")
                self.cache.set(cache_key, result)

                self._prediction_counter += 1
                if self._prediction_counter >= 10:
                    gc.collect()
                    self._prediction_counter = 0

                return primary_name, confidence

            secondary_width = self.secondary_metadata["image_width"]
            secondary_height = self.secondary_metadata["image_height"]
            secondary_image = await self.preprocess_image(
                url,
                session,
                width=secondary_width,
                height=secondary_height
            )

            secondary_name, secondary_prob = await self.predict_with_model(
                secondary_image,
                self.secondary_session,
                self.secondary_class_names
            )

            del secondary_image
            secondary_image = None

            secondary_confidence_pct = secondary_prob * 100

            if secondary_confidence_pct >= 90.0:
                confidence = f"{secondary_confidence_pct:.2f}%"
                result = (secondary_name, confidence, "secondary")
                self.cache.set(cache_key, result)

                self._prediction_counter += 1
                if self._prediction_counter >= 10:
                    gc.collect()
                    self._prediction_counter = 0

                return secondary_name, confidence

            # Fallback to primary
            confidence = f"{primary_confidence_pct:.2f}%"
            result = (primary_name, confidence, "primary_fallback")
            self.cache.set(cache_key, result)

            self._prediction_counter += 1
            if self._prediction_counter >= 10:
                gc.collect()
                self._prediction_counter = 0

            return primary_name, confidence

        except RuntimeError:
            raise
        except Exception as e:
            if primary_image is not None:
                del primary_image
            if secondary_image is not None:
                del secondary_image
            gc.collect()
            raise
        finally:
            if primary_image is not None:
                del primary_image
            if secondary_image is not None:
                del secondary_image


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
