package com.vitranslate.pdf.ui.components

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun AdvancedEngineSetupDialog(
    initialUrl: String,
    onDismissRequest: () -> Unit,
    onSave: (serverUrl: String) -> Unit,
    onTestConnection: suspend (url: String) -> Boolean
) {
    var urlText by remember { mutableStateOf(initialUrl) }
    var connectionStatus by remember { mutableStateOf<String?>(null) }
    var isTesting by remember { mutableStateOf(false) }
    val coroutineScope = rememberCoroutineScope()

    AlertDialog(
        onDismissRequest = onDismissRequest,
        title = {
            Text(
                text = "Cấu hình Advanced Engine",
                style = MaterialTheme.typography.titleLarge
            )
        },
        text = {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text(
                    text = "Bản Standard Engine (PDFBox) chạy trực tiếp offline trên máy. Bật Advanced Engine để kết nối với máy chủ máy tính (C binary / ONNX) cho việc xử lý công thức nâng cao và OCR.",
                    style = MaterialTheme.typography.bodyMedium
                )

                OutlinedTextField(
                    value = urlText,
                    onValueChange = {
                        urlText = it
                        connectionStatus = null
                    },
                    label = { Text("URL máy chủ (VD: http://192.168.1.100:8000)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )

                connectionStatus?.let { status ->
                    Text(
                        text = status,
                        style = MaterialTheme.typography.bodySmall,
                        color = if (status.contains("thành công")) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error
                    )
                }
            }
        },
        confirmButton = {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(
                    onClick = {
                        isTesting = true
                        coroutineScope.launch(Dispatchers.IO) {
                            val success = onTestConnection(urlText)
                            withContext(Dispatchers.Main) {
                                connectionStatus = if (success) "Kết nối thành công!" else "Lỗi: Không thể kết nối tới máy chủ"
                                isTesting = false
                            }
                        }
                    },
                    enabled = !isTesting && urlText.isNotBlank()
                ) {
                    Text("Thử kết nối")
                }

                Button(
                    onClick = {
                        onSave(urlText)
                        onDismissRequest()
                    },
                    enabled = urlText.isNotBlank()
                ) {
                    Text("Lưu")
                }
            }
        },
        dismissButton = {
            OutlinedButton(onClick = onDismissRequest) {
                Text("Hủy")
            }
        }
    )
}
