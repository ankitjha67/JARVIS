package com.jarvis.app

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

// Theme-aware color palette
data class JarvisPalette(
    val bg: Color, val surface: Color, val accent: Color, val accentDim: Color,
    val text: Color, val textDim: Color, val user: Color, val assistant: Color,
    val error: Color, val success: Color
)

val IonPalette = JarvisPalette(
    bg = Color(0xFF0A0E17), surface = Color(0xFF111827),
    accent = Color(0xFF00D4FF), accentDim = Color(0xFF0094B3),
    text = Color(0xFFD6ECFF), textDim = Color(0xFF7A90A3),
    user = Color(0xFF1A2332), assistant = Color(0xFF0D1520),
    error = Color(0xFFFF4466), success = Color(0xFF00FF88)
)
val HolographicPalette = JarvisPalette(
    bg = Color(0xFF060913), surface = Color(0xFF0B1120),
    accent = Color(0xFF00F0FF), accentDim = Color(0xFF0266FF),
    text = Color(0xFFE0E2F1), textDim = Color(0xFFB9CACB),
    user = Color(0xFF0F172A), assistant = Color(0xFF0B1120),
    error = Color(0xFFFF2A6D), success = Color(0xFF00F5A0)
)
val SovereignPalette = JarvisPalette(
    bg = Color(0xFF08080A), surface = Color(0xFF131317),
    accent = Color(0xFFF2CA50), accentDim = Color(0xFFD4AF37),
    text = Color(0xFFE4E1E7), textDim = Color(0xFF99907C),
    user = Color(0xFF1E1C18), assistant = Color(0xFF131317),
    error = Color(0xFFA84234), success = Color(0xFFE0CBB7)
)
fun paletteFor(theme: String) = when (theme) {
    "holographic" -> HolographicPalette; "sovereign" -> SovereignPalette; else -> IonPalette
}
val LocalPalette = compositionLocalOf { IonPalette }

class MainActivity : ComponentActivity() {

    private val clientScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val jarvis = JarvisClient(clientScope)
    private var speechRecognizer: SpeechRecognizer? = null
    private lateinit var prefs: SharedPreferences

