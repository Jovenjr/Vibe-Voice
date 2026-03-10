"""
telegram_input.py
=================
Recibe mensajes de Telegram y los pega en Cursor/VS Code.

Flujo:
1. Usuario envía mensaje desde Telegram
2. Servidor lo recibe
3. Si es texto: copia al portapapeles
4. Si es nota de voz: transcribe con STT (Whisper/Gemini/etc)
5. Simula Ctrl+V (pegar) + Enter (enviar)

Proveedores STT soportados:
    - whisper_local: Whisper de OpenAI corriendo localmente (gratis)
    - groq: Groq Cloud con Whisper Large v3 Turbo (muy rápido, gratis tier)
    - gemini: Google Gemini API (requiere API key)
    - google_cloud: Google Cloud Speech-to-Text (requiere credenciales GCP)

Requisitos:
    pip install pyautogui pyperclip requests
    
    Para Whisper local:
        pip install openai-whisper
    
    Para Groq:
        pip install groq
    
    Para Gemini:
        pip install google-genai
"""

import threading
import logging
import time
import os
import tempfile
from pathlib import Path
from typing import Optional, Literal

from database import get_db

logger = logging.getLogger(__name__)

# ============================================================
# Dependencias opcionales
# ============================================================

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    import pyperclip
    PYPERCLIP_AVAILABLE = True
except ImportError:
    PYPERCLIP_AVAILABLE = False

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
    pyautogui.FAILSAFE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False

# STT Providers
try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

try:
    from google.cloud import speech
    GOOGLE_CLOUD_STT_AVAILABLE = True
except ImportError:
    GOOGLE_CLOUD_STT_AVAILABLE = False

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

# ============================================================
# Configuración
# ============================================================

from dotenv import load_dotenv
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Proveedor STT: whisper_local, groq, gemini, google_cloud
STT_PROVIDER = os.getenv("STT_PROVIDER", "groq")
# Modelo de Whisper local: tiny, base, small, medium, large
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

# ============================================================
# Proveedores de Speech-to-Text
# ============================================================

STTProvider = Literal["whisper_local", "groq", "gemini", "google_cloud"]

