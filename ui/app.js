/**
 * app.js — Vibe Voice
 *
 * PROTOCOLO: usa msg.event (no msg.type) — compatible con el servidor original.
 *
 * CAMBIOS vs versión anterior:
 * - Web Worker inline para el ping loop → inmune al throttling de Chrome en pestañas inactivas
 * - Page Visibility API → fuerza get_state al volver al frente
 * - window.focus handler → idem al recuperar foco
 * - Reconexión con exponential backoff + jitter
 * - Indicador de latencia WebSocket en topbar
 * - Heartbeat del servidor actualiza indicadores de estado
 * - Botón "Detener audio" envía tts_stop
 * - Auto-trim: máximo 500 mensajes en DOM
 * - get_state devuelve tts_voice_index y llm_enabled → sincroniza UI al reconectar
 */

const WS_URL_DEFAULT = (() => {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host || "localhost:8765";
  return `${proto}//${host}/ws`;
})();
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const MAX_MESSAGES_DOM = 500;
const PING_INTERVAL_MS = 4000;

// ─── Web Worker inline ────────────────────────────────────────────────────────
// Corre en su propio thread → no se ve afectado por el throttling de Chrome
// que reduce setInterval a 1Hz en pestañas en segundo plano.
const WORKER_SRC = `
let iv = null;
self.onmessage = function(e) {
  if (e.data === 'start') {
    clearInterval(iv);
    iv = setInterval(() => self.postMessage('tick'), ${PING_INTERVAL_MS});
  } else if (e.data === 'stop') {
    clearInterval(iv);
  }
};
`;

function createPingWorker(onTick) {
  try {
    const blob = new Blob([WORKER_SRC], { type: "application/javascript" });
    const worker = new Worker(URL.createObjectURL(blob));
    worker.onmessage = onTick;
    worker.postMessage('start');
    return worker;
  } catch (e) {
    console.warn("[app] Web Worker no disponible, usando setInterval:", e);
    return null;
  }
}

// ─── Clase principal ──────────────────────────────────────────────────────────

class VibeVoiceViewer {
  constructor() {
    this.ws = null;
    this.reconnectAttempts = 0;
    this.reconnectTimer = null;
    this.pingWorker = null;
    this.pingFallback = null;
    this.isConnected = false;
    this.pingsSent = 0;
    this.currentRequestIndex = -1;

    // Cola de audio para modo Docker (TTS server-side)
    this._audioQueue = [];
    this._isPlayingAudio = false;
    this._currentAudio = null;
    this._clientAudioPaused = false;
    this._audioBlocked = false;
    this._audioUnlocked = false;
    this._audioUnlocking = false;
    this._pendingAutoplayAudio = null;
    this._audioUnlockHintShown = false;
    this._audioContext = null;
    this._recentAudioKeys = new Map();
    this._recentAudioUrls = new Map();
    this._dbSettings = {};
    this._sttRecorder = null;
    this._sttStream = null;
    this._sttChunks = [];
    this._sttRecording = false;
    this._agentActivity = {
      status: "idle",
      label: "Sin señal",
      detail: "",
      timestamp: 0,
      open_tool_count: 0,
      open_tools: [],
      current_tool: "",
    };
    this._agentTimer = null;
    this._sessionRefreshTimer = null;
    this._sessionRefreshDebounce = null;

    // Historial
    this._sessions = [];
    this._sessionsById = new Map();
    this._selectedSessionId = null;
    this._followedSourceFile = "";
    this._liveSessionState = null;
    this._sessionView = "active";
    this._sessionStatusFilter = "all";
    this._sessionSearch = "";
    this._localBridgeWs = null;
    this._localBridgeConnected = false;
    this._localBridgeReconnectTimer = null;
    this._localBridgeReconnectAttempts = 0;

    this.prefs = this._loadPrefs();
    this._lastNonZeroAudioVolume = this.prefs.audioVolume > 0 ? this.prefs.audioVolume : 1;

    // DOM refs
    this.$messages    = document.getElementById("messages");
    this.$statusDot   = document.getElementById("status-dot");
    this.$statusText  = document.getElementById("status-text");
    this.$latency     = document.getElementById("latency");
    this.$agentState  = document.getElementById("agent-state");
    this.$agentDetail = document.getElementById("agent-detail");
    this.$activeFile  = document.getElementById("active-file");
    this.$watching    = document.getElementById("watching-count");

    this._applyPrefs();
    this._applyTheme();
    this._bindUI();
    this._syncLocalBridgeConnection();
    this._initSettingsModal();
    this._connect();
    this._setupVisibility();
    this._startPingLoop();
    this._startAgentClock();
    this._startSessionRefresh();
  }

  // ─── Theme ───────────────────────────────────────────────────────────────────

  _applyTheme() {
    const theme = this.prefs.theme || "dark";
    document.documentElement.setAttribute("data-theme", theme);
    this._updateThemeButton(theme);
  }

  _toggleTheme() {
    const current = this.prefs.theme || "dark";
    const next = current === "dark" ? "light" : "dark";
    this.prefs.theme = next;
    this._savePrefs();
    document.documentElement.setAttribute("data-theme", next);
    this._updateThemeButton(next);
  }

