package com.vitranslate.pdf.ui.components

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import java.io.File

@Composable
fun FooterView(
    isTranslating: Boolean,
    progress: Float,
    isIndeterminate: Boolean,
    statusText: String,
    lastOutputDirectory: String?
) {
    val context = LocalContext.current

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp, vertical = 16.dp)
    ) {
        if (isTranslating) {
            if (isIndeterminate) {
                LinearProgressIndicator(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(6.dp)
                        .padding(bottom = 8.dp),
                    color = MaterialTheme.colorScheme.primary
                )
            } else {
                LinearProgressIndicator(
                    progress = { progress },
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(6.dp)
                        .padding(bottom = 8.dp),
                    color = MaterialTheme.colorScheme.primary
                )
            }
        }

        Text(
            text = statusText,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.7f)
        )

        if (lastOutputDirectory != null) {
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = "Mở thư mục kết quả: $lastOutputDirectory",
                style = MaterialTheme.typography.bodyMedium.copy(textDecoration = TextDecoration.Underline),
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.clickable {
                    val file = File(lastOutputDirectory)
                    val uri = Uri.fromFile(file)
                    val intent = Intent(Intent.ACTION_VIEW).apply {
                        setDataAndType(uri, "*/*")
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    }
                    try {
                        context.startActivity(intent)
                    } catch (_: Exception) {}
                }
            )
        }
    }
}
