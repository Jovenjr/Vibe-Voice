"""
tts_engine.py
=============
Motor TTS del servidor usando edge-tts (voces de Microsoft Edge).
Procesa texto con LLM (Gemini) antes de TTS para formato óptimo de lectura.

VELOCIDAD EN CALIENTE (estilo WhatsApp):
  - edge-tts siempre genera a velocidad normal (+0%)
  - librosa.effects.time_stretch aplica la velocidad SIN cambiar el pitch
  - Cambiar el slider mientras algo suena: para, re-stretcha los mismos bytes,
    reanuda DESDE LA POSICIÓN ACTUAL — sin volver a llamar a edge-tts
  - Parametros optimizados para VOZ: n_fft=512, hop_length=128 (sin reverb)
  - Requiere: pip install librosa numpy soundfile pydub
"""

import threading
import queue
import logging
import asyncio
import io
import os
import re
import hashlib
import time
from pathlib import Path
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Dependencias opcionales ───────────────────────────────────────────────────

try:
    import edge_tts
    import pygame
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning("edge-tts o pygame no disponible")

try:
    import numpy as np
    import librosa
    import soundfile as sf
    LIBROSA_AVAILABLE = True
    logger.info("[TTS] librosa disponible — time-stretch activo")
except ImportError:
    LIBROSA_AVAILABLE = False
    logger.warning("[TTS] librosa no disponible — velocidad usa edge-tts rate (sin time-stretch)")

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False

try:
    from llm_processor import LLMProcessor
    LLM_AVAILABLE = True
except ImportError as e:
    LLM_AVAILABLE = False
    LLMProcessor = None
    logger.debug(f"LLM no disponible: {e}")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.warning("[TTS] requests no disponible — Telegram deshabilitado")

# Cargar configuración de Telegram desde .env
from dotenv import load_dotenv
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_markdown(text: str) -> str:
    """Limpia caracteres de markdown para que el TTS no los lea."""
    if not text:
        return text
    text = re.sub(r'```[\s\S]*?```', ' bloque de código ', text)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*\*([^*]+)\*\*\*', r'\1', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'___([^_]+)___', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    text = text.replace('**', '').replace('*', '')
    text = re.sub(r'(?<!\w)_+(?!\w)', '', text)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*>\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()


@dataclass
class AudioItem:
    """
    Unidad de audio que viaja de generator → player.
    Guarda los bytes crudos del MP3 para poder re-stretchear sin re-generar.
    """
    text: str               # Texto original (para logs)
    audio_bytes: bytes      # MP3 en memoria generado por edge-tts
    generation_speed: float # Velocidad a la que se generó (1.0 = normal)


# ── Motor principal ───────────────────────────────────────────────────────────

