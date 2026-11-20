package com.jarvis.app

import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import okhttp3.*
import org.json.JSONObject
import java.util.concurrent.TimeUnit

enum class ConnectionState { DISCONNECTED, CONNECTING, CONNECTED, ERROR }

data class ChatMessage(
    val role: String,  // "user" or "assistant"
    val text: String,
    val timestamp: Long = System.currentTimeMillis()
)

class JarvisClient(private val scope: CoroutineScope) {

    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private var ws: WebSocket? = null
    private var token: String = ""
    private var wsUrl: String = "ws://127.0.0.1:47821/ws"

    private val _state = MutableStateFlow(ConnectionState.DISCONNECTED)
    val state: StateFlow<ConnectionState> = _state

    private val _messages = MutableStateFlow<List<ChatMessage>>(emptyList())
    val messages: StateFlow<List<ChatMessage>> = _messages

    private val _agentState = MutableStateFlow("idle")
    val agentState: StateFlow<String> = _agentState

    private val _error = MutableStateFlow("")
    val error: StateFlow<String> = _error

    private val _theme = MutableStateFlow("ion")
    val theme: StateFlow<String> = _theme

    private var currentDelta = StringBuilder()
    private var currentRunId = ""

    private var lastHost = "127.0.0.1"
    private var lastPort = 47821
    private var shouldReconnect = true
    private var reconnectJob: Job? = null
    private var reconnectAttempt = 0
    private val maxBackoff = 30_000L

    fun connect(host: String = "127.0.0.1", port: Int = 47821) {
        lastHost = host
        lastPort = port
        shouldReconnect = true
        reconnectAttempt = 0
        reconnectJob?.cancel()
        ws?.close(1000, "new connection")

        scope.launch(Dispatchers.IO) {
            doConnect(host, port)
        }
    }

    private suspend fun doConnect(host: String, port: Int) {
        _state.value = ConnectionState.CONNECTING
        _error.value = ""
        try {
            val pairReq = Request.Builder()
                .url("http://$host:$port/api/pair")
                .header("Host", "$host:$port")
                .build()
            val resp = client.newCall(pairReq).execute()
            if (!resp.isSuccessful) {
                _state.value = ConnectionState.ERROR
                _error.value = "Pairing failed: HTTP ${resp.code}"
                scheduleReconnect()
                return
            }
            val body = JSONObject(resp.body?.string() ?: "{}")
            token = body.optString("token", "")
            wsUrl = body.optString("ws", "ws://$host:$port/ws")
            if (token.isEmpty()) {
                _state.value = ConnectionState.ERROR
                _error.value = "No token received"
                scheduleReconnect()
                return
            }
            connectWs(host, port)
        } catch (e: Exception) {
            _state.value = ConnectionState.ERROR
            _error.value = "Connection failed: ${e.message}"
            scheduleReconnect()
        }
    }

    private fun scheduleReconnect() {
        if (!shouldReconnect) return
        reconnectJob?.cancel()
        reconnectJob = scope.launch(Dispatchers.IO) {
            val delayMs = (1000L * (1 shl reconnectAttempt.coerceAtMost(5))).coerceAtMost(maxBackoff)
            reconnectAttempt++
            delay(delayMs)
            if (shouldReconnect && _state.value != ConnectionState.CONNECTED) {
                doConnect(lastHost, lastPort)
            }
        }
    }

    private fun connectWs(host: String, port: Int) {
        val request = Request.Builder()
            .url(wsUrl)
            .header("Host", "$host:$port")
            .build()

        ws = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                val hello = JSONObject().apply {
                    put("type", "hello")
                    put("token", token)
                    put("v", 1)
                }
                webSocket.send(hello.toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                scope.launch { handleMessage(text) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                _state.value = ConnectionState.ERROR
                _error.value = "WebSocket error: ${t.message}"
                scheduleReconnect()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                _state.value = ConnectionState.DISCONNECTED
                scheduleReconnect()
            }
        })
    }

    private fun handleMessage(raw: String) {
        try {
            val msg = JSONObject(raw)
            when (msg.optString("type")) {
                "ready" -> {
                    _state.value = ConnectionState.CONNECTED
                    reconnectAttempt = 0
                    _agentState.value = msg.optString("state", "idle")
                    val settings = msg.optJSONObject("settings")
                    if (settings != null) _theme.value = settings.optString("theme", "ion")
                    // Load history
                    val history = msg.optJSONArray("history")
                    if (history != null) {
                        val msgs = mutableListOf<ChatMessage>()
                        for (i in 0 until history.length()) {
                            val h = history.getJSONObject(i)
                            msgs.add(ChatMessage(
                                role = h.optString("role", "user"),
                                text = h.optString("text", "")
                            ))
                        }
                        _messages.value = msgs
                    }
                }
                "state" -> {
                    _agentState.value = msg.optString("state", "idle")
                }
                "user.echo" -> {
                    addMessage(ChatMessage("user", msg.optString("text", "")))
                }
                "assistant.start" -> {
                    currentDelta.clear()
                    currentRunId = msg.optString("run_id", "")
                }
                "assistant.delta" -> {
                    currentDelta.append(msg.optString("text", ""))
                    // Update the last assistant message in-place for streaming
                    updateStreamingMessage(currentDelta.toString())
                }
                "assistant.done" -> {
                    if (currentDelta.isNotEmpty()) {
                        finalizeStreamingMessage(currentDelta.toString())
                    }
                    currentDelta.clear()
                }
                "error" -> {
                    addMessage(ChatMessage("assistant", "Error: ${msg.optString("message", "Unknown error")}"))
                }
                "settings" -> {
                    val settings = msg.optJSONObject("settings")
                    if (settings != null) _theme.value = settings.optString("theme", "ion")
                }
                "notice" -> {
                    // Silently handle notices
                }
            }
        } catch (_: Exception) {
            // Ignore parse errors for unknown message types
        }
    }

    private fun addMessage(msg: ChatMessage) {
        _messages.value = _messages.value + msg
    }

    private fun updateStreamingMessage(text: String) {
        val current = _messages.value.toMutableList()
        if (current.isNotEmpty() && current.last().role == "assistant_streaming") {
            current[current.lastIndex] = ChatMessage("assistant_streaming", text)
        } else {
            current.add(ChatMessage("assistant_streaming", text))
        }
        _messages.value = current
    }

    private fun finalizeStreamingMessage(text: String) {
        val current = _messages.value.toMutableList()
        if (current.isNotEmpty() && current.last().role == "assistant_streaming") {
            current[current.lastIndex] = ChatMessage("assistant", text)
        } else {
            current.add(ChatMessage("assistant", text))
        }
        _messages.value = current
    }

    fun send(text: String) {
        val msg = JSONObject().apply {
            put("type", "user.message")
            put("text", text)
        }
        ws?.send(msg.toString())
    }

    fun cancel() {
        val msg = JSONObject().apply { put("type", "run.cancel") }
        ws?.send(msg.toString())
    }

    fun disconnect() {
        shouldReconnect = false
        reconnectJob?.cancel()
        ws?.close(1000, "user disconnected")
        ws = null
        _state.value = ConnectionState.DISCONNECTED
    }

    fun newConversation() {
        val msg = JSONObject().apply { put("type", "conversation.new") }
        ws?.send(msg.toString())
        _messages.value = emptyList()
    }
}