    private val requestPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) startVoiceInput()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("jarvis_connection", Context.MODE_PRIVATE)
        val host = prefs.getString("host", "192.168.1.100") ?: "192.168.1.100"
        val port = prefs.getInt("port", 47821)
        setContent {
            JarvisApp(
                jarvis = jarvis,
                onVoiceClick = ::onVoiceClick,
                savedHost = host,
                savedPort = port,
                onSaveConnection = { h, p ->
                    prefs.edit().putString("host", h).putInt("port", p).apply()
                }
            )
        }
        jarvis.connect(host, port)
    }

    override fun onResume() {
        super.onResume()
        if (jarvis.state.value != ConnectionState.CONNECTED &&
            jarvis.state.value != ConnectionState.CONNECTING) {
            val host = prefs.getString("host", "192.168.1.100") ?: "192.168.1.100"
            val port = prefs.getInt("port", 47821)
            jarvis.connect(host, port)
        }
    }

    private fun onVoiceClick() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED) {
            startVoiceInput()
        } else {
            requestPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun startVoiceInput() {
        speechRecognizer?.destroy()
        speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
            setRecognitionListener(object : RecognitionListener {
                override fun onResults(results: Bundle?) {
                    val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        ?.firstOrNull() ?: return
                    if (text.isNotBlank()) jarvis.send(text)
                }
                override fun onReadyForSpeech(params: Bundle?) {}
                override fun onBeginningOfSpeech() {}
                override fun onRmsChanged(rmsdB: Float) {}
                override fun onBufferReceived(buffer: ByteArray?) {}
                override fun onEndOfSpeech() {}
                override fun onError(error: Int) {}
                override fun onPartialResults(partialResults: Bundle?) {}
                override fun onEvent(eventType: Int, params: Bundle?) {}
            })
            startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
            })
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        speechRecognizer?.destroy()
        jarvis.disconnect()
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun JarvisApp(
    jarvis: JarvisClient,
    onVoiceClick: () -> Unit,
    savedHost: String,
    savedPort: Int,
    onSaveConnection: (String, Int) -> Unit
) {
    val connectionState by jarvis.state.collectAsState()
    val messages by jarvis.messages.collectAsState()
    val agentState by jarvis.agentState.collectAsState()
    val error by jarvis.error.collectAsState()
    val theme by jarvis.theme.collectAsState()
    var inputText by remember { mutableStateOf("") }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val focusManager = LocalFocusManager.current
    var showSettings by remember { mutableStateOf(false) }

    CompositionLocalProvider(LocalPalette provides paletteFor(theme)) {
    val c = LocalPalette.current

    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) {
            listState.animateScrollToItem(messages.size - 1)
        }
    }

    if (showSettings) {
        ConnectionSettingsSheet(
            currentHost = savedHost,
            currentPort = savedPort,
            connectionState = connectionState,
            onDismiss = { showSettings = false },
            onConnect = { host, port ->
                onSaveConnection(host, port)
                jarvis.connect(host, port)
                showSettings = false
            }
        )
    }

    Scaffold(
        containerColor = c.bg,
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        StatusDot(connectionState, agentState)
                        Spacer(Modifier.width(10.dp))
                        Text("JARVIS", color = c.accent, fontSize = 20.sp,
                            fontWeight = FontWeight.Bold, fontFamily = FontFamily.Monospace,
                            letterSpacing = 4.sp)
                    }
                },
                actions = {
                    IconButton(onClick = { jarvis.newConversation() }) {
                        Icon(Icons.Default.Add, "New conversation", tint = c.textDim)
                    }
                    IconButton(onClick = { showSettings = true }) {
                        Icon(Icons.Default.Settings, "Connection settings", tint = c.textDim)
                    }
                    if (connectionState != ConnectionState.CONNECTED) {
                        IconButton(onClick = { jarvis.connect(savedHost, savedPort) }) {
                            Icon(Icons.Default.Refresh, "Reconnect", tint = c.accent)
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = c.surface)
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            // Error banner
            AnimatedVisibility(error.isNotEmpty() && connectionState == ConnectionState.ERROR) {
                Surface(color = c.error.copy(alpha = 0.15f), modifier = Modifier.fillMaxWidth()) {
                    Text(error, color = c.error, modifier = Modifier.padding(12.dp), fontSize = 13.sp)
                }
            }

            // Messages
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth(),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                if (messages.isEmpty() && connectionState == ConnectionState.CONNECTED) {
                    item { EmptyState() }
                }
                items(messages, key = { "${it.timestamp}_${it.role}_${it.text.hashCode()}" }) { msg ->
                    MessageBubble(msg)
                }
                if (agentState == "thinking" || agentState == "acting") {
                    item { ThinkingIndicator(agentState) }
                }
            }

            // Input bar
            Surface(
                color = c.surface,
                shadowElevation = 8.dp,
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    // Voice button
                    IconButton(
                        onClick = onVoiceClick,
                        modifier = Modifier.size(44.dp)
                    ) {
                        Icon(Icons.Default.Mic, "Voice input",
                            tint = if (connectionState == ConnectionState.CONNECTED) c.accent else c.textDim)
                    }

                    // Text input
                    OutlinedTextField(
                        value = inputText,
                        onValueChange = { inputText = it },
                        modifier = Modifier.weight(1f),
                        placeholder = { Text("Ask Jarvis...", color = c.textDim) },
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedTextColor = c.text,
                            unfocusedTextColor = c.text,
                            focusedBorderColor = c.accent,
                            unfocusedBorderColor = c.textDim.copy(alpha = 0.3f),
                            cursorColor = c.accent,
                            focusedContainerColor = c.bg,
                            unfocusedContainerColor = c.bg,
                        ),
                        shape = RoundedCornerShape(24.dp),
                        singleLine = false,
                        maxLines = 4,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                        keyboardActions = KeyboardActions(onSend = {
                            if (inputText.isNotBlank() && connectionState == ConnectionState.CONNECTED) {
                                jarvis.send(inputText.trim())
                                inputText = ""
                                focusManager.clearFocus()
                            }
                        })
                    )

                    Spacer(Modifier.width(6.dp))

                    // Send / Cancel button
                    if (agentState == "thinking" || agentState == "acting") {
                        IconButton(
                            onClick = { jarvis.cancel() },
                            modifier = Modifier.size(44.dp).clip(CircleShape)
                                .background(c.error.copy(alpha = 0.2f))
                        ) {
                            Icon(Icons.Default.Close, "Cancel", tint = c.error)
                        }
                    } else {
                        IconButton(
                            onClick = {
                                if (inputText.isNotBlank() && connectionState == ConnectionState.CONNECTED) {
                                    jarvis.send(inputText.trim())
                                    inputText = ""
                                    focusManager.clearFocus()
                                }
                            },
                            enabled = inputText.isNotBlank() && connectionState == ConnectionState.CONNECTED,
                            modifier = Modifier.size(44.dp).clip(CircleShape)
                                .background(if (inputText.isNotBlank()) c.accent.copy(alpha = 0.2f) else Color.Transparent)
                        ) {
                            Icon(Icons.AutoMirrored.Filled.Send, "Send",
                                tint = if (inputText.isNotBlank()) c.accent else c.textDim)
                        }
                    }
                }
            }
        }
    }
    } // CompositionLocalProvider
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConnectionSettingsSheet(
    currentHost: String,
    currentPort: Int,
    connectionState: ConnectionState,
    onDismiss: () -> Unit,
    onConnect: (String, Int) -> Unit
) {
    val c = LocalPalette.current
    var host by remember { mutableStateOf(currentHost) }
    var portText by remember { mutableStateOf(currentPort.toString()) }

    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = c.surface,
        dragHandle = {
            Box(
                Modifier.padding(vertical = 12.dp)
                    .size(32.dp, 4.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(c.textDim.copy(alpha = 0.4f))
            )
        }
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp)
                .padding(bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Text(
                "CONNECTION", color = c.accent, fontSize = 14.sp,
                fontWeight = FontWeight.Bold, letterSpacing = 3.sp,
                fontFamily = FontFamily.Monospace
            )

            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Box(
                    Modifier.size(8.dp).clip(CircleShape).background(
                        when (connectionState) {
                            ConnectionState.CONNECTED -> c.success
                            ConnectionState.CONNECTING -> Color(0xFFFFAA00)
                            else -> c.error
                        }
                    )
                )
                Text(
                    when (connectionState) {
                        ConnectionState.CONNECTED -> "Connected"
                        ConnectionState.CONNECTING -> "Connecting..."
                        ConnectionState.ERROR -> "Connection error"
                        ConnectionState.DISCONNECTED -> "Disconnected"
                    },
                    color = c.text, fontSize = 13.sp
                )
            }

            OutlinedTextField(
                value = host,
                onValueChange = { host = it },
                label = { Text("Host / IP Address", color = c.textDim) },
                placeholder = { Text("192.168.1.100", color = c.textDim.copy(alpha = 0.4f)) },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                colors = OutlinedTextFieldDefaults.colors(
                    focusedTextColor = c.text,
                    unfocusedTextColor = c.text,
                    focusedBorderColor = c.accent,
                    unfocusedBorderColor = c.textDim.copy(alpha = 0.3f),
                    cursorColor = c.accent,
                    focusedContainerColor = c.bg,
                    unfocusedContainerColor = c.bg,
                    focusedLabelColor = c.accent,
                    unfocusedLabelColor = c.textDim,
                ),
                shape = RoundedCornerShape(12.dp)
            )

            OutlinedTextField(
                value = portText,
                onValueChange = { portText = it.filter { ch -> ch.isDigit() } },
                label = { Text("Port", color = c.textDim) },
                placeholder = { Text("47821", color = c.textDim.copy(alpha = 0.4f)) },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedTextColor = c.text,
                    unfocusedTextColor = c.text,
                    focusedBorderColor = c.accent,
                    unfocusedBorderColor = c.textDim.copy(alpha = 0.3f),
                    cursorColor = c.accent,
                    focusedContainerColor = c.bg,
                    unfocusedContainerColor = c.bg,
                    focusedLabelColor = c.accent,
                    unfocusedLabelColor = c.textDim,
                ),
                shape = RoundedCornerShape(12.dp)
            )

            Text(
                "Enter your desktop's local IP and Jarvis port. The app will auto-reconnect if the connection drops.",
                color = c.textDim, fontSize = 12.sp, lineHeight = 16.sp
            )

            Button(
                onClick = {
                    val port = portText.toIntOrNull() ?: 47821
                    onConnect(host.trim().ifEmpty { "192.168.1.100" }, port)
                },
                modifier = Modifier.fillMaxWidth().height(48.dp),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(containerColor = c.accent)
            ) {
                Text(
                    if (connectionState == ConnectionState.CONNECTING) "CONNECTING..." else "CONNECT",
                    color = c.bg, fontWeight = FontWeight.Bold,
                    letterSpacing = 2.sp, fontFamily = FontFamily.Monospace
                )
            }
        }
    }
}