class ServerTTS:
    """Motor TTS que corre en el servidor usando edge-tts + librosa time-stretch."""

    VOICES = [
        {"index": 0, "name": "es-MX-DaliaNeural",  "display": "Dalia (México, Mujer)"},
        {"index": 1, "name": "es-MX-JorgeNeural",  "display": "Jorge (México, Hombre)"},
        {"index": 2, "name": "es-ES-ElviraNeural", "display": "Elvira (España, Mujer)"},
        {"index": 3, "name": "es-ES-AlvaroNeural", "display": "Álvaro (España, Hombre)"},
        {"index": 4, "name": "es-AR-ElenaNeural",  "display": "Elena (Argentina, Mujer)"},
        {"index": 5, "name": "es-CO-SalomeNeural", "display": "Salomé (Colombia, Mujer)"},
        {"index": 6, "name": "en-US-JennyNeural",  "display": "Jenny (US, Woman)"},
        {"index": 7, "name": "en-US-GuyNeural",    "display": "Guy (US, Man)"},
        {"index": 8, "name": "en-GB-SoniaNeural",  "display": "Sonia (UK, Woman)"},
    ]

    def __init__(self, llm_model: str = None, audio_callback=None):
        self.enabled  = True
        self.queue    = queue.Queue()
        self.running  = False
        self.voice    = "es-MX-DaliaNeural"

        # ── Velocidad ─────────────────────────────────────────────────────────
        self._speed_float: float = 1.0
        self._edge_rate:   str   = "+0%"
        self.rate = "+0%"

        # ── Docker mode ───────────────────────────────────────────────────────
        self.docker_mode      = os.environ.get("DOCKER_MODE") == "1"
        self.audio_callback   = audio_callback
        self.audio_cache_dir  = Path(__file__).parent / "audio_cache"
        if self.docker_mode:
            self.audio_cache_dir.mkdir(exist_ok=True)
            logger.info(f"[TTS] Modo Docker: audio en {self.audio_cache_dir}")

        # ── LLM ───────────────────────────────────────────────────────────────
        self.llm_enabled   = True
        self.llm           = LLMProcessor(model=llm_model, enabled=True) if LLM_AVAILABLE else None
        self._llm_executor = ThreadPoolExecutor(max_workers=1) if LLM_AVAILABLE else None

        # ── Telegram ──────────────────────────────────────────────────────────
        self.telegram_enabled = False
        self._telegram_configured = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        if self._telegram_configured:
            logger.info("[TTS] Telegram configurado — disponible para enviar audios")
        else:
            logger.info("[TTS] Telegram no configurado (TELEGRAM_BOT_TOKEN/CHAT_ID vacíos)")

        # ── Tracking de chunks ────────────────────────────────────────────────
        self.last_request_index     = None
        self.last_text_length       = 0
        self._last_accumulated_text = ""

        # ── Debounce / acumulación ────────────────────────────────────────────
        self._pending_queue: list  = []
        self._pending_timer        = None
        self._debounce_sec         = 1.5
        self._last_processed_hash  = ""

        # ── Deduplicación ─────────────────────────────────────────────────────
        self._seen_hashes: deque = deque(maxlen=100)
        self._seen_lock          = threading.Lock()

        # ── Generation ID (para invalidar tareas del executor) ────────────────
        self._generation_id      = 0
        self._generation_lock    = threading.Lock()

        # ── Estado de reproducción ────────────────────────────────────────────
        self._paused              = False
        self._active_channel      = None
        self._current_item:  Optional[AudioItem] = None
        self._current_item_lock = threading.Lock()
        self._speed_changed_event = threading.Event()

        # ── Pygame ────────────────────────────────────────────────────────────
        # edge-tts genera a 24000 Hz — usar el mismo rate evita resampling y pérdida de calidad
        if EDGE_TTS_AVAILABLE and not self.docker_mode:
            try:
                pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=1024)
            except Exception as e:
                logger.warning(f"[TTS] No se pudo inicializar pygame: {e}")

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    def start(self):
        if not EDGE_TTS_AVAILABLE:
            logger.error("[TTS] edge-tts no disponible")
            return
        if self.running:
            return
        self.running = True

        self._audio_ready_queue: queue.Queue = queue.Queue(maxsize=5)

        self._generator_thread = threading.Thread(
            target=self._generator_loop, daemon=True, name="TTS-Generator")
        self._generator_thread.start()

        if not self.docker_mode:
            self._player_thread = threading.Thread(
                target=self._player_loop, daemon=True, name="TTS-Player")
            self._player_thread.start()

        logger.info("[TTS] Motor iniciado (time-stretch: %s)", "librosa" if LIBROSA_AVAILABLE else "edge-tts rate")

    def stop(self):
        self.running = False
        self.queue.put(None)
        if hasattr(self, '_audio_ready_queue'):
            self._audio_ready_queue.put(None)
        if hasattr(self, '_generator_thread'):
            self._generator_thread.join(timeout=2)
        if hasattr(self, '_player_thread'):
            self._player_thread.join(timeout=2)
        if self._llm_executor:
            self._llm_executor.shutdown(wait=False)
        if EDGE_TTS_AVAILABLE and not self.docker_mode:
            try:
                pygame.mixer.quit()
            except Exception:
                pass
        logger.info("[TTS] Motor detenido")

    # ── Generator loop ────────────────────────────────────────────────────────

    def _generator_loop(self):
        """Convierte texto → AudioItem (bytes en memoria) y lo encola."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while self.running:
            try:
                text = self.queue.get(timeout=1)
                if text is None:
                    break
                if self.enabled and text.strip():
                    logger.info(f"[TTS] Generando: {text[:50]}…")
                    gen_speed = self._speed_float  # Guardar velocidad actual
                    audio_bytes = loop.run_until_complete(self._generate_audio_bytes(text))
                    if audio_bytes:
                        item = AudioItem(text=text, audio_bytes=audio_bytes, generation_speed=gen_speed)
                        
                        # Enviar a Telegram (en paralelo, no bloquea)
                        if self.telegram_enabled:
                            threading.Thread(
                                target=self._send_to_telegram,
                                args=(audio_bytes, text[:200]),
                                daemon=True
                            ).start()
                        
                        if self.docker_mode:
                            self._handle_docker_audio(item)
                        else:
                            self._audio_ready_queue.put(item)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[TTS] Error en generator: {e}")

        loop.close()

    async def _generate_audio_bytes(self, text: str) -> Optional[bytes]:
        """
        Genera audio con edge-tts a la velocidad actual.
        
        Si la velocidad es != 1.0, edge-tts la aplica directamente (mejor calidad).
        Librosa solo se usa cuando cambias velocidad DURANTE la reproducción.
        """
        text = _clean_markdown(text)
        if not text:
            return None
        try:
            # Generar a la velocidad actual — edge-tts lo hace nativo, mejor calidad
            buf = io.BytesIO()
            communicate = edge_tts.Communicate(text, self.voice, rate=self._edge_rate)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])

            audio_bytes = buf.getvalue()
            if not audio_bytes:
                logger.warning("[TTS] edge-tts devolvió 0 bytes")
                return None

            logger.info(f"[TTS] Audio generado a {self._edge_rate}: {len(audio_bytes)} bytes")
            return audio_bytes

        except Exception as e:
            logger.error(f"[TTS] Error generando audio: {e}")
            return None

    # ── Player loop ───────────────────────────────────────────────────────────

    def _player_loop(self):
        """Reproduce AudioItems. Detecta cambios de velocidad en caliente."""
        while self.running:
            try:
                item = self._audio_ready_queue.get(timeout=1)
                if item is None:
                    break
                if self.enabled:
                    with self._current_item_lock:
                        self._current_item = item
                    self._play_item(item)
                    with self._current_item_lock:
                        self._current_item = None
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[TTS] Error en player: {e}")

    def _play_item(self, item: AudioItem):
        """
        Reproduce un AudioItem con soporte de cambio de velocidad en caliente.
        
        - Si velocidad actual == velocidad de generación: reproduce directo (mejor calidad)
        - Si cambia durante reproducción: usa librosa para time-stretch desde la posición actual
        """
        current_speed = self._speed_float
        gen_speed = item.generation_speed
        self._speed_changed_event.clear()
        TARGET_SR = 24000

        # Decodificar MP3 a samples numpy UNA sola vez
        samples, sr = self._decode_to_samples(item.audio_bytes)
        if samples is None:
            return
        
        # Resamplear al rate de pygame UNA sola vez
        if LIBROSA_AVAILABLE and sr != TARGET_SR:
            samples = librosa.resample(samples, orig_sr=sr, target_sr=TARGET_SR)
            sr = TARGET_SR

        channel = None
        play_start_ms = None
        samples_offset = 0
        
        # Si velocidad actual == generación, no necesitamos stretch
        needs_stretch = abs(current_speed - gen_speed) > 0.02

        try:
            while self.running:
                remaining = samples[samples_offset:]
                if len(remaining) == 0:
                    break

                if needs_stretch:
                    # Calcular ratio relativo: si generó a 1.5x y quiere 2.0x, ratio = 2.0/1.5
                    relative_speed = current_speed / gen_speed
                    audio_buf = self._stretch_samples(remaining, sr, relative_speed)
                else:
                    # Reproducir directo sin procesar
                    audio_buf = io.BytesIO()
                    sf.write(audio_buf, remaining, sr, format='WAV', subtype='PCM_16')
                    audio_buf.seek(0)
                
                if audio_buf is None:
                    break

                sound = pygame.mixer.Sound(audio_buf)
                channel = sound.play()
                self._active_channel = channel
                play_start_ms = time.monotonic()
                
                if needs_stretch:
                    logger.info(f"[TTS] Reproduciendo a {current_speed:.2f}x (stretch {current_speed/gen_speed:.2f}x sobre {gen_speed:.2f}x)")
                else:
                    logger.info(f"[TTS] Reproduciendo directo a {gen_speed:.2f}x (sin librosa)")

                while self.running and channel and channel.get_busy():
                    if self._speed_changed_event.is_set() and not self._paused:
                        new_speed = self._speed_float
                        self._speed_changed_event.clear()

                        # Samples consumidos (considerando la velocidad efectiva)
                        elapsed_sec = time.monotonic() - play_start_ms
                        effective_speed = current_speed if needs_stretch else gen_speed
                        consumed = int(elapsed_sec * effective_speed * sr)
                        samples_offset = min(samples_offset + consumed, len(samples) - 1)

                        logger.info(f"[TTS] {current_speed:.2f}x → {new_speed:.2f}x | offset {samples_offset}/{len(samples)}")
                        current_speed = new_speed
                        needs_stretch = abs(current_speed - gen_speed) > 0.02
                        channel.stop()
                        break

                    time.sleep(0.03)
                else:
                    break

        except Exception as e:
            logger.error(f"[TTS] Error reproduciendo: {e}")
        finally:
            try:
                if channel:
                    channel.stop()
            except Exception:
                pass
            self._active_channel = None

    def _decode_to_samples(self, audio_bytes: bytes):
        """Decodifica MP3 bytes → (numpy array float32, sample_rate)."""
        try:
            mp3_buf = io.BytesIO(audio_bytes)
            if PYDUB_AVAILABLE:
                seg     = AudioSegment.from_mp3(mp3_buf)
                sr      = seg.frame_rate
                samples = np.array(seg.get_array_of_samples(), dtype=np.float32)
                if seg.channels == 2:
                    samples = samples.reshape(-1, 2).mean(axis=1)
                samples /= 32768.0
            else:
                samples, sr = sf.read(mp3_buf, dtype='float32')
                if samples.ndim > 1:
                    samples = samples.mean(axis=1)
            return samples, sr
        except Exception as e:
            logger.error(f"[TTS] Error decodificando audio: {e}")
            return None, None

    def _stretch_samples(self, samples: "np.ndarray", sr: int, speed: float) -> Optional[io.BytesIO]:
        """
        Time-stretch optimizado para VOZ (no música).
        
        n_fft=512, hop_length=128 → ventanas cortas, sin efecto reverb/tanque.
        Asume que samples ya está en el sample rate correcto (24000 Hz).
        """
        try:
            out_buf = io.BytesIO()

            if not LIBROSA_AVAILABLE or abs(speed - 1.0) < 0.02:
                sf.write(out_buf, samples, sr, format='WAV', subtype='PCM_16')
                out_buf.seek(0)
                return out_buf

            t0 = time.monotonic()

            # WSOLA con parámetros para speech (ventanas cortas = sin reverb)
            stretched = librosa.effects.time_stretch(
                samples,
                rate=speed,
                n_fft=512,
                hop_length=128,
            )

            sf.write(out_buf, stretched, sr, format='WAV', subtype='PCM_16')
            out_buf.seek(0)

            elapsed = time.monotonic() - t0
            logger.debug(f"[TTS] Stretch {speed:.2f}x en {elapsed*1000:.0f}ms ({len(samples)} samples)")
            return out_buf

        except Exception as e:
            logger.error(f"[TTS] Error en stretch: {e}")
            try:
                out_buf = io.BytesIO()
                sf.write(out_buf, samples, sr, format='WAV', subtype='PCM_16')
                out_buf.seek(0)
                return out_buf
            except Exception:
                return None

    # ── Docker mode ───────────────────────────────────────────────────────────

    def _handle_docker_audio(self, item: AudioItem):
        try:
            audio_id   = hashlib.sha256(f"{item.text}{time.time()}".encode()).hexdigest()[:12]
            audio_file = self.audio_cache_dir / f"{audio_id}.mp3"
            audio_file.write_bytes(item.audio_bytes)
            self._cleanup_old_audio()
            if self.audio_callback:
                self.audio_callback(f"/audio/{audio_id}.mp3")
        except Exception as e:
            logger.error(f"[TTS] Error guardando audio Docker: {e}")

    def _cleanup_old_audio(self):
        try:
            files = sorted(self.audio_cache_dir.glob("*.mp3"), key=lambda f: f.stat().st_mtime)
            while len(files) > 20:
                files[0].unlink()
                files.pop(0)
        except Exception:
            pass

    # ── Controles ─────────────────────────────────────────────────────────────

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        if not enabled:
            self._clear_all_queues()
            if EDGE_TTS_AVAILABLE and not self.docker_mode:
                try:
                    if self._active_channel:
                        self._active_channel.stop()
                except Exception:
                    pass
        else:
            self.last_request_index     = None
            self.last_text_length       = 0
            self._last_accumulated_text = ""
            self._last_processed_hash   = ""
            if self._pending_timer:
                self._pending_timer.cancel()
                self._pending_timer = None
            self._pending_queue = []
            with self._seen_lock:
                self._seen_hashes.clear()
        logger.info(f"[TTS] Enabled: {enabled}")

    def set_rate(self, rate):
        """Establece velocidad. Acepta float 0.5–2.0 o int 100–300."""
        try:
            rate = float(rate)
            if rate <= 5:
                speed = max(0.25, min(4.0, rate))
            else:
                speed = max(0.25, min(4.0, rate / 200.0))

            old_speed         = self._speed_float
            self._speed_float = speed
            percent           = int((speed - 1.0) * 100)
            self._edge_rate   = f"{percent:+d}%"
            self.rate         = self._edge_rate

            logger.info(f"[TTS] Velocidad: {old_speed:.2f}x → {speed:.2f}x")

            if LIBROSA_AVAILABLE and abs(speed - old_speed) > 0.02:
                self._speed_changed_event.set()

        except (ValueError, TypeError) as e:
            logger.warning(f"[TTS] Rate inválido '{rate}': {e}")

    def set_voice(self, voice_index: int):
        if 0 <= voice_index < len(self.VOICES):
            self.voice = self.VOICES[voice_index]["name"]
            logger.info(f"[TTS] Voz: {self.voice}")

    def get_voices(self):
        return [{"index": v["index"], "name": v["display"]} for v in self.VOICES]

    def pause(self):
        if EDGE_TTS_AVAILABLE and not self.docker_mode:
            try:
                if self._active_channel:
                    self._active_channel.pause()
                self._paused = True
                logger.info("[TTS] Pausado")
                return True
            except Exception as e:
                logger.warning(f"[TTS] Error pausando: {e}")
        return False

    def resume(self):
        if EDGE_TTS_AVAILABLE and not self.docker_mode:
            try:
                if self._active_channel:
                    self._active_channel.unpause()
                self._paused = False
                logger.info("[TTS] Reanudado")
                return True
            except Exception as e:
                logger.warning(f"[TTS] Error reanudando: {e}")
        return False

    def stop_audio(self):
        if EDGE_TTS_AVAILABLE and not self.docker_mode:
            try:
                if self._active_channel:
                    self._active_channel.stop()
                self._paused = False
                self._clear_all_queues()
                logger.info("[TTS] Detenido")
                return True
            except Exception as e:
                logger.warning(f"[TTS] Error deteniendo: {e}")
        return False

    def skip_current(self):
        if EDGE_TTS_AVAILABLE and not self.docker_mode:
            try:
                if self._active_channel:
                    self._active_channel.stop()
                self._paused = False
                self._clear_all_queues()
                logger.info("[TTS] Skip: colas limpiadas")
                return True
            except Exception as e:
                logger.warning(f"[TTS] Error en skip: {e}")
        return False

    def _clear_all_queues(self):
        # Incrementar generation_id para invalidar tareas pendientes del LLM executor
        with self._generation_lock:
            self._generation_id += 1
            logger.info(f"[TTS] Generation ID incrementado a {self._generation_id}")
        
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        if hasattr(self, '_audio_ready_queue'):
            while not self._audio_ready_queue.empty():
                try:
                    self._audio_ready_queue.get_nowait()
                except queue.Empty:
                    break
        if self._pending_timer:
            self._pending_timer.cancel()
            self._pending_timer = None
        self._pending_queue = []

    def get_playback_status(self):
        if EDGE_TTS_AVAILABLE and not self.docker_mode:
            try:
                is_busy = (self._active_channel is not None and self._active_channel.get_busy())
                return {
                    "playing":    is_busy and not self._paused,
                    "paused":     self._paused,
                    "speed":      self._speed_float,
                    "queue_size": self._audio_ready_queue.qsize() if hasattr(self, '_audio_ready_queue') else 0,
                }
            except Exception:
                pass
        return {"playing": False, "paused": False, "speed": self._speed_float, "queue_size": 0}

    # ── Procesamiento de texto ────────────────────────────────────────────────

    def speak(self, text: str):
        if self.enabled:
            clean = self._clean_text(text)
            if clean:
                self._enqueue_for_tts(clean)

    def process_chunk(self, request_index: int, accumulated_text: str,
                      is_first: bool, is_history: bool = False,
                      is_complete: bool = False, ide: str = ""):
        if not self.enabled or is_history:
            return

        if is_first:
            self.last_request_index     = request_index
            self.last_text_length       = 0
            self._last_accumulated_text = ""

        is_new_request = (
            is_first
            or self.last_request_index != request_index
            or not accumulated_text.startswith(self._last_accumulated_text)
        )

        if is_new_request:
            self.last_request_index     = request_index
            self.last_text_length       = 0
            self._last_accumulated_text = ""

        new_text = accumulated_text[self.last_text_length:]

        if new_text and new_text.strip():
            self.last_text_length       = len(accumulated_text)
            self._last_accumulated_text = accumulated_text
            clean = self._clean_text(new_text)
            if clean:
                self._pending_queue.append((request_index, clean))
                if self._pending_timer:
                    self._pending_timer.cancel()
                if is_complete:
                    self._flush_pending()
                else:
                    self._pending_timer = threading.Timer(self._get_debounce_sec(ide), self._flush_pending)
                    self._pending_timer.daemon = True
                    self._pending_timer.start()
        elif is_complete:
            self._flush_pending()

    def _get_debounce_sec(self, ide: str) -> float:
        if ide in ("vscode", "vscode-insiders"):
            return 0.35
        return self._debounce_sec

    def _flush_pending(self):
        if self._pending_timer:
            self._pending_timer.cancel()
            self._pending_timer = None
        if self._pending_queue and self.enabled:
            combined = " ".join(t for _, t in self._pending_queue)
            h = hashlib.sha1(combined.encode()).hexdigest()[:16]
            if h != self._last_processed_hash:
                self._last_processed_hash = h
                logger.info(f"[TTS] Flush: {len(self._pending_queue)} chunks → {len(combined)} chars")
                self._enqueue_for_tts(combined)
        self._pending_queue = []

    def _enqueue_for_tts(self, text: str):
        if not text or not text.strip():
            return
        if self._is_skipable_text(text):
            return
        if self._was_already_processed(text):
            return

        if self.llm_enabled and self.llm and self._llm_executor:
            # Capturar generation_id actual — si cambia antes de que termine,
            # la tarea se descarta (evita reproducir texto obsoleto)
            with self._generation_lock:
                task_gen_id = self._generation_id
            
            def process_and_queue():
                try:
                    processed = self.llm.process(text)
                    # Verificar que el generation_id no haya cambiado (skip/stop)
                    with self._generation_lock:
                        if task_gen_id != self._generation_id:
                            logger.info(f"[TTS] Tarea LLM descartada (gen {task_gen_id} != {self._generation_id})")
                            return
                    self.queue.put(processed if processed else text)
                except Exception as e:
                    logger.warning(f"[TTS] LLM falló: {e}")
                    with self._generation_lock:
                        if task_gen_id == self._generation_id:
                            self.queue.put(text)
            self._llm_executor.submit(process_and_queue)
        else:
            self.queue.put(text)

    def set_llm_enabled(self, enabled: bool):
        self.llm_enabled = enabled
        if self.llm:
            self.llm.set_enabled(enabled)
        logger.info(f"[TTS] LLM: {'activado' if enabled else 'desactivado'}")

    # ── Telegram ──────────────────────────────────────────────────────────────

    def set_telegram_enabled(self, enabled: bool):
        """Activa/desactiva el envío de audios a Telegram."""
        if enabled and not self._telegram_configured:
            logger.warning("[TTS] Telegram no configurado — configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env")
            self.telegram_enabled = False
            return False
        self.telegram_enabled = enabled
        logger.info(f"[TTS] Telegram: {'activado' if enabled else 'desactivado'}")
        return True

    def get_telegram_status(self):
        """Devuelve el estado de Telegram."""
        return {
            "enabled": self.telegram_enabled,
            "configured": self._telegram_configured
        }

    def _send_to_telegram(self, audio_bytes: bytes, caption: str = ""):
        """Envía audio a Telegram como nota de voz nativa (OGG/OPUS)."""
        if not self.telegram_enabled or not self._telegram_configured:
            return False
        if not REQUESTS_AVAILABLE:
            logger.warning("[TTS] requests no disponible para Telegram")
            return False
        
        try:
            # Convertir MP3 a OGG/OPUS para que sea nota de voz nativa
            ogg_bytes = self._convert_mp3_to_ogg(audio_bytes)
            if not ogg_bytes:
                logger.warning("[TTS] No se pudo convertir a OGG, enviando como audio")
                return self._send_audio_fallback(audio_bytes, caption)
            
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice"
            
            files = {
                "voice": ("voice.ogg", io.BytesIO(ogg_bytes), "audio/ogg")
            }
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption[:1024] if caption else "",
            }
            
            response = requests.post(url, files=files, data=data, timeout=30)
            
            if response.status_code == 200:
                logger.info("[TTS] Nota de voz enviada a Telegram")
                return True
            else:
                logger.error(f"[TTS] Error Telegram: {response.status_code} - {response.text[:200]}")
                return self._send_audio_fallback(audio_bytes, caption)
                
        except Exception as e:
            logger.error(f"[TTS] Error enviando a Telegram: {e}")
            return False
    
    def _convert_mp3_to_ogg(self, mp3_bytes: bytes) -> bytes:
        """Convierte MP3 a OGG/OPUS para Telegram Voice."""
        try:
            if not PYDUB_AVAILABLE:
                logger.warning("[TTS] pydub no disponible para conversión OGG")
                return None
            
            # Cargar MP3
            audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
            
            # Exportar como OGG con codec OPUS
            ogg_buffer = io.BytesIO()
            audio.export(
                ogg_buffer, 
                format="ogg",
                codec="libopus",
                parameters=["-ar", "48000", "-ac", "1"]  # 48kHz mono, óptimo para voz
            )
            ogg_buffer.seek(0)
            return ogg_buffer.read()
            
        except Exception as e:
            logger.error(f"[TTS] Error convirtiendo a OGG: {e}")
            return None
    
    def _send_audio_fallback(self, audio_bytes: bytes, caption: str = ""):
        """Fallback: envía como archivo de audio si la conversión OGG falla."""
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAudio"
            
            files = {
                "audio": ("audio.mp3", io.BytesIO(audio_bytes), "audio/mpeg")
            }
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption[:1024] if caption else "",
            }
            
            response = requests.post(url, files=files, data=data, timeout=30)
            return response.status_code == 200
            
        except Exception as e:
            logger.error(f"[TTS] Error en fallback audio: {e}")
            return False

    # ── Deduplicación ─────────────────────────────────────────────────────────

    def _text_hash(self, text: str) -> str:
        return hashlib.sha256(text.strip().encode()).hexdigest()

    def _was_already_processed(self, text: str) -> bool:
        h = self._text_hash(text)
        with self._seen_lock:
            if h in self._seen_hashes:
                return True
            self._seen_hashes.append(h)
        return False

    def _is_skipable_text(self, text: str) -> bool:
        t = text.strip().lower()
        if not t or len(t) < 3:
            return True
        if re.match(r'^(\s*bloque de código\s*[\.\s]*)+$', t, re.IGNORECASE):
            return True
        if re.match(r'^(\s*enlace\s*[\.\s]*)+$', t, re.IGNORECASE):
            return True
        if t.count("bloque de código") * len("bloque de código") > len(t) * 0.9:
            return True
        return False

    def _clean_text(self, text: str) -> str:
        text = re.sub(r'```[\s\S]*?```', ' ', text)
        text = re.sub(r'`[^`]+`', '', text)
        text = re.sub(r'https?://\S+', ' enlace ', text)
        text = re.sub(r'\[ref:[^\]]+\]', '', text)
        text = re.sub(r'/', ' o ', text)
        text = re.sub(r'[#*_~`]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
