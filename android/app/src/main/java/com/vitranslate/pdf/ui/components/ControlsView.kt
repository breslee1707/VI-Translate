package com.vitranslate.pdf.ui.components

import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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
    overwrite: Boolean,
    onOverwriteChange: (Boolean) -> Unit,
    customSaveDirectory: String?,
    onPickSaveDirectory: () -> Unit,
    isTranslating: Boolean,
    onStartTranslation: () -> Unit
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

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp, vertical = 12.dp),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "Dịch sang",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.padding(bottom = 6.dp)
                    )

                    ExposedDropdownMenuBox(
                        expanded = expanded,
                        onExpandedChange = { if (!isTranslating) expanded = !expanded }
                    ) {
                        OutlinedTextField(
                            value = selectedLanguage.name,
                            onValueChange = {},
                            readOnly = true,
                            enabled = !isTranslating,
                            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
                            modifier = Modifier
                                .menuAnchor()
                                .fillMaxWidth(0.9f),
                            shape = RoundedCornerShape(8.dp)
                        )

                        ExposedDropdownMenu(
                            expanded = expanded,
                            onDismissRequest = { expanded = false }
                        ) {
                            TargetLanguage.SUPPORTED_LANGUAGES.forEach { language ->
                                DropdownMenuItem(
                                    text = { Text(language.name) },
                                    onClick = {
                                        onLanguageSelected(language)
                                        expanded = false
                                    }
                                )
                            }
                        }
                    }
                }

                Button(
                    onClick = onStartTranslation,
                    enabled = !isTranslating,
                    shape = RoundedCornerShape(8.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primary),
                    modifier = Modifier
                        .height(48.dp)
                        .padding(start = 8.dp)
                ) {
                    Text(
                        text = if (isTranslating) "Đang dịch…" else "Dịch",
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Bold)
                    )
                }
            }

            Spacer(modifier = Modifier.height(10.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "Thư mục lưu file:",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.7f)
                    )
                    Text(
                        text = folderDisplayName,
                        style = MaterialTheme.typography.bodySmall,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }

                OutlinedButton(
                    onClick = onPickSaveDirectory,
                    enabled = !isTranslating,
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier.padding(start = 8.dp)
                ) {
                    Text("Chọn thư mục", style = MaterialTheme.typography.bodySmall)
                }
            }

            Spacer(modifier = Modifier.height(6.dp))

            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .fillMaxWidth()
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
        }
    }
}