@Composable
fun StatusDot(state: ConnectionState, agentState: String) {
    val c = LocalPalette.current
    val color = when {
        state == ConnectionState.CONNECTED && agentState == "thinking" -> c.accent
        state == ConnectionState.CONNECTED && agentState == "acting" -> Color(0xFFFFAA00)
        state == ConnectionState.CONNECTED -> c.success
        state == ConnectionState.CONNECTING -> Color(0xFFFFAA00)
        else -> c.error
    }

    val infiniteTransition = rememberInfiniteTransition(label = "pulse")
    val alpha by infiniteTransition.animateFloat(
        initialValue = 1f,
        targetValue = if (agentState == "thinking" || agentState == "acting" || state == ConnectionState.CONNECTING) 0.3f else 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(800, easing = EaseInOutCubic),
            repeatMode = RepeatMode.Reverse
        ),
        label = "pulse_alpha"
    )

    Box(
        modifier = Modifier.size(10.dp).clip(CircleShape)
            .background(color.copy(alpha = alpha))
    )
}

@Composable
fun MessageBubble(msg: ChatMessage) {
    val c = LocalPalette.current
    val isUser = msg.role == "user"
    val isStreaming = msg.role == "assistant_streaming"

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start
    ) {
        Surface(
            color = if (isUser) c.user else c.assistant,
            shape = RoundedCornerShape(
                topStart = 16.dp, topEnd = 16.dp,
                bottomStart = if (isUser) 16.dp else 4.dp,
                bottomEnd = if (isUser) 4.dp else 16.dp
            ),
            border = if (!isUser) androidx.compose.foundation.BorderStroke(
                1.dp, c.accent.copy(alpha = 0.1f)
            ) else null,
            modifier = Modifier.widthIn(max = 320.dp)
        ) {
            Column(modifier = Modifier.padding(12.dp)) {
                if (!isUser) {
                    Text(
                        if (isStreaming) "JARVIS ..." else "JARVIS",
                        color = c.accent,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = 2.sp,
                        fontFamily = FontFamily.Monospace
                    )
                    Spacer(Modifier.height(4.dp))
                }
                Text(
                    msg.text,
                    color = c.text,
                    fontSize = 15.sp,
                    lineHeight = 22.sp
                )
            }
        }
    }
}

