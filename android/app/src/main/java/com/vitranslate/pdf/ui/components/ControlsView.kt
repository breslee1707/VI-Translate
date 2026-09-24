package com.vitranslate.pdf.ui.components

import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Translate
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.documentfile.provider.DocumentFile
import com.vitranslate.pdf.model.TargetLanguage

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ControlsView(
    selectedLanguage: TargetLanguage,
    onLanguageSelected: (TargetLanguage) -> Unit,
    engineType: SelectedEngineType = SelectedEngineType.GOOGLE_DEFAULT,
    onOpenEngineConfig: () -> Unit = {},
    useOcr: Boolean = true,
    onUseOcrChange: (Boolean) -> Unit = {},
    overwrite: Boolean,
    onOverwriteChange: (Boolean) -> Unit,
    customSaveDirectory: String?,
    onPickSaveDirectory: () -> Unit,
    isTranslating: Boolean,
    onStartTranslation: () -> Unit,
    onCancelTranslation: () -> Unit
) {
    var expanded by remember { mutableStateOf(false) }
    val context = LocalContext.current

    val folderDisplayName = remember(customSaveDirectory) {
        if (customSaveDirectory.isNullOrBlank()) {
            "Mặc định (Bộ nhớ ứng dụng)"
        } else if (customSaveDirectory.startsWith("content://")) {
            try {
                val uri = Uri.parse(customSaveDirectory)
                val docTree = DocumentFile.fromTreeUri(context, uri)
                docTree?.name ?: uri.lastPathSegment?.substringAfterLast(":") ?: customSaveDirectory
            } catch (_: Exception) {
                customSaveDirectory
            }
        } else {
            customSaveDirectory
        }
    }

    val engineDisplayName = remember(engineType) {
        when (engineType) {
            SelectedEngineType.GOOGLE_DEFAULT -> "Google Translate (Mặc định)"
            SelectedEngineType.DEEPSEEK -> "DeepSeek AI (V3/R1)"
            SelectedEngineType.GEMINI -> "Google Gemini AI (2.0/1.5)"
            SelectedEngineType.OPENAI -> "OpenAI (GPT-4o)"
            SelectedEngineType.OPENROUTER -> "OpenRouter"
            SelectedEngineType.GROQ -> "Groq (Ultra Fast)"
            SelectedEngineType.SILICONFLOW -> "SiliconCloud (DeepSeek)"
            SelectedEngineType.CUSTOM_OPENAI -> "Custom / Local LLM"
        }
    }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 12.dp),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerLow
        )
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = "Dịch sang",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Spacer(Modifier.height(6.dp))

            ExposedDropdownMenuBox(
                expanded = expanded,
                onExpandedChange = { if (!isTranslating) expanded = !expanded }
            ) {
                OutlinedTextField(
                    value = selectedLanguage.name,
                    onValueChange = {},
                    readOnly = true,
                    enabled = !isTranslating,
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodyMedium,
                    trailingIcon = {
                        ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded)
                    },
                    modifier = Modifier
                        .menuAnchor(MenuAnchorType.PrimaryNotEditable, enabled = !isTranslating)
                        .fillMaxWidth(),
                    shape = RoundedCornerShape(12.dp)
                )

                ExposedDropdownMenu(
                    expanded = expanded,
                    onDismissRequest = { expanded = false }
                ) {
                    TargetLanguage.SUPPORTED_LANGUAGES.forEach { language ->
                        DropdownMenuItem(
                            text = {
                                Text(language.name, style = MaterialTheme.typography.bodyMedium)
                            },
                            onClick = {
                                onLanguageSelected(language)
                                expanded = false
                            }
                        )
                    }
                }
            }

            Spacer(Modifier.height(10.dp))

            // Engine Selection & AI Setup Button
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    imageVector = Icons.Default.AutoAwesome,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(18.dp)
                )
                Spacer(Modifier.width(8.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "Công cụ dịch",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Text(
                        text = engineDisplayName,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurface,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
                TextButton(
                    onClick = onOpenEngineConfig,
                    enabled = !isTranslating
                ) {
                    Text("Cấu hình AI", style = MaterialTheme.typography.labelLarge)
                }
            }

            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    imageVector = Icons.Default.FolderOpen,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(18.dp)
                )
                Spacer(Modifier.width(8.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "Lưu vào",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Text(
                        text = folderDisplayName,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurface,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
                TextButton(
                    onClick = onPickSaveDirectory,
                    enabled = !isTranslating
                ) {
                    Text("Đổi", style = MaterialTheme.typography.labelLarge)
                }
            }

            // OCR Toggle Row
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 40.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .clickable(enabled = !isTranslating) { onUseOcrChange(!useOcr) }
            ) {
                Checkbox(
                    checked = useOcr,
                    onCheckedChange = { if (!isTranslating) onUseOcrChange(it) },
                    enabled = !isTranslating
                )
                Text(
                    text = "Tự động OCR trang PDF dạng ảnh (ML Kit)",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurface
                )
            }

            // Overwrite Checkbox
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 40.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .clickable(enabled = !isTranslating) { onOverwriteChange(!overwrite) }
            ) {
                Checkbox(
                    checked = overwrite,
                    onCheckedChange = { if (!isTranslating) onOverwriteChange(it) },
                    enabled = !isTranslating
                )
                Text(
                    text = "Ghi đè file đã dịch trước đó",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurface
                )
            }

            Spacer(Modifier.height(4.dp))

            // A long run is the normal case, so the same button has to be the
            // way out of it; a disabled "Đang dịch…" left no way to stop.
            Button(
                onClick = { if (isTranslating) onCancelTranslation() else onStartTranslation() },
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (isTranslating) {
                        MaterialTheme.colorScheme.error
                    } else {
                        MaterialTheme.colorScheme.primary
                    }
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 52.dp)
            ) {
                Text(
                    text = if (isTranslating) "Huỷ dịch" else "Bắt đầu dịch",
                    style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.Bold)
                )
            }
        }
    }
}