  _updateThemeButton(theme) {
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.textContent = theme === "dark" ? "🌙" : "☀️";
      btn.title = theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro";
    }
  }

  // ─── WebSocket ─────────────────────────────────────────────────────────────

  _connect() {
    const url = this.prefs.wsUrl || WS_URL_DEFAULT;
    this._setStatus("connecting", `Conectando a ${url}…`);

    try {
      this.ws = new WebSocket(url);
    } catch (e) {
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.isConnected = true;
      this.reconnectAttempts = 0;
      this._setStatus("connected", "Conectado");
      // get_state no necesario: register() en servidor ya envía el estado
      this._send({ action: "tts_get_voices" });
      this._send({ action: "tts_telegram_status" });
      this._send({ action: "telegram_input_status" });
      
      // Sincronizar TODAS las preferencias con el servidor
      this._send({ action: "tts_enable", enabled: this.prefs.ttsEnabled });
      this._send({ action: "tts_llm_enable", enabled: this.prefs.ttsLlmEnabled });
      this._send({ action: "tts_telegram_enable", enabled: this.prefs.ttsTelegram || false });
      this._send({ action: "telegram_input_enable", enabled: this.prefs.telegramInput || false });
      this._send({ action: "set_ide", ide: this.prefs.ideFilter });
      this._send({ action: "set_include_thinking", enabled: this.prefs.includeThinking });
      this._send({ action: "set_include_codex_progress", enabled: this.prefs.includeCodexProgress });
      
      // Cargar historial de base de datos
      this._loadDbSessions();
      this._send({ action: "db_get_settings" });
      
      this._toast("✓ Conectado al servidor", "success");
    };

    this.ws.onmessage = (e) => {
      try {
        this._handle(JSON.parse(e.data));
      } catch (err) {
        console.error("[app] Error parseando mensaje:", err);
      }
    };

    this.ws.onclose = (ev) => {
      this.isConnected = false;
      const reason = ev.wasClean ? "cerrado" : `código ${ev.code}`;
      this._setStatus("disconnected", `Desconectado (${reason})`);
      this._setAgentActivity(null);
      this._scheduleReconnect();
    };

    this.ws.onerror = () => { /* onclose se llama después */ };
  }

  _scheduleReconnect() {
    if (this.reconnectTimer) return;
    const delay = Math.min(
      RECONNECT_BASE_MS * Math.pow(1.5, this.reconnectAttempts) + Math.random() * 500,
      RECONNECT_MAX_MS
    );
    this.reconnectAttempts++;
    this._setStatus("reconnecting", `Reconectando en ${(delay/1000).toFixed(1)}s… (intento ${this.reconnectAttempts})`);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this._connect();
    }, delay);
  }

  _send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  // ─── Ping loop (Web Worker) ────────────────────────────────────────────────

  _startPingLoop() {
    const onTick = () => {
      if (!this.isConnected) return;
      this._send({ action: "ping", _t: Date.now() });
    };
    this.pingWorker = createPingWorker(onTick);
    if (!this.pingWorker) {
      this.pingFallback = setInterval(onTick, PING_INTERVAL_MS);
    }
  }

  _startAgentClock() {
    this._renderAgentActivity();
    this._agentTimer = setInterval(() => this._renderAgentActivity(), 1000);
  }

  _startSessionRefresh() {
    this._sessionRefreshTimer = setInterval(() => {
      if (!this.isConnected || document.hidden) return;
      this._loadDbSessions();
    }, 8000);
  }

  _scheduleSessionRefresh(delay = 500) {
    if (this._sessionRefreshDebounce) {
      clearTimeout(this._sessionRefreshDebounce);
    }
    this._sessionRefreshDebounce = setTimeout(() => {
      this._sessionRefreshDebounce = null;
      this._loadDbSessions();
    }, delay);
  }

  // ─── Page Visibility API ───────────────────────────────────────────────────

  _setupVisibility() {
    // Cuando la pestaña vuelve al frente, forzar refresh de datos
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        if (this.isConnected) {
          this._send({ action: "get_state" });
          this._loadDbSessions();
        } else {
          // Reconectar inmediatamente en lugar de esperar el timer
          if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
          }
          this._connect();
        }
      }
    });

    // También al recuperar foco de ventana
    window.addEventListener("focus", () => {
      if (this.isConnected) {
        this._send({ action: "get_state" });
        this._loadDbSessions();
      }
    });
  }

  // ─── Manejador de mensajes ─────────────────────────────────────────────────

  _handle(msg) {
    // El servidor usa msg.event (no msg.type)
    const ev = msg.event;

    switch (ev) {

      case "pong": {
        if (msg._t) {
          const latency = Date.now() - msg._t;
          if (this.$latency) this.$latency.textContent = `${latency}ms`;
        }
        break;
      }

      case "heartbeat": {
        if (this.$watching)   this.$watching.textContent   = msg.watching   ?? "–";
        if (this.$activeFile) this.$activeFile.textContent = msg.active_file || "ninguno";
        break;
      }

      case "session_state": {
        this._liveSessionState = {
          session_id: msg.session_id || "",
          title: msg.title || "",
          file: msg.file || "",
          cwd: msg.cwd || "",
          cwd_name: msg.cwd_name || "",
          git_root: msg.git_root || "",
          branch: msg.branch || "",
          repository: msg.repository || "",
          model: msg.model || "",
          models_used: Array.isArray(msg.models_used) ? msg.models_used : [],
          manual_follow: !!msg.manual_follow,
          message_count: msg.message_count || 0,
          ide: msg.ide || "",
          agent_activity: msg.agent_activity || null,
        };
        if (this.$watching)   this.$watching.textContent   = "–";
        if (this.$activeFile) this.$activeFile.textContent = msg.file ? msg.file.split(/[/\\]/).pop() : "ninguno";
        this._setAgentActivity(msg.agent_activity || null);
        this._renderCurrentSessionHeader();
        this._renderSessions(this._sessions);
        // Sincronizar UI de TTS con estado del servidor
        if (msg.tts_enabled    !== undefined) this._setToggle("tts-enabled",     msg.tts_enabled);
        if (msg.tts_voice_index !== undefined) this._setSelect("tts-voice",      msg.tts_voice_index);
        if (msg.llm_enabled    !== undefined) this._setToggle("tts-llm-enabled", msg.llm_enabled);
        break;
      }

      case "no_session": {
        this._liveSessionState = null;
        this._setAgentActivity(null);
        this._renderCurrentSessionHeader();
        this._renderSessions(this._sessions);
        this._appendSystem("⚠️ No hay sesiones de chat disponibles.");
        break;
      }

      case "session_changed": {
        if (!this._shouldRenderIncomingLiveMessages()) {
          this._scheduleSessionRefresh(400);
          break;
        }
        this._liveSessionState = null;
        this._setAgentActivity(null);
        this._renderCurrentSessionHeader();
        this._appendSystem(`📁 Sesión: ${msg.session_id || "nueva"}`);
        if (this.isConnected) {
          this._send({ action: "get_state" });
        }
        this._scheduleSessionRefresh(400);
        break;
      }

      case "agent_activity": {
        if (this._liveSessionState) {
          this._liveSessionState.agent_activity = msg;
        }
        this._setAgentActivity(msg);
        this._renderCurrentSessionHeader();
        this._scheduleSessionRefresh(500);
        break;
      }

      case "session_init": {
        this._appendSystem(`🔄 Sesión inicializada (${msg.request_count} requests)`);
        break;
      }

      case "history_message": {
        // Historial: no disparar TTS (debounce lo maneja el servidor)
        this._appendMessage(msg.role, msg.text, msg.timestamp);
        break;
      }

      case "history_complete": {
        this._appendSystem(`✓ Historial cargado (${msg.total_messages} mensajes)`);
        break;
      }

      case "user_message": {
        if (!this._shouldRenderIncomingLiveMessages()) {
          this._scheduleSessionRefresh(700);
          break;
        }
        this._clearUserDraft();
        this._appendMessage("user", msg.text, msg.timestamp);
        this._scheduleSessionRefresh(700);
        break;
      }

      case "user_draft": {
        if (!this._shouldRenderIncomingLiveMessages()) {
          if (msg.cleared) this._clearUserDraft();
          break;
        }
        this._renderUserDraft(msg.text || "", msg.timestamp, !!msg.cleared);
        break;
      }

      case "response_chunk": {
        if (!this._shouldRenderIncomingLiveMessages()) {
          this._scheduleSessionRefresh(700);
          break;
        }
        const idx  = msg.request_index ?? -1;
        const text = msg.text || "";
        const isNew = idx !== this.currentRequestIndex;
        if (isNew) {
          this.currentRequestIndex = idx;
          this._appendMessage("assistant", text, msg.timestamp, true);
        } else {
          this._appendToLastAssistant(text);
        }
        this._scheduleSessionRefresh(700);
        break;
      }

      case "title_changed": {
        if (msg.title) document.title = `Copilot: ${msg.title}`;
        break;
      }

      // TTS responses
      case "tts_status":
        this._setToggle("tts-enabled", msg.enabled);
        break;
      case "tts_rate_set":
        if (msg.rate !== undefined) this._setSlider("tts-rate", msg.rate);
        break;
      case "tts_voice_set":
        if (msg.voice_index !== undefined) this._setSelect("tts-voice", msg.voice_index);
        if (msg.index       !== undefined) this._setSelect("tts-voice", msg.index);
        break;
      case "tts_voices":
        this._populateVoices(msg.voices || []);
        this._populateSettingsVoices(msg.voices || []);
        break;
      case "tts_llm_status":
        this._setToggle("tts-llm-enabled", msg.enabled);
        break;
      case "tts_telegram_status":
        this._setToggle("tts-telegram", msg.enabled);
        this._updateTelegramStatus(msg);
        break;
      case "telegram_input_status":
        this._setToggle("telegram-input", msg.enabled);
        this._updateTelegramInputStatus(msg);
        break;
      case "telegram_input_received":
        this._showToast(`📱 Telegram: ${msg.text}`, "info");
        break;
      case "tts_stopped":
        this._updateAudioStatus("Detenido");
        this._resetAudioButtons();
        break;
      case "tts_paused":
        this._updateAudioStatus("Pausado");
        break;
      case "tts_resumed":
        this._updateAudioStatus("Reproduciendo");
        break;
      case "tts_skipped":
        this._updateAudioStatus("Saltado");
        break;
      case "tts_playback_status":
        if (msg.client_mode) {
          this._updateAudioStatus(this._clientAudioPaused ? "Pausado" : "Listo");
        } else {
          this._updateAudioStatus(msg.playing ? `Reproduciendo (${msg.queue_size} en cola)` : "Listo");
        }
        break;
      case "tts_audio":
        // Audio generado en el servidor (modo Docker)
        this._playServerAudio(msg.url, msg.audio_key || "");
        break;

      case "ide_changed":
        this._appendSystem(`🔄 IDE: ${msg.name}`);
        this._toast(`IDE cambiado a ${msg.name}`, "info");
        // Actualizar el selector si el cambio vino de otro cliente
        const sel = document.getElementById("ide-selector");
        if (sel && sel.value !== msg.ide) {
          sel.value = msg.ide;
          this._updateIdeHint(msg.ide);
        }
        break;

      case "include_codex_progress_set":
        this.prefs.includeCodexProgress = !!msg.enabled;
        this._savePrefs();
        break;

      case "ides_list":
        // Recibimos la lista de IDEs disponibles (no usado actualmente, IDEs son estáticos)
        break;

      // ═══════════════════════════════════════════════════════════════════════
      // BASE DE DATOS / HISTORIAL
      // ═══════════════════════════════════════════════════════════════════════
      
      case "db_sessions":
        this._followedSourceFile = msg.followed_source_file || "";
        this._renderSessions(msg.sessions);
        break;
      
      case "db_messages":
        this._loadMessagesFromDb(msg.messages);
        break;
      
      case "db_search_results":
        this._renderSearchResults(msg.messages, msg.query);
        break;
      
      case "db_export":
        this._handleExport(msg);
        break;
      
      case "db_stats":
        this._renderDbStats(msg);
        break;
      
      case "db_session_deleted":
        this._toast("Sesión eliminada", "success");
        this._loadDbSessions();
        break;

      case "db_session_archived_state": {
        const leavesCurrentView = (this._sessionView === "active" && msg.archived) ||
          (this._sessionView === "archived" && !msg.archived);
        if (leavesCurrentView && this._selectedSessionId === msg.session_id) {
          this._selectedSessionId = null;
        }
        this._toast(msg.archived ? "Sesión archivada" : "Sesión desarchivada", "success");
        this._loadDbSessions();
        this._renderCurrentSessionHeader();
        break;
      }
      
      case "db_setting_saved":
        // Silencioso - no mostrar toast por cada setting
        break;

      case "refresh_triggered":
        if (this.isConnected) {
          this._send({ action: "get_state" });
        }
        this._scheduleSessionRefresh(200);
        break;

      case "session_follow_set":
        this._followedSourceFile = msg.manual ? (msg.source_file || "") : "";
        if (msg.session_id) {
          this._selectedSessionId = msg.session_id;
          this._send({ action: "db_get_messages", session_id: msg.session_id, limit: 200 });
        } else {
          this._selectedSessionId = null;
        }
        this._send({ action: "get_state" });
        this._loadDbSessions();
        this._renderCurrentSessionHeader();
        this._toast(msg.manual ? `Siguiendo: ${msg.name || "sesión"}` : "Volviendo a la sesión más reciente", "success");
        break;
      
      case "db_settings":
        this._dbSettings = msg.settings || {};
        this._applyDbSettings(msg.settings || {});
        break;

      case "stt_transcription":
        this._handleSttTranscription(msg);
        break;

      case "error":
        console.error("[servidor]", msg.message);
        break;
    }
  }

  // ─── Audio del servidor (modo Docker) ─────────────────────────────────────

  _pruneRecentAudio(map, now) {
    for (const [key, ts] of map.entries()) {
      if (now - ts > 15000) {
        map.delete(key);
      }
    }
  }

  _markRecentAudio(key, url) {
    const now = Date.now();
    if (key) {
      this._recentAudioKeys.set(key, now);
      this._pruneRecentAudio(this._recentAudioKeys, now);
    }
    if (url) {
      this._recentAudioUrls.set(url, now);
      this._pruneRecentAudio(this._recentAudioUrls, now);
    }
  }

  _isDuplicateAudio(key, url) {
    const now = Date.now();
    this._pruneRecentAudio(this._recentAudioKeys, now);
    this._pruneRecentAudio(this._recentAudioUrls, now);

    if (key && this._recentAudioKeys.has(key)) {
      return true;
    }
    return !!(url && this._recentAudioUrls.has(url));
  }

  _playServerAudio(url, audioKey = "") {
    const fullUrl = url.startsWith("http") ? url : `${window.location.origin}${url}`;
    if (this._isDuplicateAudio(audioKey, fullUrl)) {
      console.debug("[audio] Duplicado ignorado:", audioKey || fullUrl);
      return;
    }

    this._markRecentAudio(audioKey, fullUrl);

    const audioItem = { url: fullUrl, key: audioKey };

    if (this._audioBlocked) {
      // Si el navegador sigue bloqueando autoplay, conservar solo el audio más reciente
      // para evitar que se dispare una avalancha al desbloquearlo.
      this._pendingAutoplayAudio = audioItem;
      this._audioQueue = [];
      this._isPlayingAudio = false;
      this._updateAudioStatus("Haz clic para activar audio");
      this._syncAudioUnlockButton();
      return;
    }

    // Encolar audio para reproducción secuencial
    this._audioQueue.push(audioItem);
    if (this._clientAudioPaused) return;
    if (!this._isPlayingAudio) {
      this._processAudioQueue();
    }
  }

  _processAudioQueue() {
    if (this._audioQueue.length === 0) {
      this._isPlayingAudio = false;
      return;
    }

    this._isPlayingAudio = true;
    this._clientAudioPaused = false;
    const nextAudio = this._audioQueue.shift();
    if (!nextAudio) {
      this._isPlayingAudio = false;
      return;
    }

    this._currentAudio = new Audio(nextAudio.url);
    this._currentAudio.preload = "auto";
    this._currentAudio.volume = this._getAudioVolume();
    this._currentAudio.onended = () => {
      this._currentAudio = null;
      this._processAudioQueue();
    };
    this._currentAudio.onplay = () => {
      this._audioUnlocked = true;
      this._audioBlocked = false;
      this._pendingAutoplayAudio = null;
      this._syncAudioUnlockButton();
      this._updateAudioStatus("Reproduciendo");
    };
    this._currentAudio.onpause = () => {
      if (this._currentAudio) this._updateAudioStatus("Pausado");
    };
    this._currentAudio.onerror = (e) => {
      console.error("[audio] Error reproduciendo:", e);
      this._currentAudio = null;
      this._processAudioQueue();
    };
    this._currentAudio.play().catch(e => {
      console.error("[audio] No se pudo reproducir:", e);
      if (e?.name === "NotAllowedError") {
        this._audioBlocked = true;
        this._pendingAutoplayAudio = nextAudio;
        this._audioQueue = [];
        this._isPlayingAudio = false;
        this._currentAudio = null;
        this._updateAudioStatus("Haz clic para activar audio");
        this._syncAudioUnlockButton();
        if (!this._audioUnlockHintShown) {
          this._audioUnlockHintShown = true;
          this._toast("El navegador bloqueó el audio. Haz clic en cualquier parte o en ▶ para activarlo.", "warn");
        }
        return;
      }
      this._currentAudio = null;
      this._processAudioQueue();
    });
  }

  _resumeBlockedAudio() {
    this._audioQueue = [];
    if (this._pendingAutoplayAudio) {
      this._audioQueue.push(this._pendingAutoplayAudio);
      this._pendingAutoplayAudio = null;
    }
    this._audioBlocked = false;
    this._syncAudioUnlockButton();
    if (!this._isPlayingAudio && this._audioQueue.length > 0) {
      this._processAudioQueue();
    }
  }

  _getAudioVolume() {
    const volume = parseFloat(this.prefs.audioVolume);
    if (Number.isNaN(volume)) return 1;
    return Math.max(0, Math.min(1, volume));
  }

  _getAudioVolumeMeta(volume = this._getAudioVolume()) {
    if (volume <= 0.001) {
      return {
        icon: "🔇",
        levelClass: "muted",
        title: "Audio silenciado. Haz clic para restaurar el volumen.",
        ariaLabel: "Restaurar volumen",
      };
    }

    if (volume < 0.34) {
      return {
        icon: "🔈",
        levelClass: "level-low",
        title: "Volumen bajo. Haz clic para silenciar.",
        ariaLabel: "Silenciar volumen",
      };
    }

    if (volume < 0.67) {
      return {
        icon: "🔉",
        levelClass: "level-medium",
        title: "Volumen medio. Haz clic para silenciar.",
        ariaLabel: "Silenciar volumen",
      };
    }

    return {
      icon: "🔊",
      levelClass: "level-high",
      title: "Volumen alto. Haz clic para silenciar.",
      ariaLabel: "Silenciar volumen",
    };
  }

  _setAudioVolume(volume, { save = true } = {}) {
    const normalized = Math.max(0, Math.min(1, Number(volume)));
    if (!Number.isFinite(normalized)) return;

    if (normalized > 0) {
      this._lastNonZeroAudioVolume = normalized;
    }

    this.prefs.audioVolume = normalized;
    if (save) {
      this._savePrefs();
    }

    this._setSlider("audio-volume", normalized);
    this._applyCurrentAudioVolume();
    this._syncAudioVolumeIcon();
  }

  _applyCurrentAudioVolume() {
    if (this._currentAudio) {
      this._currentAudio.volume = this._getAudioVolume();
    }
  }

  _syncAudioVolumeIcon() {
    const btn = document.getElementById("audio-volume-icon");
    if (!btn) return;

    const { icon, levelClass, title, ariaLabel } = this._getAudioVolumeMeta();
    btn.textContent = icon;
    btn.title = title;
    btn.setAttribute("aria-label", ariaLabel);
    btn.classList.remove("muted", "level-low", "level-medium", "level-high");
    btn.classList.add(levelClass);
  }

  _toggleAudioMute() {
    const current = this._getAudioVolume();
    const restoredVolume = this._lastNonZeroAudioVolume > 0 ? this._lastNonZeroAudioVolume : 1;
    const nextVolume = current <= 0.001 ? restoredVolume : 0;
    this._setAudioVolume(nextVolume);
  }

  _syncAudioUnlockButton() {
    const btn = document.getElementById("unlock-audio-btn");
    if (!btn) return;

    if (this._audioBlocked || this._pendingAutoplayAudio) {
      btn.textContent = "🔈 Activar audio";
      btn.classList.remove("active");
      btn.title = "El navegador bloqueó el audio. Haz clic para volver a activarlo.";
      return;
    }

    if (this._audioUnlocked) {
      btn.textContent = "🔊 Audio listo";
      btn.classList.add("active");
      btn.title = "El navegador ya puede reproducir el audio remoto.";
      return;
    }

    btn.textContent = "🔈 Activar audio";
    btn.classList.remove("active");
    btn.title = "Desbloquear el audio del navegador remoto";
  }

  async _unlockAudioPlayback() {
    if (this._audioUnlocked || this._audioUnlocking) {
      return this._audioUnlocked;
    }

    this._audioUnlocking = true;
    try {
      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      if (AudioContextCtor) {
        if (!this._audioContext) {
          this._audioContext = new AudioContextCtor();
        }
        if (this._audioContext.state === "suspended") {
          await this._audioContext.resume();
        }

        const buffer = this._audioContext.createBuffer(1, 1, 22050);
        const source = this._audioContext.createBufferSource();
        const gain = this._audioContext.createGain();
        gain.gain.value = 0;
        source.buffer = buffer;
        source.connect(gain);
        gain.connect(this._audioContext.destination);
        source.start(0);
        source.stop(this._audioContext.currentTime + 0.001);
      }

      this._audioUnlocked = true;
      this._audioBlocked = false;
      if (this._pendingAutoplayAudio || this._audioQueue.length > 0) {
        this._resumeBlockedAudio();
      }
      if (this._audioUnlockHintShown) {
        this._updateAudioStatus("Audio activado");
      }
      this._syncAudioUnlockButton();
      return true;
    } catch (e) {
      console.warn("[audio] No se pudo desbloquear el audio:", e);
      this._syncAudioUnlockButton();
      return false;
    } finally {
      this._audioUnlocking = false;
    }
  }

  _stopServerAudio() {
    if (this._currentAudio) {
      this._currentAudio.pause();
      this._currentAudio = null;
    }
    this._audioQueue = [];
    this._isPlayingAudio = false;
    this._clientAudioPaused = false;
    this._audioBlocked = false;
    this._pendingAutoplayAudio = null;
    this._recentAudioKeys.clear();
    this._recentAudioUrls.clear();
    this._syncAudioUnlockButton();
  }

  _pauseServerAudio() {
    if (this._currentAudio) {
      this._currentAudio.pause();
    }
    this._clientAudioPaused = true;
  }

  _resumeServerAudio() {
    this._clientAudioPaused = false;
    if (this._audioBlocked || this._pendingAutoplayAudio) {
      this._resumeBlockedAudio();
    }
    if (this._currentAudio) {
      this._currentAudio.play().catch(e => {
        console.error("[audio] No se pudo reanudar:", e);
      });
      return;
    }
    if (this._audioQueue.length > 0 && !this._isPlayingAudio) {
      this._processAudioQueue();
    }
  }

  _skipServerAudio() {
    if (this._currentAudio) {
      const current = this._currentAudio;
      this._currentAudio = null;
      current.pause();
    }
    this._pendingAutoplayAudio = null;
    this._audioBlocked = false;
    this._clientAudioPaused = false;
    this._isPlayingAudio = false;
    this._processAudioQueue();
  }

  _updateAudioStatus(status) {
    const el = document.getElementById("audio-status");
    if (el) el.textContent = status;
  }

  _resetAudioButtons() {
    document.getElementById("tts-pause")?.classList.remove("active");
    document.getElementById("tts-resume")?.classList.remove("active");
  }

  _updateTelegramStatus(msg) {
    const el = document.getElementById("telegram-status");
    if (!el) return;
    if (!msg.configured) {
      el.textContent = "No configurado (ver .env)";
      el.style.color = "var(--yellow)";
    } else if (msg.enabled) {
      el.textContent = "Enviando audios";
      el.style.color = "var(--green)";
    } else {
      el.textContent = "Desactivado";
      el.style.color = "var(--muted)";
    }
  }

  _updateTelegramInputStatus(msg) {
    const el = document.getElementById("telegram-input-status");
    if (!el) return;
    if (!msg.configured) {
      el.textContent = "No configurado (ver .env)";
      el.style.color = "var(--yellow)";
    } else if (!msg.deps_available) {
      el.textContent = "Instalar: pip install pyautogui pyperclip";
      el.style.color = "var(--red)";
    } else if (msg.enabled) {
      el.textContent = "Escuchando mensajes";
      el.style.color = "var(--green)";
    } else {
      el.textContent = "Desactivado";
      el.style.color = "var(--muted)";
    }
  }

  _setAgentActivity(activity) {
    if (!activity) {
      this._agentActivity = {
        status: "idle",
        label: "Sin señal",
        detail: "",
        timestamp: 0,
        open_tool_count: 0,
        open_tools: [],
        current_tool: "",
      };
      this._renderAgentActivity();
      return;
    }

    this._agentActivity = {
      ...this._agentActivity,
      ...activity,
      open_tools: Array.isArray(activity.open_tools) ? activity.open_tools : (this._agentActivity.open_tools || []),
      open_tool_count: activity.open_tool_count ?? this._agentActivity.open_tool_count ?? 0,
    };
    this._renderAgentActivity();
  }

  _renderAgentActivity() {
    if (!this.$agentState || !this.$agentDetail) return;

    const now = Math.floor(Date.now() / 1000);
    const ts = this._agentActivity.timestamp || 0;
    const ageSec = ts > 0 ? Math.max(0, now - ts) : null;
    const hasOpenTools = (this._agentActivity.open_tool_count || 0) > 0;
    const pendingApprovalCount = this._agentActivity.pending_approval_count || 0;

    let label = this._agentActivity.label || "Sin señal";
    let detail = this._agentActivity.detail || "";
    let cssClass = "meta-agent-idle";

    if (!ts) {
      label = "Sin señal";
      detail = "No hay actividad reciente";
    } else if (pendingApprovalCount > 0 || (this._agentActivity.status || "") === "waiting_input") {
      label = "Esperando confirmación";
      detail = this._agentActivity.current_tool || this._agentActivity.open_tools?.[0] || detail || "Pendiente de aprobación";
      cssClass = "meta-agent-busy";
    } else if (hasOpenTools) {
      label = "Ejecutando";
      detail = this._agentActivity.current_tool || this._agentActivity.open_tools?.[0] || detail || "Herramienta en curso";
      cssClass = "meta-agent-busy";
    } else if ((this._agentActivity.status || "") === "error") {
      label = "Error";
      cssClass = "meta-agent-busy";
    } else if ((this._agentActivity.status || "") === "idle") {
      label = "En reposo";
      cssClass = "meta-agent-idle";
    } else if ((this._agentActivity.status || "") === "final") {
      label = "Respuesta lista";
      cssClass = ageSec !== null && ageSec > 15 ? "meta-agent-idle" : "meta-agent-final";
    } else if (ageSec !== null && ageSec > 15) {
      label = "En reposo";
      detail = detail || "Sin eventos nuevos";
      cssClass = "meta-agent-idle";
    } else if ((this._agentActivity.status || "") === "reasoning") {
      label = "Pensando";
      cssClass = "meta-agent-working";
    } else if ((this._agentActivity.status || "") === "commentary") {
      label = "Comentando";
      cssClass = "meta-agent-working";
    } else if ((this._agentActivity.status || "") === "working") {
      label = "Trabajando";
      cssClass = "meta-agent-working";
    } else {
      cssClass = "meta-agent-idle";
    }

    if (ageSec !== null) {
      label = `${label} · ${this._formatAge(ageSec)}`;
    }

    this.$agentState.textContent = label;
    this.$agentState.className = cssClass;
    this.$agentDetail.textContent = detail || "–";
    this.$agentDetail.title = detail || "";
  }

  _formatAge(seconds) {
    if (seconds < 2) return "ahora";
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h`;
  }

  // ─── Renderizado de mensajes ───────────────────────────────────────────────

  _appendMessage(role, text, timestamp, streaming = false) {
    this._trimDOM();

    const el   = document.createElement("div");
    el.className = `message ${role}${streaming ? " streaming" : ""}`;

    const header = document.createElement("div");
    header.className = "message-header";
    header.textContent = role === "user" ? "Tú" : "Copilot";

    if (this.prefs.showTimestamps && timestamp) {
      const ts = document.createElement("span");
      ts.className = "timestamp";
      ts.textContent = " · " + new Date(timestamp).toLocaleTimeString("es", {
        hour: "2-digit", minute: "2-digit", second: "2-digit"
      });
      header.appendChild(ts);
    }

    const body = document.createElement("div");
    body.className = "message-body";
    body.textContent = text;

    el.appendChild(header);
    el.appendChild(body);
    this.$messages.appendChild(el);

    if (this.prefs.autoScroll) this._scrollBottom();
    return el;
  }

  _renderUserDraft(text, timestamp, cleared = false) {
    const existing = this.$messages.querySelector(".message.user.draft");
    const cleanText = (text || "").trim();

    if (cleared || !cleanText) {
      if (existing) existing.remove();
      return;
    }

    if (existing) {
      existing.querySelector(".message-body").textContent = text;
      if (this.prefs.autoScroll) this._scrollBottom();
      return;
    }

    this._trimDOM();
    const el = this._appendMessage("user", text, timestamp, false);
    el.classList.add("draft");
    const header = el.querySelector(".message-header");
    if (header) header.textContent = "Tú escribiendo…";
  }

  _clearUserDraft() {
    const existing = this.$messages.querySelector(".message.user.draft");
    if (existing) existing.remove();
  }

  _appendToLastAssistant(text) {
    const all  = this.$messages.querySelectorAll(".message.assistant");
    const last = all[all.length - 1];
    if (last) {
      last.querySelector(".message-body").textContent += text;
      if (this.prefs.autoScroll) this._scrollBottom();
    } else {
      this._appendMessage("assistant", text);
    }
  }

  _appendSystem(text) {
    const el = document.createElement("div");
    el.className = "message system";
    el.textContent = text;
    this.$messages.appendChild(el);
    if (this.prefs.autoScroll) this._scrollBottom();
  }

  _trimDOM() {
    while (this.$messages.children.length >= MAX_MESSAGES_DOM) {
      this.$messages.removeChild(this.$messages.firstChild);
    }
  }

  _scrollBottom() {
    requestAnimationFrame(() => {
      this.$messages.scrollTop = this.$messages.scrollHeight;
    });
  }

  // ─── Indicadores de estado ─────────────────────────────────────────────────

  _setStatus(state, text) {
    if (this.$statusDot)  this.$statusDot.className = `status-dot ${state}`;
    if (this.$statusText) this.$statusText.textContent = text;
    console.log(`[estado] ${text}`);
  }

  _toast(text, type = "info") {
    const c = document.getElementById("toast-container");
    if (!c) return;
    const t = document.createElement("div");
    t.className = `toast toast-${type}`;
    t.textContent = text;
    c.appendChild(t);
    requestAnimationFrame(() => t.classList.add("visible"));
    setTimeout(() => {
      t.classList.remove("visible");
      setTimeout(() => t.remove(), 300);
    }, 2500);
  }

  // ─── Controles TTS ─────────────────────────────────────────────────────────

  _populateVoices(voices) {
    const sel = document.getElementById("tts-voice");
    if (!sel) return;
    sel.innerHTML = "";
    voices.forEach((v) => {
      const opt = document.createElement("option");
      opt.value = v.index;
      opt.textContent = v.name || `Voz ${v.index + 1}`;
      sel.appendChild(opt);
    });
    if (this.prefs.ttsVoiceIndex != null) sel.value = this.prefs.ttsVoiceIndex;
  }

  _setToggle(id, value) {
    const el = document.getElementById(id);
    if (el) el.checked = !!value;
  }

  _setSlider(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value;
    const lbl = document.getElementById(`${id}-label`);
    if (lbl) {
      if (id === "audio-volume") {
        lbl.textContent = `${Math.round(parseFloat(value) * 100)}%`;
      } else {
        lbl.textContent = `${parseFloat(value).toFixed(1)}x`;
      }
    }
  }

  _setSelect(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value;
  }

  // ─── Binding UI ────────────────────────────────────────────────────────────

  _on(id, event, fn) {
    const el = document.getElementById(id);
    if (el) el.addEventListener(event, fn);
  }

  _bindUI() {
    const unlockAudio = () => {
      this._unlockAudioPlayback();
      if (this._audioBlocked || this._pendingAutoplayAudio) {
        this._resumeBlockedAudio();
      }
    };
    document.addEventListener("pointerdown", unlockAudio, { passive: true });
    document.addEventListener("keydown", unlockAudio);

    this._on("unlock-audio-btn", "click", async () => {
      const wasUnlocked = this._audioUnlocked;
      const ok = await this._unlockAudioPlayback();
      if (ok) {
        if (!wasUnlocked) {
          this._toast("Audio del navegador activado", "success");
        }
        if (!this._isPlayingAudio) {
          this._updateAudioStatus("Audio listo");
        }
      } else {
        this._toast("El navegador todavía no permitió activar el audio", "warn");
      }
    });

    this._on("tts-enabled", "change", (e) => {
      this.prefs.ttsEnabled = e.target.checked;
      this._savePrefs();
      this._send({ action: "tts_enable", enabled: e.target.checked });
    });

    this._on("tts-rate", "input", (e) => {
      const rate = parseFloat(e.target.value);
      this.prefs.ttsRate = rate;
      this._savePrefs();
      this._send({ action: "tts_set_rate", rate });
      const lbl = document.getElementById("tts-rate-label");
      if (lbl) lbl.textContent = `${rate.toFixed(1)}x`;
    });

    this._on("audio-volume", "input", (e) => {
      const volume = Math.max(0, Math.min(1, parseFloat(e.target.value)));
      this._setAudioVolume(volume);
    });

    this._on("audio-volume-icon", "click", () => {
      this._toggleAudioMute();
    });

    this._on("tts-voice", "change", (e) => {
      const idx = parseInt(e.target.value, 10);
      this.prefs.ttsVoiceIndex = idx;
      this._savePrefs();
      this._send({ action: "tts_set_voice", voice_index: idx });
    });

    this._on("tts-llm-enabled", "change", (e) => {
      this.prefs.ttsLlmEnabled = e.target.checked;
      this._savePrefs();
      this._send({ action: "tts_llm_enable", enabled: e.target.checked });
    });

    this._on("tts-telegram", "change", (e) => {
      this.prefs.ttsTelegram = e.target.checked;
      this._savePrefs();
      this._send({ action: "tts_telegram_enable", enabled: e.target.checked });
    });

    this._on("telegram-input", "change", (e) => {
      this.prefs.telegramInput = e.target.checked;
      this._savePrefs();
      this._send({ action: "telegram_input_enable", enabled: e.target.checked });
    });

    this._on("tts-stop", "click", () => {
      this._send({ action: "tts_stop" });
      this._stopServerAudio();
      this._updateAudioStatus("Detenido");
    });

    this._on("tts-pause", "click", () => {
      this._send({ action: "tts_pause" });
      this._pauseServerAudio();
      this._updateAudioStatus("Pausado");
      document.getElementById("tts-pause").classList.add("active");
      document.getElementById("tts-resume").classList.remove("active");
    });

    this._on("tts-resume", "click", () => {
      this._send({ action: "tts_resume" });
      this._resumeServerAudio();
      this._updateAudioStatus("Reproduciendo");
      document.getElementById("tts-resume").classList.add("active");
      document.getElementById("tts-pause").classList.remove("active");
    });

    this._on("tts-skip", "click", () => {
      this._send({ action: "tts_skip" });
      this._skipServerAudio();
      this._updateAudioStatus("Saltando...");
    });

    this._on("auto-scroll", "change", (e) => {
      this.prefs.autoScroll = e.target.checked;
      this._savePrefs();
    });

    this._on("show-timestamps", "change", (e) => {
      this.prefs.showTimestamps = e.target.checked;
      this._savePrefs();
    });

    this._on("clear-messages", "click", () => {
      this.$messages.innerHTML = "";
    });

    this._on("ws-url", "change", (e) => {
      this.prefs.wsUrl = e.target.value;
      this._savePrefs();
      if (this.ws) this.ws.close();
    });

    // Selector de IDE
    this._on("ide-selector", "change", (e) => {
      const ide = e.target.value;
      this.prefs.ideFilter = ide;
      this._savePrefs();
      this._send({ action: "set_ide", ide });
      this._updateIdeHint(ide);
      this._loadDbSessions();
    });

    // Toggle incluir razonamiento (Cursor)
    this._on("include-thinking", "change", (e) => {
      this.prefs.includeThinking = e.target.checked;
      this._savePrefs();
      this._send({ action: "set_include_thinking", enabled: e.target.checked });
    });

    this._on("reconnect-btn", "click", () => {
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
      if (this.ws) this.ws.close();
      this.reconnectAttempts = 0;
      this._connect();
    });

    // Toggle de tema
    this._on("theme-toggle", "click", () => this._toggleTheme());

    // Forzar actualización - re-escanea archivos inmediatamente
    this._on("force-refresh-btn", "click", () => {
      if (this.isConnected) {
        this._send({ action: "force_refresh" });
        this._toast("Forzando actualización...", "info");
      } else {
        this._toast("No conectado", "warn");
      }
    });

    // ═══════════════════════════════════════════════════════════════════════════
    // HISTORIAL / BASE DE DATOS
    // ═══════════════════════════════════════════════════════════════════════════

    // Buscar en historial
    this._on("history-search", "input", (e) => {
      this._sessionSearch = e.target.value || "";
      this._renderSessions(this._sessions);
    });
    this._on("history-search", "keydown", (e) => {
      if (e.key === "Enter") {
        this._searchHistory();
      } else if (e.key === "Escape") {
        this._clearSessionSearch();
      }
    });
    this._on("search-btn", "click", () => this._searchHistory());
    this._on("clear-search-btn", "click", () => this._clearSessionSearch());

    // Exportar
    this._on("pin-session-btn", "click", () => this._togglePinnedSession());
    this._on("export-md", "click", () => this._exportSession("markdown"));
    this._on("export-json", "click", () => this._exportSession("json"));

    this._on("follow-latest", "click", () => this._followLatestSession());
    this._on("stt-record", "click", () => this._toggleSttRecording());
    this._on("stt-local-bridge", "change", (e) => {
      this.prefs.localPasteEnabled = e.target.checked;
      this._savePrefs();
      this._syncLocalBridgeConnection();
    });

    document.querySelectorAll("[data-session-view]").forEach((button) => {
      button.addEventListener("click", () => this._setSessionView(button.dataset.sessionView || "active"));
    });
    document.querySelectorAll("[data-session-filter]").forEach((button) => {
      button.addEventListener("click", () => this._setSessionStatusFilter(button.dataset.sessionFilter || "all"));
    });

    // Cargar sesiones al conectar
    this._loadDbSessions();
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // HISTORIAL - Métodos
  // ═══════════════════════════════════════════════════════════════════════════

  _loadDbSessions() {
    if (this.isConnected) {
      this._send({
        action: "db_get_sessions",
        limit: 100,
        archived: this._sessionView === "archived",
        ide: this.prefs.ideFilter || "all",
      });
      this._send({ action: "db_stats" });
    }
  }

  _searchHistory() {
    const input = document.getElementById("history-search");
    const query = input?.value?.trim();
    if (query) {
      this._send({ action: "db_search", query, limit: 30, archived: this._sessionView === "archived" });
    } else {
      this._toast("Escribe algo para buscar en mensajes", "info");
    }
  }

  _clearSessionSearch() {
    const input = document.getElementById("history-search");
    if (input) input.value = "";
    this._sessionSearch = "";
    this._renderSessions(this._sessions);
  }

  _setSessionView(view) {
    const nextView = view === "archived" ? "archived" : "active";
    if (this._sessionView === nextView) return;
    this._sessionView = nextView;
    this._syncSessionControls();
    this._loadDbSessions();
  }

  _setSessionStatusFilter(filterKey) {
    const allowed = new Set(["all", "active", "waiting", "error"]);
    this._sessionStatusFilter = allowed.has(filterKey) ? filterKey : "all";
    this._syncSessionControls();
    this._renderSessions(this._sessions);
  }

  _syncSessionControls() {
    document.querySelectorAll("[data-session-view]").forEach((button) => {
      const active = (button.dataset.sessionView || "active") === this._sessionView;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });

    document.querySelectorAll("[data-session-filter]").forEach((button) => {
      const active = (button.dataset.sessionFilter || "all") === this._sessionStatusFilter;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  _exportSession(format) {
    const sessionId = this._selectedSessionId;
    if (!sessionId) {
      this._toast("Selecciona una sesión primero", "info");
      return;
    }
    this._send({ action: "db_export_session", session_id: sessionId, format });
  }

  _loadSelectedSession() {
    const sessionId = this._selectedSessionId;
    if (!sessionId) {
      this._toast("Selecciona una sesión primero", "info");
      return;
    }
    this._send({ action: "db_get_messages", session_id: sessionId, limit: 200 });
    this._toast("Cargando sesión...", "info");
  }

  _followSelectedSession() {
    const sessionId = this._selectedSessionId;
    if (!sessionId) {
      this._toast("Selecciona una sesión primero", "info");
      return;
    }
    this._send({ action: "db_get_messages", session_id: sessionId, limit: 200 });
    this._send({ action: "follow_session", session_id: sessionId });
  }

  _followLatestSession() {
    this._selectedSessionId = null;
    this._send({ action: "follow_latest" });
  }

  _shouldRenderIncomingLiveMessages() {
    const selected = this._selectedSessionId ? this._sessionsById.get(this._selectedSessionId) : null;
    if (!selected?.source_file) {
      return true;
    }

    const trackedLiveFile = this._followedSourceFile || this._liveSessionState?.file || "";
    if (!trackedLiveFile) {
      return this._isSessionPinned(selected);
    }

    return selected.source_file === trackedLiveFile;
  }

  _togglePinnedSession() {
    const session = this._selectedSessionId ? this._sessionsById.get(this._selectedSessionId) : null;
    if (!session?.source_file) {
      this._toast("Selecciona una sesión con archivo primero", "info");
      return;
    }

    if (this._isSessionPinned(session)) {
      this._followLatestSession();
      return;
    }

    this._followSelectedSession();
  }

  _isSessionPinned(session) {
    if (!session?.source_file) return false;
    if (this._followedSourceFile && session.source_file === this._followedSourceFile) {
      return true;
    }
    return !!(
      this._liveSessionState?.manual_follow &&
      this._liveSessionState?.file &&
      session.source_file === this._liveSessionState.file
    );
  }

  _renderSessions(sessions) {
    const list = document.getElementById("sessions-list");
    if (!list) return;

    this._sessions = this._sortSessions(Array.isArray(sessions) ? sessions : []);
    this._sessionsById = new Map(this._sessions.map(session => [session.id, session]));
    this._syncSelectionFromLiveState();
    this._renderCurrentSessionHeader();
    this._syncSessionControls();
    const visibleSessions = this._getFilteredSessions(this._sessions);

    if (visibleSessions.length === 0) {
      const emptyText = (this._sessionSearch || this._sessionStatusFilter !== "all")
        ? "Ninguna sesión coincide con los filtros actuales."
        : (this._sessionView === "archived" ? "No hay sesiones archivadas." : "Sin sesiones guardadas.");
      list.innerHTML = `<p class="hint">${emptyText}</p>`;
      return;
    }

    list.innerHTML = visibleSessions.map((session) => {
      const isActive = this._isSessionActive(session);
      const sessionState = this._deriveSessionState(session);
      const sessionName = session.name || session.cwd_name || "Sesión sin nombre";
      const previewText = this._escapeHtml(this._truncate(session.preview_text || session.activity_detail || "Sin actividad reciente", 180));
      const detailText = session.current_tool || session.activity_detail || "Sin herramientas activas";
      const pathText = session.git_root || session.cwd || session.source_file || "Ruta no disponible";
      const subtitle = this._escapeHtml(pathText);
      const updated = this._formatRelativeDate(this._sessionEventTimestamp(session) || session.updated_at);
      const branchChip = session.branch ? `<span class="session-chip">${this._escapeHtml(this._truncate(session.branch, 24))}</span>` : "";
      const modelLabel = this._formatSessionModelLabel(session);
      const modelChip = modelLabel ? `<span class="session-chip">${this._escapeHtml(modelLabel)}</span>` : "";
      const repoLine = session.repository
        ? `<div class="session-path" title="${this._escapeHtml(session.repository)}">${this._escapeHtml(this._truncate(session.repository, 72))}</div>`
        : "";
      const archiveLabel = session.archived ? "Archivada" : "Archivar";
      const archiveAction = session.archived ? "Desarchivar" : "Archivar";

      return `
      <div class="session-item session-item-${sessionState.className} ${isActive ? "selected" : ""}" data-id="${session.id}" data-source-file="${this._escapeHtml(session.source_file || "")}" tabindex="0" role="button">
        <div class="session-row">
          <div class="session-name">${this._escapeHtml(sessionName)}</div>
          <span class="session-chip">${this._escapeHtml(session.ide || "?")}</span>
        </div>
        <div class="session-row session-row-wrap">
          <span class="session-chip">${this._escapeHtml(session.cwd_name || "sin carpeta")}</span>
          ${branchChip}
          ${modelChip}
          <span class="session-chip">${updated}</span>
          <span class="session-chip">${session.message_count || 0} msgs</span>
        </div>
        <div class="session-path" title="${subtitle}">${subtitle}</div>
        ${repoLine}
        <div class="session-preview">${previewText}</div>
        <div class="session-footer">
          <div class="session-status-line">
            <span class="session-status-dot ${sessionState.className}"></span>
            <span>${this._escapeHtml(sessionState.label)}</span>
            ${session.current_tool ? `<span class="session-tool" title="${this._escapeHtml(detailText)}">${this._escapeHtml(this._truncate(detailText, 52))}</span>` : ""}
          </div>
          <label class="session-archive-toggle" title="${archiveAction} sesión">
            <input type="checkbox" data-session-archive="${session.id}" ${session.archived ? "checked" : ""} aria-label="${archiveAction} ${this._escapeHtml(sessionName)}" />
            <span>${archiveLabel}</span>
          </label>
        </div>
      </div>
    `;
    }).join("");

    // Bind click events
    list.querySelectorAll(".session-item").forEach(el => {
      const sessionId = parseInt(el.dataset.id, 10);
      const activate = () => this._openSessionCard(sessionId);
      el.addEventListener("click", activate);
      el.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate();
        }
      });
    });

    list.querySelectorAll(".session-archive-toggle").forEach((el) => {
      el.addEventListener("click", (event) => event.stopPropagation());
      el.addEventListener("keydown", (event) => event.stopPropagation());
    });
    list.querySelectorAll("[data-session-archive]").forEach((input) => {
      input.addEventListener("change", (event) => {
        event.stopPropagation();
        const sessionId = parseInt(event.target.dataset.sessionArchive, 10);
        this._toggleSessionArchived(sessionId, event.target.checked);
      });
    });
  }

  _openSessionCard(sessionId) {
    if (!sessionId) return;
    this._selectedSessionId = sessionId;
    this._renderCurrentSessionHeader();
    this._renderSessions(this._sessions);
    const session = this._sessionsById.get(sessionId);
    this._send({ action: "db_get_messages", session_id: sessionId, limit: 200 });
    if (session && this._isSessionPinned(session)) {
      this._send({ action: "follow_session", session_id: sessionId });
    }
  }

  _toggleSessionArchived(sessionId, archived) {
    if (!sessionId) return;
    this._send({ action: "db_set_session_archived", session_id: sessionId, archived });
  }

  _getFilteredSessions(sessions) {
    return sessions.filter((session) => {
      const state = this._deriveSessionState(session);
      if (this._sessionStatusFilter === "active" && state.key !== "working") {
        return false;
      }
      if (this._sessionStatusFilter === "waiting" && state.key !== "waiting") {
        return false;
      }
      if (this._sessionStatusFilter === "error" && state.key !== "error") {
        return false;
      }
      return this._sessionMatchesSearch(session, this._sessionSearch);
    });
  }

  _sessionMatchesSearch(session, query) {
    const normalizedQuery = (query || "").trim().toLowerCase();
    if (!normalizedQuery) return true;
    const modelsUsed = Array.isArray(session.models_used) ? session.models_used.join(" ") : "";
    const haystack = [
      session.name,
      session.ide,
      session.cwd_name,
      session.cwd,
      session.git_root,
      session.branch,
      session.repository,
      session.model,
      modelsUsed,
      session.source_file,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(normalizedQuery);
  }

  _formatSessionModelLabel(session) {
    if (!session?.model) return "";
    const modelCount = session.model_count || (Array.isArray(session.models_used) ? session.models_used.length : 0);
    if (modelCount > 1) {
      return `${this._truncate(session.model, 20)} +${modelCount - 1}`;
    }
    return this._truncate(session.model, 24);
  }

  _isSessionActive(session) {
    if (!session) return false;
    if (this._selectedSessionId && session.id === this._selectedSessionId) {
      return true;
    }
    if (this._followedSourceFile && session.source_file === this._followedSourceFile) {
      return true;
    }
    const liveFile = this._liveSessionState?.file || "";
    return !!(liveFile && session.source_file === liveFile);
  }

  _syncSelectionFromLiveState() {
    if (this._selectedSessionId && this._sessionsById.has(this._selectedSessionId)) {
      return;
    }
    const sourceFile = this._followedSourceFile || this._liveSessionState?.file || "";
    if (!sourceFile) {
      this._selectedSessionId = null;
      return;
    }
    const activeSession = this._sessions.find((session) => session.source_file === sourceFile);
    if (activeSession) {
      this._selectedSessionId = activeSession.id;
      return;
    }
    this._selectedSessionId = null;
  }

  _renderCurrentSessionHeader() {
    const titleEl = document.getElementById("current-session-title");
    const subtitleEl = document.getElementById("current-session-subtitle");
    const pinBtn = document.getElementById("pin-session-btn");
    if (!titleEl || !subtitleEl) return;

    const selected = this._selectedSessionId ? this._sessionsById.get(this._selectedSessionId) : null;
    const live = this._liveSessionState || null;
    const selectedIsLive = !!(
      selected &&
      ((this._followedSourceFile && selected.source_file === this._followedSourceFile) ||
       (live?.file && selected.source_file === live.file))
    );

    if (!selected && !live) {
      titleEl.textContent = "Sin sesión seleccionada";
      subtitleEl.textContent = "Selecciona una sesión en la barra izquierda";
      if (pinBtn) {
        pinBtn.disabled = true;
        pinBtn.classList.remove("active");
        pinBtn.textContent = "📌 Fijar";
        pinBtn.title = "Selecciona una sesión para fijarla";
      }
      return;
    }

    const title = selected?.name || selected?.cwd_name || live?.title || live?.cwd_name || "Sesión activa";
    const parts = [];
    const cwd = selected?.git_root || selected?.cwd || live?.git_root || live?.cwd || "";
    const branch = selected?.branch || (selectedIsLive ? live?.branch : "") || "";
    const model = selected?.model || (selectedIsLive ? live?.model : "") || "";
    const activityLabel = this._deriveHeaderStateLabel(selected, live, selectedIsLive);
    const tool = selectedIsLive
      ? (live?.agent_activity?.current_tool || selected?.current_tool || "")
      : (selected?.current_tool || live?.agent_activity?.current_tool || "");

    if (cwd) {
      parts.push(cwd);
    } else if (selected?.source_file || live?.file) {
      parts.push(selected?.source_file || live?.file || "");
    }
    if (selected?.archived) parts.push("Archivada");
    if (branch) parts.push(`rama ${branch}`);
    if (model) parts.push(model);
    if (selected && this._isSessionPinned(selected)) parts.push("fijada");
    if (activityLabel) parts.push(activityLabel);
    if (tool) parts.push(tool);

    titleEl.textContent = title;
    subtitleEl.textContent = parts.filter(Boolean).join(" · ") || "Sesión lista";
    subtitleEl.title = subtitleEl.textContent;

    if (pinBtn) {
      const canPin = !!selected?.source_file && !selected?.archived;
      const pinned = canPin && this._isSessionPinned(selected);
      pinBtn.disabled = !canPin;
      pinBtn.classList.toggle("active", !!pinned);
      pinBtn.textContent = pinned ? "📌 Fijado" : "📌 Fijar";
      pinBtn.title = pinned
        ? "Desfijar y volver a seguir la sesión más reciente"
        : "Fijar esta sesión para que no la reemplace otra actividad";
    }
  }

  _renderDbStats(stats) {
    const el = document.getElementById("db-stats-text");
    if (el) {
      el.textContent = `Sesiones: ${stats.active_sessions ?? stats.total_sessions ?? 0} activas · ${stats.archived_sessions ?? 0} archivadas · ${stats.total_messages || 0} mensajes`;
    }
  }

  _renderSearchResults(results, query) {
    if (!results || results.length === 0) {
      this._toast(`Sin resultados para "${query}"`, "info");
      return;
    }
    
    // Mostrar resultados en mensajes
    this.$messages.innerHTML = "";
    this._appendSystem(`🔍 Resultados para: "${query}" (${results.length} encontrados)`);
    
    results.forEach(m => {
      this._appendMessage(m.role, m.text, m.created_at);
    });
  }

  _handleExport(data) {
    const { format, content, session_id } = data;
    if (!content) {
      this._toast("Error exportando", "error");
      return;
    }

    // Descargar archivo
    const ext = format === "json" ? "json" : "md";
    const mime = format === "json" ? "application/json" : "text/markdown";
    const filename = `session_${session_id}.${ext}`;
    
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    
    this._toast(`Exportado: ${filename}`, "success");
  }

  _loadMessagesFromDb(messages) {
    if (!messages || messages.length === 0) {
      this._toast("Sesión vacía", "info");
      return;
    }
    
    // Limpiar y cargar mensajes
    this.$messages.innerHTML = "";
    this._appendSystem(`📂 Sesión cargada (${messages.length} mensajes)`);
    
    messages.forEach(m => {
      const el = this._appendMessage(m.role, m.text, m.created_at);
      
      // Si tiene razonamiento, agregarlo
      if (m.has_thinking && m.thinking_text && el) {
        const thinking = document.createElement("details");
        thinking.className = "thinking-block";
        thinking.innerHTML = `
          <summary style="font-size:10px;color:var(--muted);cursor:pointer;">💭 Razonamiento</summary>
          <pre style="font-size:11px;color:var(--muted);margin-top:4px;white-space:pre-wrap;">${this._escapeHtml(m.thinking_text)}</pre>
        `;
        el.appendChild(thinking);
      }
    });
    
    this._scrollBottom();
  }

  _formatDate(isoString) {
    if (!isoString) return "?";
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString("es", { day: "2-digit", month: "short" });
    } catch {
      return "?";
    }
  }

  _formatRelativeDate(isoString) {
    if (!isoString) return "?";
    try {
      const timeValue = typeof isoString === "number" ? isoString * 1000 : new Date(isoString).getTime();
      const seconds = Math.max(0, Math.floor((Date.now() - timeValue) / 1000));
      return this._formatAge(seconds);
    } catch {
      return this._formatDate(isoString);
    }
  }

  _sessionEventTimestamp(session) {
    if (!session) return 0;
    if (typeof session.activity_timestamp === "number" && session.activity_timestamp > 0) {
      return session.activity_timestamp;
    }
    if (typeof session.preview_timestamp === "number" && session.preview_timestamp > 0) {
      return session.preview_timestamp;
    }
    if (session.updated_at) {
      const parsed = new Date(session.updated_at).getTime();
      if (!Number.isNaN(parsed)) {
        return Math.floor(parsed / 1000);
      }
    }
    return 0;
  }

  _deriveSessionState(session) {
    const rawStatus = session?.activity_status || "idle";
    const openToolCount = session?.open_tool_count || 0;
    const pendingApprovalCount = session?.pending_approval_count || 0;
    const activeTaskCount = session?.active_task_count || 0;
    const ts = this._sessionEventTimestamp(session);
    const ageSec = ts ? Math.max(0, Math.floor(Date.now() / 1000) - ts) : null;
    const hasSignal = !!(session?.preview_text || session?.activity_detail || session?.current_tool || ts);
    const detail = `${session?.activity_detail || ""} ${session?.preview_text || ""}`.toLowerCase();

    if (rawStatus === "error" || detail.includes("permission denied") || detail.includes("exit 1") || detail.includes("failed")) {
      return { key: "error", label: "Error", className: "error", priority: 0, ageSec };
    }
    if (pendingApprovalCount > 0 || rawStatus === "waiting_input") {
      return { key: "waiting", label: "Esperando confirmación", className: "waiting", priority: 2, ageSec };
    }

    if (openToolCount > 0 || (["working", "reasoning", "commentary", "tool_running"].includes(rawStatus) && (ageSec === null || ageSec <= 90 || activeTaskCount > 0))) {
      return { key: "working", label: "Trabajando", className: "working", priority: 1, ageSec };
    }
    if (rawStatus === "final" || rawStatus === "idle") {
      return { key: "finished", label: "Terminado", className: "finished", priority: 3, ageSec };
    }
    if (hasSignal && (ageSec === null || ageSec <= 120)) {
      return { key: "waiting", label: "Esperando", className: "waiting", priority: 2, ageSec };
    }
    return { key: "no_signal", label: "Sin señal", className: "no_signal", priority: 4, ageSec };
  }

  _deriveHeaderStateLabel(selected, live, selectedIsLive) {
    if (selectedIsLive && live?.agent_activity) {
      return this._deriveSessionState({
        activity_status: live.agent_activity.status || "idle",
        activity_detail: live.agent_activity.detail || "",
        current_tool: live.agent_activity.current_tool || "",
        open_tool_count: live.agent_activity.open_tool_count || 0,
        preview_text: selected?.preview_text || "",
        preview_timestamp: live.agent_activity.timestamp || 0,
        updated_at: selected?.updated_at || "",
      }).label;
    }
    if (selected) {
      return this._deriveSessionState(selected).label;
    }
    if (live?.agent_activity) {
      return this._deriveSessionState({
        activity_status: live.agent_activity.status || "idle",
        activity_detail: live.agent_activity.detail || "",
        current_tool: live.agent_activity.current_tool || "",
        open_tool_count: live.agent_activity.open_tool_count || 0,
        preview_timestamp: live.agent_activity.timestamp || 0,
      }).label;
    }
    return "Sin señal";
  }

  _sortSessions(sessions) {
    return [...sessions].sort((a, b) => {
      const aActive = this._isSessionActive(a) ? 1 : 0;
      const bActive = this._isSessionActive(b) ? 1 : 0;
      if (aActive !== bActive) return bActive - aActive;

      const aState = this._deriveSessionState(a);
      const bState = this._deriveSessionState(b);
      if (aState.priority !== bState.priority) return aState.priority - bState.priority;

      const aTs = this._sessionEventTimestamp(a);
      const bTs = this._sessionEventTimestamp(b);
      return bTs - aTs;
    });
  }

  _truncate(text, maxLength) {
    if (!text || text.length <= maxLength) return text || "";
    return `${text.slice(0, maxLength - 1)}…`;
  }

  _escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // MODAL DE CONFIGURACIÓN
  // ═══════════════════════════════════════════════════════════════════════════

  _initSettingsModal() {
    const modal = document.getElementById("settings-modal");
    if (!modal) return;

    // Abrir modal
    this._on("settings-btn", "click", () => this._openSettings());
    
    // Cerrar modal
    this._on("close-settings", "click", () => this._closeSettings());
    this._on("cancel-settings", "click", () => this._closeSettings());
    modal.querySelector(".modal-backdrop")?.addEventListener("click", () => this._closeSettings());
    
    // Guardar
    this._on("save-settings", "click", () => this._saveSettings());
    
    // Tabs
    modal.querySelectorAll(".tab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        modal.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        modal.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
        btn.classList.add("active");
        const tabId = `tab-${btn.dataset.tab}`;
        document.getElementById(tabId)?.classList.add("active");
      });
    });

    // Preview de voz
    this._on("preview-voice", "click", () => this._previewVoice());
    
    // Rate slider label
    this._on("settings-tts-rate", "input", (e) => {
      const lbl = document.getElementById("settings-rate-label");
      if (lbl) lbl.textContent = `${parseFloat(e.target.value).toFixed(1)}x`;
    });

    // Mostrar/ocultar grupo de modelo Whisper según proveedor
    this._on("settings-stt-provider", "change", (e) => {
      const whisperGroup = document.getElementById("whisper-model-group");
      if (whisperGroup) {
        whisperGroup.style.display = e.target.value === "whisper_local" ? "block" : "none";
      }
    });
  }

  _openSettings() {
    const modal = document.getElementById("settings-modal");
    if (!modal) return;
    
    // Cargar valores actuales
    this._loadSettingsToModal();
    
    // Cargar voces disponibles
    this._send({ action: "tts_get_voices" });
    
    // Cargar settings de DB
    this._send({ action: "db_get_settings" });
    
    modal.classList.remove("hidden");
  }

  _closeSettings() {
    const modal = document.getElementById("settings-modal");
    if (modal) modal.classList.add("hidden");
  }

  _loadSettingsToModal() {
    const p = this.prefs;
    
    // General
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (el.type === "checkbox") el.checked = !!val;
      else el.value = val ?? "";
    };
    
    setVal("settings-ws-url", p.wsUrl || WS_URL_DEFAULT);
    setVal("settings-ide", p.ideFilter || "all");
    setVal("settings-thinking", p.includeThinking);
    setVal("settings-codex-progress", p.includeCodexProgress);
    
    // TTS
    setVal("settings-tts-enabled", p.ttsEnabled);
    setVal("settings-llm-enabled", p.ttsLlmEnabled);
    setVal("settings-tts-rate", p.ttsRate || 1.0);
    const rateLbl = document.getElementById("settings-rate-label");
    if (rateLbl) rateLbl.textContent = `${(p.ttsRate || 1.0).toFixed(1)}x`;
    
    // Telegram
    setVal("settings-telegram-send", p.ttsTelegram);
    setVal("settings-telegram-receive", p.telegramInput);
  }

  _saveSettings() {
    const getVal = (id) => {
      const el = document.getElementById(id);
      if (!el) return undefined;
      if (el.type === "checkbox") return el.checked;
      return el.value;
    };
    
    // Actualizar preferencias locales
    this.prefs.wsUrl = getVal("settings-ws-url");
    this.prefs.ideFilter = getVal("settings-ide");
    this.prefs.includeThinking = getVal("settings-thinking");
    this.prefs.includeCodexProgress = getVal("settings-codex-progress");
    this.prefs.ttsEnabled = getVal("settings-tts-enabled");
    this.prefs.ttsLlmEnabled = getVal("settings-llm-enabled");
    this.prefs.ttsRate = parseFloat(getVal("settings-tts-rate")) || 1.0;
    this.prefs.ttsTelegram = getVal("settings-telegram-send");
    this.prefs.telegramInput = getVal("settings-telegram-receive");
    
    const voiceSelect = document.getElementById("settings-tts-voice");
    if (voiceSelect && voiceSelect.value) {
      this.prefs.ttsVoiceIndex = parseInt(voiceSelect.value);
    }
    
    this._savePrefs();
    
    // Enviar al servidor
    this._send({ action: "tts_enable", enabled: this.prefs.ttsEnabled });
    this._send({ action: "tts_llm_enable", enabled: this.prefs.ttsLlmEnabled });
    this._send({ action: "tts_set_rate", rate: this.prefs.ttsRate });
    this._send({ action: "tts_telegram_enable", enabled: this.prefs.ttsTelegram });
    this._send({ action: "telegram_input_enable", enabled: this.prefs.telegramInput });
    this._send({ action: "set_ide", ide: this.prefs.ideFilter });
    this._send({ action: "set_include_thinking", enabled: this.prefs.includeThinking });
    this._send({ action: "set_include_codex_progress", enabled: this.prefs.includeCodexProgress });
    
    if (this.prefs.ttsVoiceIndex !== undefined) {
      this._send({ action: "tts_set_voice", voice_index: this.prefs.ttsVoiceIndex });
    }
    
    // Guardar API keys en DB (si cambiaron)
    const geminiKey = getVal("settings-gemini-key");
    const groqKey = getVal("settings-groq-key");
    const telegramToken = getVal("settings-telegram-token");
    const telegramChatId = getVal("settings-telegram-chat-id");
    
    if (geminiKey) {
      this._dbSettings.GEMINI_API_KEY = geminiKey;
      this._send({ action: "db_set_setting", key: "GEMINI_API_KEY", value: geminiKey, encrypted: true });
    }
    if (groqKey) {
      this._dbSettings.GROQ_API_KEY = groqKey;
      this._send({ action: "db_set_setting", key: "GROQ_API_KEY", value: groqKey, encrypted: true });
    }
    if (telegramToken) {
      this._dbSettings.TELEGRAM_BOT_TOKEN = telegramToken;
      this._send({ action: "db_set_setting", key: "TELEGRAM_BOT_TOKEN", value: telegramToken, encrypted: true });
    }
    if (telegramChatId) {
      this._dbSettings.TELEGRAM_CHAT_ID = telegramChatId;
      this._send({ action: "db_set_setting", key: "TELEGRAM_CHAT_ID", value: telegramChatId });
    }
    
    // Guardar proveedor STT
    const sttProvider = getVal("settings-stt-provider");
    const whisperModel = getVal("settings-whisper-model");
    if (sttProvider) {
      this._dbSettings.STT_PROVIDER = sttProvider;
      this._send({ action: "db_set_setting", key: "STT_PROVIDER", value: sttProvider });
    }
    if (whisperModel) {
      this._dbSettings.WHISPER_MODEL = whisperModel;
      this._send({ action: "db_set_setting", key: "WHISPER_MODEL", value: whisperModel });
    }
    
    // Actualizar UI principal
    this._setToggle("tts-enabled", this.prefs.ttsEnabled);
    this._setToggle("tts-llm-enabled", this.prefs.ttsLlmEnabled);
    this._setSlider("tts-rate", this.prefs.ttsRate);
    this._setSelect("ide-selector", this.prefs.ideFilter);
    this._setToggle("include-thinking", this.prefs.includeThinking);
    this._setToggle("tts-telegram", this.prefs.ttsTelegram);
    this._setToggle("telegram-input", this.prefs.telegramInput);
    
    this._closeSettings();
    this._toast("Configuración guardada", "success");
  }

  _previewVoice() {
    const voiceSelect = document.getElementById("settings-tts-voice");
    if (!voiceSelect || !voiceSelect.value) return;
    
    const idx = parseInt(voiceSelect.value);
    // Enviar texto de prueba al TTS
    this._send({ action: "tts_set_voice", voice_index: idx });
    this._toast("Reproduciendo preview...", "info");
    
    // El servidor no tiene un endpoint de preview directo, 
    // así que usamos el cambio de voz como confirmación
  }

  _populateSettingsVoices(voices) {
    const sel = document.getElementById("settings-tts-voice");
    if (!sel) return;
    sel.innerHTML = "";
    voices.forEach((v) => {
      const opt = document.createElement("option");
      opt.value = v.index;
      opt.textContent = v.name || `Voz ${v.index + 1}`;
      sel.appendChild(opt);
    });
    if (this.prefs.ttsVoiceIndex != null) sel.value = this.prefs.ttsVoiceIndex;
  }

  _applyDbSettings(settings) {
    // Aplicar settings guardados en DB al modal
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (!el || val === undefined) return;
      if (el.type === "checkbox") el.checked = !!val;
      else el.value = val;
    };
    
    setVal("settings-stt-provider", settings.STT_PROVIDER);
    setVal("settings-whisper-model", settings.WHISPER_MODEL);
    
    // Mostrar/ocultar modelo Whisper según proveedor
    const whisperGroup = document.getElementById("whisper-model-group");
    if (whisperGroup) {
      whisperGroup.style.display = settings.STT_PROVIDER === "whisper_local" ? "block" : "none";
    }
    
    // No mostramos las API keys por seguridad (solo indicar si están configuradas)
    if (settings.GEMINI_API_KEY) {
      const el = document.getElementById("settings-gemini-key");
      if (el) el.placeholder = "••••••• (configurada)";
    }
    if (settings.GROQ_API_KEY) {
      const el = document.getElementById("settings-groq-key");
      if (el) el.placeholder = "••••••• (configurada)";
    }
    if (settings.TELEGRAM_BOT_TOKEN) {
      const el = document.getElementById("settings-telegram-token");
      if (el) el.placeholder = "••••••• (configurado)";
    }
    if (settings.TELEGRAM_CHAT_ID) {
      const el = document.getElementById("settings-telegram-chat-id");
      if (el) el.value = settings.TELEGRAM_CHAT_ID;
    }
  }

  async _toggleSttRecording() {
    if (this._sttRecording) {
      this._stopSttRecording();
      return;
    }
    await this._startSttRecording();
  }

  async _startSttRecording() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      this._updateSttStatus("Tu navegador no soporta grabación de micrófono.");
      this._toast("Micrófono no soportado en este navegador", "error");
      return;
    }

    try {
      this._sttStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = this._pickSttMimeType();
      this._sttChunks = [];
      this._sttRecorder = mimeType
        ? new MediaRecorder(this._sttStream, { mimeType })
        : new MediaRecorder(this._sttStream);
      this._sttRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) this._sttChunks.push(e.data);
      };
      this._sttRecorder.onstop = () => this._sendSttForTranscription();
      this._sttRecorder.start();
      this._sttRecording = true;
      this._updateSttButton();
      this._updateSttStatus("Grabando... pulsa de nuevo para detener.");
    } catch (e) {
      console.error("[stt] No se pudo iniciar grabación:", e);
      this._updateSttStatus("No pude acceder al micrófono.");
      this._toast("No pude acceder al micrófono", "error");
    }
  }

  _stopSttRecording() {
    if (!this._sttRecorder) return;
    this._sttRecording = false;
    this._updateSttButton();
    this._updateSttStatus("Transcribiendo audio...");
    this._sttRecorder.stop();
    if (this._sttStream) {
      this._sttStream.getTracks().forEach(t => t.stop());
      this._sttStream = null;
    }
  }

  _pickSttMimeType() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/ogg;codecs=opus",
      "audio/webm",
      "audio/ogg",
    ];
    return candidates.find(type => MediaRecorder.isTypeSupported(type)) || "";
  }

  async _sendSttForTranscription() {
    try {
      const blob = new Blob(
        this._sttChunks,
        { type: this._sttRecorder?.mimeType || this._pickSttMimeType() || "audio/webm" }
      );
      const provider = this._dbSettings.STT_PROVIDER || "groq";
      const apiUrl = new URL("/api/stt", window.location.origin);
      apiUrl.searchParams.set("provider", provider);
      const response = await fetch(apiUrl.toString(), {
        method: "POST",
        headers: {
          "Content-Type": blob.type || "application/octet-stream",
        },
        body: blob,
      });
      const result = await response.json().catch(() => ({
        ok: false,
        message: `HTTP ${response.status}`,
      }));
      if (!response.ok && result.ok !== false) {
        result.ok = false;
        result.message = result.message || `HTTP ${response.status}`;
      }
      await this._handleSttTranscription(result);
    } catch (e) {
      console.error("[stt] Error preparando audio:", e);
      this._updateSttStatus("No pude preparar el audio para transcribir.");
      this._toast("Error preparando audio", "error");
    } finally {
      this._sttRecorder = null;
      this._sttChunks = [];
    }
  }

  async _handleSttTranscription(msg) {
    if (!msg.ok) {
      this._updateSttStatus(msg.message || "No se pudo transcribir.");
      this._toast(msg.message || "Error transcribiendo audio", "error");
      return;
    }

    const text = msg.text || "";
    try {
      const copied = await this._copyTextToClipboard(text);
      const sentToLocalBridge = this._sendLocalBridgePaste(text);
      if (sentToLocalBridge) {
        this._updateSttStatus(`Transcrito con ${msg.provider || "STT"}, copiado y enviado al agente local.`);
      } else if (copied) {
        this._updateSttStatus(`Transcrito con ${msg.provider || "STT"} y copiado al portapapeles.`);
      } else {
        this._updateSttStatus(`Transcrito con ${msg.provider || "STT"}. Copia manual necesaria.`);
      }
      this._toast("Transcripción lista", "success");
      this._appendSystem(`🎙 Dictado: ${text}`);
    } catch (e) {
      console.error("[stt] No se pudo copiar al portapapeles:", e);
      this._updateSttStatus(`Transcrito con ${msg.provider || "STT"}, pero no pude copiar al portapapeles.`);
      this._appendSystem(`🎙 Dictado: ${text}`);
    }
  }

  async _copyTextToClipboard(text) {
    if (!text) return false;

    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (e) {
      console.warn("[stt] Clipboard API falló, probando fallback:", e);
    }

    try {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.top = "-1000px";
      textarea.style.left = "-1000px";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      textarea.setSelectionRange(0, textarea.value.length);
      const copied = document.execCommand("copy");
      document.body.removeChild(textarea);
      return copied;
    } catch (e) {
      console.error("[stt] Fallback de copy falló:", e);
      return false;
    }
  }

  _updateSttStatus(text) {
    const el = document.getElementById("stt-status");
    if (el) el.textContent = text;
  }

  _updateSttButton() {
    const btn = document.getElementById("stt-record");
    if (!btn) return;
    btn.textContent = this._sttRecording ? "⏹ Detener" : "🎙 Empezar";
  }

  _localBridgeUrl() {
    return (this.prefs.localPasteUrl || "ws://127.0.0.1:8766").trim();
  }

  _syncLocalBridgeConnection() {
    if (this.prefs.localPasteEnabled) {
      this._connectLocalBridge();
      return;
    }
    this._disconnectLocalBridge(false);
    this._updateLocalBridgeStatus("Agente local desactivado.", "muted");
  }

  _connectLocalBridge() {
    const url = this._localBridgeUrl();
    if (!url) {
      this._updateLocalBridgeStatus("Configura una URL válida para el agente local.", "warning");
      return;
    }
    if (this._localBridgeWs && [WebSocket.OPEN, WebSocket.CONNECTING].includes(this._localBridgeWs.readyState)) {
      return;
    }

    this._updateLocalBridgeStatus(`Conectando agente local (${url})...`, "warning");
    try {
      this._localBridgeWs = new WebSocket(url);
    } catch (error) {
      console.error("[local-bridge] No pude crear el WebSocket:", error);
      this._scheduleLocalBridgeReconnect();
      return;
    }

    this._localBridgeWs.onopen = () => {
      this._localBridgeConnected = true;
      this._localBridgeReconnectAttempts = 0;
      this._updateLocalBridgeStatus("Agente local conectado.", "connected");
      this._sendLocalBridgeMessage({ type: "hello", client: "vibe-voice-ui", origin: window.location.origin });
    };

    this._localBridgeWs.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        this._handleLocalBridgeMessage(payload);
      } catch (error) {
        console.error("[local-bridge] Mensaje inválido:", error);
      }
    };

    this._localBridgeWs.onclose = () => {
      this._localBridgeConnected = false;
      this._localBridgeWs = null;
      if (this.prefs.localPasteEnabled) {
        this._updateLocalBridgeStatus("Agente local desconectado. Reintentando...", "warning");
        this._scheduleLocalBridgeReconnect();
      } else {
        this._updateLocalBridgeStatus("Agente local desactivado.", "muted");
      }
    };

    this._localBridgeWs.onerror = () => {
      this._updateLocalBridgeStatus("No pude hablar con el agente local.", "error");
    };
  }

  _disconnectLocalBridge(closeMessage = true) {
    if (this._localBridgeReconnectTimer) {
      clearTimeout(this._localBridgeReconnectTimer);
      this._localBridgeReconnectTimer = null;
    }
    this._localBridgeReconnectAttempts = 0;
    this._localBridgeConnected = false;
    if (this._localBridgeWs) {
      const ws = this._localBridgeWs;
      this._localBridgeWs = null;
      if ([WebSocket.OPEN, WebSocket.CONNECTING].includes(ws.readyState)) {
        ws.close();
      }
    }
    if (closeMessage) {
      this._updateLocalBridgeStatus("Agente local desactivado.", "muted");
    }
  }

  _scheduleLocalBridgeReconnect() {
    if (!this.prefs.localPasteEnabled || this._localBridgeReconnectTimer) return;
    const delay = Math.min(
      RECONNECT_BASE_MS * Math.pow(1.5, this._localBridgeReconnectAttempts) + Math.random() * 400,
      RECONNECT_MAX_MS
    );
    this._localBridgeReconnectAttempts += 1;
    this._localBridgeReconnectTimer = setTimeout(() => {
      this._localBridgeReconnectTimer = null;
      this._connectLocalBridge();
    }, delay);
  }

  _sendLocalBridgeMessage(payload) {
    if (!this._localBridgeWs || this._localBridgeWs.readyState !== WebSocket.OPEN) {
      return false;
    }
    this._localBridgeWs.send(JSON.stringify(payload));
    return true;
  }

  _sendLocalBridgePaste(text) {
    if (!this.prefs.localPasteEnabled || !text) {
      return false;
    }
    if (!this._localBridgeConnected) {
      this._updateLocalBridgeStatus("El agente local no está conectado.", "warning");
      return false;
    }
    const sent = this._sendLocalBridgeMessage({ type: "paste", text });
    if (sent) {
      this._updateLocalBridgeStatus("Texto enviado al agente local.", "connected");
    }
    return sent;
  }

  _handleLocalBridgeMessage(payload) {
    const eventType = payload?.type || payload?.event || "";
    if (eventType === "hello" || eventType === "bridge.status") {
      const hotkey = payload.hotkey ? ` Hotkey: ${payload.hotkey}.` : "";
      const message = `${payload.message || "Agente local listo."}${hotkey}`;
      this._updateLocalBridgeStatus(message, "connected");
      return;
    }

    if (eventType === "bridge.hotkey" && payload.action === "toggle_stt") {
      this._toggleSttRecording();
      return;
    }

    if (eventType === "paste.result") {
      const ok = payload.ok !== false;
      this._updateLocalBridgeStatus(
        payload.message || (ok ? "Texto pegado por el agente local." : "El agente local no pudo pegar el texto."),
        ok ? "connected" : "error"
      );
      this._toast(ok ? "Pegado en tu PC" : "Falló el pegado local", ok ? "success" : "error");
      return;
    }

    if (eventType === "error") {
      this._updateLocalBridgeStatus(payload.message || "Error en el agente local.", "error");
    }
  }

  _updateLocalBridgeStatus(text, tone = "muted") {
    const el = document.getElementById("local-bridge-status");
    if (!el) return;
    el.textContent = text;
    el.className = "hint";
    if (tone === "connected") {
      el.classList.add("status-text-connected");
    } else if (tone === "warning") {
      el.classList.add("status-text-warning");
    } else if (tone === "error") {
      el.classList.add("status-text-error");
    }
  }

  // ─── Preferencias ──────────────────────────────────────────────────────────

  _loadPrefs() {
    try { return JSON.parse(localStorage.getItem("vibe_voice_prefs") || "{}"); }
    catch { return {}; }
  }

  _savePrefs() {
    try { localStorage.setItem("vibe_voice_prefs", JSON.stringify(this.prefs)); }
    catch {}
  }

  _applyPrefs() {
    const p = this.prefs;
    
    // DEFAULTS: todo activado por defecto
    if (p.autoScroll === undefined)      p.autoScroll      = true;
    if (p.showTimestamps === undefined)  p.showTimestamps  = true;
    if (p.ttsEnabled === undefined)      p.ttsEnabled      = true;
    if (p.ttsLlmEnabled === undefined)   p.ttsLlmEnabled   = true;
    if (p.audioVolume === undefined)     p.audioVolume     = 1;
    if (p.includeThinking === undefined) p.includeThinking = true;
    if (p.includeCodexProgress === undefined) p.includeCodexProgress = true;
    if (p.ideFilter === undefined)       p.ideFilter       = "all";
    if (p.localPasteEnabled === undefined) p.localPasteEnabled = false;
    if (p.localPasteUrl === undefined)   p.localPasteUrl   = "ws://127.0.0.1:8766";

    const setEl = (id, val) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (el.type === "checkbox") el.checked = !!val;
      else el.value = val;
    };

    setEl("auto-scroll",      p.autoScroll);
    setEl("show-timestamps",  p.showTimestamps);
    setEl("tts-enabled",      p.ttsEnabled);
    setEl("tts-llm-enabled",  p.ttsLlmEnabled);
    setEl("audio-volume",     p.audioVolume);
    setEl("include-thinking", p.includeThinking);
    setEl("ws-url",           p.wsUrl || WS_URL_DEFAULT);
    setEl("ide-selector",     p.ideFilter);
    setEl("stt-local-bridge", p.localPasteEnabled);
    this._updateIdeHint(p.ideFilter);
    this._updateLocalBridgeStatus(p.localPasteEnabled ? "Conectando agente local..." : "Agente local desactivado.");
    this._setAudioVolume(p.audioVolume, { save: false });
    this._syncAudioUnlockButton();

    if (p.ttsRate !== undefined) {
      setEl("tts-rate", p.ttsRate);
      const lbl = document.getElementById("tts-rate-label");
      if (lbl) lbl.textContent = `${parseFloat(p.ttsRate).toFixed(1)}x`;
    }
    
    // Guardar defaults
    this._savePrefs();
  }

  _updateIdeHint(ide) {
    const hint = document.getElementById("ide-hint");
    if (!hint) return;
    const names = {
      "all": "Monitoreando todos los IDEs",
      "vscode-insiders": "Solo VS Code Insiders",
      "vscode": "Solo VS Code",
      "cursor": "Solo Cursor",
      "codex": "Solo Codex CLI",
      "copilot": "Solo Copilot CLI"
    };
    hint.textContent = names[ide] || "Selecciona qué IDE monitorear";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  window.viewer = new VibeVoiceViewer();
});