@Composable
fun ThinkingIndicator(state: String) {
    val c = LocalPalette.current
    val infiniteTransition = rememberInfiniteTransition(label = "dots")
    val dotCount by infiniteTransition.animateFloat(
        initialValue = 0f, targetValue = 3f,
        animationSpec = infiniteRepeatable(tween(1200), RepeatMode.Restart),
        label = "dot_count"
    )

    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
        horizontalArrangement = Arrangement.Start
    ) {
        Surface(
            color = c.assistant,
            shape = RoundedCornerShape(16.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, c.accent.copy(alpha = 0.15f))
        ) {
            Row(modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text(
                    if (state == "thinking") "Thinking" else "Running tool",
                    color = c.accent,
                    fontSize = 13.sp,
                    fontFamily = FontFamily.Monospace
                )
                Text(
                    ".".repeat(dotCount.toInt() + 1),
                    color = c.accent,
                    fontSize = 13.sp,
                    fontFamily = FontFamily.Monospace
                )
            }
        }
    }
}

@Composable
fun EmptyState() {
    val c = LocalPalette.current
    Column(
        modifier = Modifier.fillMaxWidth().padding(vertical = 80.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text("J", color = c.accent, fontSize = 64.sp,
            fontWeight = FontWeight.Thin, fontFamily = FontFamily.Monospace)
        Spacer(Modifier.height(16.dp))
        Text("JARVIS READY", color = c.textDim,
            fontSize = 14.sp, letterSpacing = 4.sp, fontFamily = FontFamily.Monospace)
        Spacer(Modifier.height(8.dp))
        Text("Tap the mic or type a command", color = c.textDim.copy(alpha = 0.5f),
            fontSize = 12.sp)
    }
}