class STTEngine:
    """Motor de Speech-to-Text con múltiples proveedores."""
    
    _whisper_model = None  # Singleton para no cargar el modelo cada vez

    @classmethod
    def _get_runtime_setting(cls, key: str, default: str = "") -> str:
        try:
            value = get_db().get_setting(key, None)
            if value not in (None, ""):
                return str(value)
        except Exception as e:
            logger.debug(f"[STT] No se pudo leer setting {key} desde DB: {e}")
        return os.getenv(key, default)

    @classmethod
    def _get_runtime_provider(cls) -> str:
        return cls._get_runtime_setting("STT_PROVIDER", STT_PROVIDER)

    @classmethod
    def _get_runtime_whisper_model(cls) -> str:
        return cls._get_runtime_setting("WHISPER_MODEL", WHISPER_MODEL)
    
    @classmethod
    def get_available_providers(cls) -> list[str]:
        """Devuelve lista de proveedores disponibles."""
        providers = []
        if WHISPER_AVAILABLE:
            providers.append("whisper_local")
        if GROQ_AVAILABLE and cls._get_runtime_setting("GROQ_API_KEY", GROQ_API_KEY):
            providers.append("groq")
        if GEMINI_AVAILABLE and cls._get_runtime_setting("GEMINI_API_KEY", GEMINI_API_KEY):
            providers.append("gemini")
        if GOOGLE_CLOUD_STT_AVAILABLE:
            providers.append("google_cloud")
        return providers
    
    @classmethod
    def transcribe(
        cls,
        audio_bytes: bytes,
        provider: STTProvider = None,
        file_ext: str = ".ogg",
        mime_type: str = "",
    ) -> Optional[str]:
        """
        Transcribe audio a texto usando el proveedor especificado.
        
        Args:
            audio_bytes: Bytes del archivo de audio (OGG, MP3, WAV, etc.)
            provider: Proveedor a usar. Si es None, usa STT_PROVIDER del .env
        
        Returns:
            Texto transcrito o None si hay error
        """
        provider = provider or cls._get_runtime_provider()
        
        logger.info(f"[STT] Transcribiendo con proveedor: {provider}")
        
        if provider == "whisper_local":
            return cls._transcribe_whisper(audio_bytes, file_ext=file_ext)
        elif provider == "groq":
            return cls._transcribe_groq(audio_bytes, file_ext=file_ext)
        elif provider == "gemini":
            return cls._transcribe_gemini(audio_bytes, file_ext=file_ext)
        elif provider == "google_cloud":
            return cls._transcribe_google_cloud(audio_bytes, file_ext=file_ext, mime_type=mime_type)
        else:
            logger.error(f"[STT] Proveedor desconocido: {provider}")
            return None
    
    @classmethod
    def _transcribe_whisper(cls, audio_bytes: bytes, file_ext: str = ".ogg") -> Optional[str]:
        """Transcribe usando Whisper local."""
        if not WHISPER_AVAILABLE:
            logger.error("[STT] Whisper no está instalado. pip install openai-whisper")
            return None
        
        try:
            # Cargar modelo si no está cargado (singleton)
            if cls._whisper_model is None:
                model_name = cls._get_runtime_whisper_model()
                logger.info(f"[STT] Cargando modelo Whisper: {model_name}")
                cls._whisper_model = whisper.load_model(model_name)
                logger.info("[STT] Modelo Whisper cargado")
            
            # Guardar audio temporalmente (Whisper necesita archivo)
            with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as f:
                f.write(audio_bytes)
                temp_path = f.name
            
            try:
                # Transcribir
                result = cls._whisper_model.transcribe(
                    temp_path,
                    language="es",  # Forzar español para mejor precisión
                    fp16=False      # Desactivar FP16 para compatibilidad CPU
                )
                text = result["text"].strip()
                logger.info(f"[STT] Whisper transcribió: {len(text)} chars")
                return text
            finally:
                # Limpiar archivo temporal
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"[STT] Error en Whisper: {e}")
            return None
    
    @classmethod
    def _transcribe_groq(cls, audio_bytes: bytes, file_ext: str = ".ogg") -> Optional[str]:
        """Transcribe usando Groq Cloud (Whisper Large v3 Turbo)."""
        if not GROQ_AVAILABLE:
            logger.error("[STT] groq no está instalado. pip install groq")
            return None
        
        groq_api_key = cls._get_runtime_setting("GROQ_API_KEY", GROQ_API_KEY)
        if not groq_api_key:
            logger.error("[STT] GROQ_API_KEY no configurada")
            return None
        
        try:
            # Guardar temporalmente (Groq necesita archivo)
            with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as f:
                f.write(audio_bytes)
                temp_path = f.name
            
            try:
                client = Groq(api_key=groq_api_key)
                
                with open(temp_path, "rb") as audio_file:
                    transcription = client.audio.transcriptions.create(
                        file=(temp_path, audio_file.read()),
                        model="whisper-large-v3-turbo",
                        language="es",
                        temperature=0,
                        response_format="text",
                    )
                
                text = transcription.strip() if isinstance(transcription, str) else transcription.text.strip()
                logger.info(f"[STT] Groq transcribió: {len(text)} chars")
                return text
                
            finally:
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"[STT] Error en Groq: {e}")
            return None
    
    @classmethod
    def _transcribe_gemini(cls, audio_bytes: bytes, file_ext: str = ".ogg") -> Optional[str]:
        """Transcribe usando Google Gemini API."""
        if not GEMINI_AVAILABLE:
            logger.error("[STT] google-genai no está instalado. pip install google-genai")
            return None
        
        gemini_api_key = cls._get_runtime_setting("GEMINI_API_KEY", GEMINI_API_KEY)
        if not gemini_api_key:
            logger.error("[STT] GEMINI_API_KEY no configurada")
            return None
        
        try:
            # Guardar temporalmente
            with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as f:
                f.write(audio_bytes)
                temp_path = f.name
            
            try:
                # Inicializar cliente
                client = genai.Client(api_key=gemini_api_key)
                
                # Subir archivo
                audio_file = client.files.upload(file=temp_path)
                
                # Transcribir
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[
                        audio_file,
                        "Transcribe este audio a texto en español. "
                        "Solo devuelve la transcripción exacta, sin explicaciones, "
                        "correcciones ni comentarios adicionales."
                    ]
                )
                
                text = response.text.strip()
                logger.info(f"[STT] Gemini transcribió: {len(text)} chars")
                return text
                
            finally:
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"[STT] Error en Gemini: {e}")
            return None
    
    @classmethod
    def _transcribe_google_cloud(
        cls,
        audio_bytes: bytes,
        file_ext: str = ".ogg",
        mime_type: str = "",
    ) -> Optional[str]:
        """Transcribe usando Google Cloud Speech-to-Text."""
        if not GOOGLE_CLOUD_STT_AVAILABLE:
            logger.error("[STT] google-cloud-speech no está instalado. pip install google-cloud-speech")
            return None
        
        try:
            client = speech.SpeechClient()
            
            audio = speech.RecognitionAudio(content=audio_bytes)
            encoding = speech.RecognitionConfig.AudioEncoding.OGG_OPUS
            if file_ext == ".webm" or "webm" in mime_type:
                encoding = speech.RecognitionConfig.AudioEncoding.WEBM_OPUS
            config = speech.RecognitionConfig(
                encoding=encoding,
                sample_rate_hertz=48000,
                language_code="es-ES",
                enable_automatic_punctuation=True,
            )
            
            response = client.recognize(config=config, audio=audio)
            
            text = " ".join(
                result.alternatives[0].transcript
                for result in response.results
            )
            
            logger.info(f"[STT] Google Cloud transcribió: {len(text)} chars")
            return text.strip()
            
        except Exception as e:
            logger.error(f"[STT] Error en Google Cloud: {e}")
            return None


