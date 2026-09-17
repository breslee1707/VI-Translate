package com.vitranslate.pdf.ui.components

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.vitranslate.pdf.repository.AiProvider
import kotlinx.coroutines.launch

enum class SelectedEngineType {
    GOOGLE_DEFAULT,
    OPENAI,
    DEEPSEEK,
    GEMINI,
    OPENROUTER,
    GROQ,
    SILICONFLOW,
    CUSTOM_OPENAI
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AiEngineSetupDialog(
    initialEngineType: SelectedEngineType = SelectedEngineType.GOOGLE_DEFAULT,
    initialApiKey: String = "",
    initialModelName: String = "gpt-4o-mini",
    initialEndpoint: String = "https://api.openai.com/v1/chat/completions",
    onDismissRequest: () -> Unit,
    onApplySession: (
        engineType: SelectedEngineType,
        apiKey: String,
        modelName: String,
        endpoint: String
    ) -> Unit
) {
    var selectedType by remember { mutableStateOf(initialEngineType) }
    var apiKeyText by remember { mutableStateOf(initialApiKey) }
    var modelNameText by remember { mutableStateOf(initialModelName) }
    var endpointText by remember { mutableStateOf(initialEndpoint) }
    var isPasswordVisible by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismissRequest,
        title = {
            Text(
                text = "Cấu hình AI Dịch (LLMs)",
                style = MaterialTheme.typography.titleLarge
            )
        },
        text = {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 4.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = MaterialTheme.shapes.small,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text(
                        text = "Cam kết bảo mật & Quyền riêng tư:\n" +
                                "• 100% Cục bộ & Server-less: Ứng dụng này không có máy chủ trung gian. Mọi quá trình xử lý PDF và giao tiếp API diễn ra trực tiếp trên điện thoại của bạn.\n" +
                                "• API Key được bảo vệ: Token API chỉ gửi trực tiếp từ thiết bị của bạn đến nhà cung cấp AI (OpenAI, Gemini, DeepSeek...) qua HTTPS mã hóa, và chỉ giữ tạm trong bộ nhớ RAM.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(10.dp)
                    )
                }

                Text(
                    text = "Chọn nhà cung cấp AI:",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold
                )

                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    RadioButton(
                        selected = selectedType == SelectedEngineType.GOOGLE_DEFAULT,
                        onClick = { selectedType = SelectedEngineType.GOOGLE_DEFAULT }
                    )
                    Text("Google Translate (Miễn phí)")
                }

                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    RadioButton(
                        selected = selectedType == SelectedEngineType.DEEPSEEK,
                        onClick = {
                            selectedType = SelectedEngineType.DEEPSEEK
                            modelNameText = "deepseek-chat"
                            endpointText = "https://api.deepseek.com/v1/chat/completions"
                        }
                    )
                    Text("DeepSeek (DeepSeek V3 / R1)")
                }

                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    RadioButton(
                        selected = selectedType == SelectedEngineType.GEMINI,
                        onClick = {
                            selectedType = SelectedEngineType.GEMINI
                            modelNameText = "gemini-2.0-flash"
                        }
                    )
                    Text("Google Gemini (Gemini 2.0 / 1.5 Flash)")
                }

                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    RadioButton(
                        selected = selectedType == SelectedEngineType.OPENAI,
                        onClick = {
                            selectedType = SelectedEngineType.OPENAI
                            modelNameText = "gpt-4o-mini"
                            endpointText = "https://api.openai.com/v1/chat/completions"
                        }
                    )
                    Text("OpenAI (GPT-4o, GPT-4o-mini)")
                }

                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    RadioButton(
                        selected = selectedType == SelectedEngineType.OPENROUTER,
                        onClick = {
                            selectedType = SelectedEngineType.OPENROUTER
                            modelNameText = "deepseek/deepseek-chat"
                            endpointText = "https://openrouter.ai/api/v1/chat/completions"
                        }
                    )
                    Text("OpenRouter (Claude, Llama, DeepSeek)")
                }

                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    RadioButton(
                        selected = selectedType == SelectedEngineType.GROQ,
                        onClick = {
                            selectedType = SelectedEngineType.GROQ
                            modelNameText = "llama-3.3-70b-versatile"
                            endpointText = "https://api.groq.com/openai/v1/chat/completions"
                        }
                    )
                    Text("Groq (Llama 3.3 70B, DeepSeek R1)")
                }

                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    RadioButton(
                        selected = selectedType == SelectedEngineType.SILICONFLOW,
                        onClick = {
                            selectedType = SelectedEngineType.SILICONFLOW
                            modelNameText = "deepseek-ai/DeepSeek-V3"
                            endpointText = "https://api.siliconflow.cn/v1/chat/completions"
                        }
                    )
                    Text("SiliconFlow (SiliconCloud)")
                }

                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    RadioButton(
                        selected = selectedType == SelectedEngineType.CUSTOM_OPENAI,
                        onClick = {
                            selectedType = SelectedEngineType.CUSTOM_OPENAI
                            if (endpointText.isBlank() || endpointText.contains("openai.com")) {
                                endpointText = "http://10.0.2.2:11434/v1/chat/completions"
                            }
                        }
                    )
                    Text("Custom LLM / Local (Ollama, LM Studio)")
                }

                if (selectedType != SelectedEngineType.GOOGLE_DEFAULT) {
                    OutlinedTextField(
                        value = apiKeyText,
                        onValueChange = { apiKeyText = it },
                        label = { Text("API Key / Token (Tạm thời)") },
                        visualTransformation = if (isPasswordVisible) VisualTransformation.None else PasswordVisualTransformation(),
                        trailingIcon = {
                            TextButton(onClick = { isPasswordVisible = !isPasswordVisible }) {
                                Text(if (isPasswordVisible) "Ẩn" else "Hiện")
                            }
                        },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    OutlinedTextField(
                        value = modelNameText,
                        onValueChange = { modelNameText = it },
                        label = { Text("Tên Model") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    if (selectedType == SelectedEngineType.CUSTOM_OPENAI || selectedType == SelectedEngineType.OPENAI) {
                        OutlinedTextField(
                            value = endpointText,
                            onValueChange = { endpointText = it },
                            label = { Text("Endpoint API URL") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }

                    val coroutineScope = rememberCoroutineScope()
                    var isTesting by remember { mutableStateOf(false) }
                    var testResult by remember { mutableStateOf<Result<com.vitranslate.pdf.repository.AiTranslateEngine.ApiHealthResult>?>(null) }
                    var sessionTokenRefreshTrigger by remember { mutableStateOf(0) }

                    OutlinedButton(
                        onClick = {
                            isTesting = true
                            testResult = null
                            coroutineScope.launch {
                                val provider = when (selectedType) {
                                    SelectedEngineType.DEEPSEEK -> com.vitranslate.pdf.repository.AiProvider.DEEPSEEK
                                    SelectedEngineType.GEMINI -> com.vitranslate.pdf.repository.AiProvider.GEMINI
                                    SelectedEngineType.OPENROUTER -> com.vitranslate.pdf.repository.AiProvider.OPENROUTER
                                    SelectedEngineType.GROQ -> com.vitranslate.pdf.repository.AiProvider.GROQ
                                    SelectedEngineType.SILICONFLOW -> com.vitranslate.pdf.repository.AiProvider.SILICONFLOW
                                    SelectedEngineType.CUSTOM_OPENAI -> com.vitranslate.pdf.repository.AiProvider.CUSTOM_OPENAI
                                    else -> com.vitranslate.pdf.repository.AiProvider.OPENAI
                                }
                                val result = com.vitranslate.pdf.repository.AiTranslateEngine.testConnection(
                                    provider = provider,
                                    apiKey = apiKeyText,
                                    modelName = modelNameText,
                                    customEndpoint = endpointText
                                )
                                testResult = result
                                isTesting = false
                                sessionTokenRefreshTrigger++
                            }
                        },
                        enabled = !isTesting,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        if (isTesting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                strokeWidth = 2.dp
                            )
                            Spacer(Modifier.width(8.dp))
                            Text("Đang kiểm tra kết nối...")
                        } else {
                            Text("Kiểm tra kết nối API")
                        }
                    }

                    testResult?.let { res ->
                        val isSuccess = res.isSuccess
                        val health = res.getOrNull()
                        Surface(
                            color = if (isSuccess) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.errorContainer,
                            shape = MaterialTheme.shapes.small,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(
                                text = if (isSuccess && health != null) {
                                    "Kết nối thành công! Phản hồi: ${health.latencyMs} ms | Token test: ~${health.totalTokens} (Input: ${health.promptTokens}, Output: ${health.completionTokens})"
                                } else {
                                    "Lỗi kết nối: ${res.exceptionOrNull()?.message ?: "Lỗi không xác định"}"
                                },
                                style = MaterialTheme.typography.bodySmall,
                                color = if (isSuccess) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onErrorContainer,
                                modifier = Modifier.padding(8.dp)
                            )
                        }
                    }

                    // Session Token Counter Card
                    val totalTokens = com.vitranslate.pdf.repository.AiTranslateEngine.sessionTotalTokens
                    val promptTokens = com.vitranslate.pdf.repository.AiTranslateEngine.sessionPromptTokens
                    val compTokens = com.vitranslate.pdf.repository.AiTranslateEngine.sessionCompletionTokens

                    Surface(
                        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
                        shape = MaterialTheme.shapes.small,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 8.dp)
                    ) {
                        Column(modifier = Modifier.padding(10.dp)) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    text = "Bộ đếm Token phiên này",
                                    style = MaterialTheme.typography.labelMedium,
                                    fontWeight = FontWeight.Bold
                                )
                                if (totalTokens > 0) {
                                    TextButton(
                                        onClick = {
                                            com.vitranslate.pdf.repository.AiTranslateEngine.resetSessionTokens()
                                            sessionTokenRefreshTrigger++
                                        },
                                        contentPadding = PaddingValues(horizontal = 4.dp, vertical = 0.dp)
                                    ) {
                                        Text("Đặt lại", style = MaterialTheme.typography.labelSmall)
                                    }
                                }
                            }
                            Spacer(Modifier.height(2.dp))
                            Text(
                                text = "Tổng token đã dùng: %,d tokens".format(totalTokens),
                                style = MaterialTheme.typography.bodySmall,
                                fontWeight = FontWeight.SemiBold
                            )
                            Text(
                                text = "Dữ liệu vào (Input): %,d  |  Dữ liệu ra (Output): %,d".format(promptTokens, compTokens),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    onApplySession(selectedType, apiKeyText, modelNameText, endpointText)
                    onDismissRequest()
                }
            ) {
                Text("Áp dụng cho phiên này")
            }
        },
        dismissButton = {
            OutlinedButton(onClick = onDismissRequest) {
                Text("Hủy")
            }
        }
    )
}
