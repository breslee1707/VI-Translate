package com.vitranslate.pdf.ui.components

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun LlmSettingsDialog(
    initialApiKey: String,
    initialBaseUrl: String,
    initialModelName: String,
    onDismiss: () -> Unit,
    onSave: (apiKey: String, baseUrl: String, modelName: String) -> Unit
) {
    var apiKey by remember { mutableStateOf(initialApiKey) }
    var baseUrl by remember { mutableStateOf(initialBaseUrl.ifBlank { "https://api.openai.com/v1" }) }
    var modelName by remember { mutableStateOf(initialModelName.ifBlank { "gpt-4o-mini" }) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Cấu hình AI (LLM)") },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text(
                    text = "Hỗ trợ OpenAI, OpenRouter, DeepSeek, hoặc Ollama cục bộ.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )

                OutlinedTextField(
                    value = apiKey,
                    onValueChange = { apiKey = it },
                    label = { Text("API Key (sk-...)") },
                    leadingIcon = { Icon(Icons.Default.Key, contentDescription = null) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )

                OutlinedTextField(
                    value = baseUrl,
                    onValueChange = { baseUrl = it },
                    label = { Text("Base URL Endpoint") },
                    leadingIcon = { Icon(Icons.Default.Link, contentDescription = null) },
                    singleLine = true,
                    placeholder = { Text("https://api.openai.com/v1") },
                    modifier = Modifier.fillMaxWidth()
                )

                OutlinedTextField(
                    value = modelName,
                    onValueChange = { modelName = it },
                    label = { Text("Tên Model AI") },
                    leadingIcon = { Icon(Icons.Default.Memory, contentDescription = null) },
                    singleLine = true,
                    placeholder = { Text("gpt-4o-mini / deepseek-chat") },
                    modifier = Modifier.fillMaxWidth()
                )

                // Quick presets
                Text(
                    text = "Mẫu cài đặt nhanh:",
                    style = MaterialTheme.typography.labelSmall
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    FilterChip(
                        selected = baseUrl.contains("openai.com"),
                        onClick = {
                            baseUrl = "https://api.openai.com/v1"
                            modelName = "gpt-4o-mini"
                        },
                        label = { Text("OpenAI") }
                    )
                    FilterChip(
                        selected = baseUrl.contains("deepseek"),
                        onClick = {
                            baseUrl = "https://api.deepseek.com/v1"
                            modelName = "deepseek-chat"
                        },
                        label = { Text("DeepSeek") }
                    )
                    FilterChip(
                        selected = baseUrl.contains("openrouter"),
                        onClick = {
                            baseUrl = "https://openrouter.ai/api/v1"
                            modelName = "google/gemini-flash-1.5"
                        },
                        label = { Text("OpenRouter") }
                    )
                }
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    onSave(apiKey.trim(), baseUrl.trim(), modelName.trim())
                    onDismiss()
                }
            ) {
                Text("Lưu cài đặt")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Hủy")
            }
        }
    )
}