class TelegramInputHandler:
    """Escucha mensajes de Telegram y los pega en el IDE."""
    
    def __init__(self, on_message_callback=None):
        self.enabled = False
        self.running = False
        self._thread = None
        self._last_update_id = 0
        self.on_message_callback = on_message_callback  # Para notificar a la UI
        
        # Verificar dependencias
        self._configured = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        self._deps_available = REQUESTS_AVAILABLE and PYPERCLIP_AVAILABLE and PYAUTOGUI_AVAILABLE
        
        if not self._configured:
            logger.warning("[TG-INPUT] Telegram no configurado")
        if not self._deps_available:
            missing = []
            if not REQUESTS_AVAILABLE: missing.append("requests")
            if not PYPERCLIP_AVAILABLE: missing.append("pyperclip")
            if not PYAUTOGUI_AVAILABLE: missing.append("pyautogui")
            logger.warning(f"[TG-INPUT] Dependencias faltantes: {', '.join(missing)}")
    
    def start(self):
        """Inicia el listener de Telegram."""
        if not self._configured or not self._deps_available:
            logger.error("[TG-INPUT] No se puede iniciar: falta configuración o dependencias")
            return False
        
        if self.running:
            return True
        
        self.running = True
        self.enabled = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="TG-Input")
        self._thread.start()
        logger.info("[TG-INPUT] Listener iniciado")
        return True
    
    def stop(self):
        """Detiene el listener."""
        self.running = False
        self.enabled = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("[TG-INPUT] Listener detenido")
    
    def set_enabled(self, enabled: bool) -> bool:
        """Activa/desactiva el listener."""
        if enabled:
            return self.start()
        else:
            self.enabled = False
            logger.info("[TG-INPUT] Desactivado (polling sigue pero no pega)")
            return True
    
    def get_status(self):
        """Devuelve el estado actual."""
        return {
            "enabled": self.enabled,
            "running": self.running,
            "configured": self._configured,
            "deps_available": self._deps_available,
            "stt_provider": STT_PROVIDER,
            "stt_available_providers": STTEngine.get_available_providers()
        }
    
    def _poll_loop(self):
        """Loop de polling de Telegram."""
        logger.info("[TG-INPUT] Polling iniciado")
        
        while self.running:
            try:
                messages = self._get_updates()
                for msg in messages:
                    if self.enabled:
                        if msg["type"] == "text":
                            self._handle_text(msg["content"])
                        elif msg["type"] == "voice":
                            self._handle_voice(msg["file_id"])
                time.sleep(1)  # Poll cada segundo
            except Exception as e:
                logger.error(f"[TG-INPUT] Error en polling: {e}")
                time.sleep(5)
    
    def _get_updates(self):
        """Obtiene nuevos mensajes de Telegram (texto y notas de voz)."""
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {
                "offset": self._last_update_id + 1,
                "timeout": 10,
                "allowed_updates": ["message"]
            }
            response = requests.get(url, params=params, timeout=15)
            
            if response.status_code != 200:
                return []
            
            data = response.json()
            if not data.get("ok"):
                return []
            
            messages = []
            for update in data.get("result", []):
                self._last_update_id = update["update_id"]
                
                # Solo procesar mensajes del chat configurado
                msg = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                
                if chat_id != TELEGRAM_CHAT_ID:
                    continue
                
                # Texto normal
                if "text" in msg:
                    messages.append({"type": "text", "content": msg["text"]})
                
                # Nota de voz
                elif "voice" in msg:
                    file_id = msg["voice"]["file_id"]
                    messages.append({"type": "voice", "file_id": file_id})
                
                # Audio (archivo de audio, no nota de voz)
                elif "audio" in msg:
                    file_id = msg["audio"]["file_id"]
                    messages.append({"type": "voice", "file_id": file_id})
            
            return messages
            
        except Exception as e:
            logger.error(f"[TG-INPUT] Error obteniendo updates: {e}")
            return []
    
    def _handle_text(self, text: str):
        """Procesa un mensaje de texto: copia al portapapeles y pega en IDE."""
        logger.info(f"[TG-INPUT] Texto recibido: {text[:50]}...")
        self._paste_and_send(text)
        self._send_confirmation(text)
    
    def _handle_voice(self, file_id: str):
        """Procesa una nota de voz: descarga, transcribe con STT, y pega."""
        logger.info(f"[TG-INPUT] Nota de voz recibida: {file_id[:20]}...")
        
        # Notificar que estamos procesando
        provider_name = STT_PROVIDER.replace("_", " ").title()
        self._send_telegram_message(f"🎤 Transcribiendo con {provider_name}...")
        
        try:
            # 1. Descargar el archivo de voz
            audio_bytes = self._download_voice(file_id)
            if not audio_bytes:
                self._send_telegram_message("❌ Error descargando audio")
                return
            
            # 2. Transcribir con Gemini
            text = self._transcribe_audio(audio_bytes)
            if not text:
                self._send_telegram_message("❌ Error transcribiendo audio")
                return
            
            logger.info(f"[TG-INPUT] Transcripción: {text[:50]}...")
            
            # 3. Pegar y enviar
            self._paste_and_send(text)
            
            # 4. Confirmar transcripción
            self._send_telegram_message(f"✅ Transcrito:\n{text[:200]}{'...' if len(text) > 200 else ''}")
            
        except Exception as e:
            logger.error(f"[TG-INPUT] Error procesando voz: {e}")
            self._send_telegram_message(f"❌ Error: {str(e)[:100]}")
    
    def _download_voice(self, file_id: str) -> bytes:
        """Descarga un archivo de voz de Telegram."""
        try:
            # Obtener ruta del archivo
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile"
            response = requests.get(url, params={"file_id": file_id}, timeout=10)
            data = response.json()
            
            if not data.get("ok"):
                logger.error(f"[TG-INPUT] Error obteniendo file path: {data}")
                return None
            
            file_path = data["result"]["file_path"]
            
            # Descargar archivo
            download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
            response = requests.get(download_url, timeout=30)
            
            if response.status_code == 200:
                logger.info(f"[TG-INPUT] Audio descargado: {len(response.content)} bytes")
                return response.content
            else:
                logger.error(f"[TG-INPUT] Error descargando: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"[TG-INPUT] Error descargando voz: {e}")
            return None
    
    def _transcribe_audio(self, audio_bytes: bytes) -> Optional[str]:
        """Transcribe audio usando el proveedor STT configurado."""
        return STTEngine.transcribe(audio_bytes)
    
    def _paste_and_send(self, text: str):
        """Copia texto al portapapeles, pega con Ctrl+V y envía con Enter."""
        try:
            # 1. Copiar al portapapeles
            pyperclip.copy(text)
            logger.debug("[TG-INPUT] Copiado al portapapeles")
            
            # 2. Pequeña pausa
            time.sleep(0.1)
            
            # 3. Pegar (Ctrl+V)
            pyautogui.hotkey('ctrl', 'v')
            logger.debug("[TG-INPUT] Ctrl+V enviado")
            
            # 4. Pequeña pausa
            time.sleep(0.2)
            
            # 5. Enter para enviar
            pyautogui.press('enter')
            logger.info("[TG-INPUT] Mensaje pegado y enviado")
            
            # 6. Notificar a la UI si hay callback
            if self.on_message_callback:
                self.on_message_callback(text)
            
        except Exception as e:
            logger.error(f"[TG-INPUT] Error pegando mensaje: {e}")
    
    def _send_telegram_message(self, text: str):
        """Envía un mensaje a Telegram."""
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_notification": True
            }
            requests.post(url, data=data, timeout=10)
        except Exception:
            pass
    
    def _send_confirmation(self, original_text: str):
        """Envía confirmación a Telegram."""
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": f"✅ Enviado a Cursor:\n{original_text[:100]}{'...' if len(original_text) > 100 else ''}",
                "disable_notification": True
            }
            requests.post(url, data=data, timeout=10)
        except Exception:
            pass  # No importa si falla la confirmación


# Singleton para uso global
_handler: TelegramInputHandler = None

def get_telegram_input_handler(on_message_callback=None) -> TelegramInputHandler:
    """Obtiene o crea el handler singleton."""
    global _handler
    if _handler is None:
        _handler = TelegramInputHandler(on_message_callback)
    return _handler
