package com.vitranslate.pdf.ui.components

import android.content.Context
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.vitranslate.pdf.model.QueueItem
import com.vitranslate.pdf.model.TranslationStatus
import com.vitranslate.pdf.ui.theme.*

@Composable
fun QueueView(
    items: List<QueueItem>,
    isTranslating: Boolean,
    onRemoveItem: (String) -> Unit,
    onClearQueue: () -> Unit,
    modifier: Modifier = Modifier
) {
    val count = items.size

    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Text(
                text = if (count > 0) "Hàng đợi ($count)" else "Hàng đợi",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold
            )

            if (count > 0 && !isTranslating) {
                TextButton(onClick = onClearQueue) {
                    Text(
                        text = "Xoá tất cả",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                    )
                }
            }
        }

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
            shape = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            if (items.isEmpty()) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = "Chưa có file nào.\nKéo thả PDF hoặc chọn file phía trên để bắt đầu.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f),
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center
                    )
                }
            } else {
                LazyColumn(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(8.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp)
                ) {
                    items(items, key = { it.id }) { item ->
                        QueueRow(item = item, isTranslating = isTranslating, onRemoveItem = onRemoveItem)
                    }
                }
            }
        }
    }
}

@Composable
private fun QueueRow(
    item: QueueItem,
    isTranslating: Boolean,
    onRemoveItem: (String) -> Unit
) {
    val context = LocalContext.current
    val statusColor = when (item.status) {
        TranslationStatus.QUEUED -> StatusQueuedLight
        TranslationStatus.RUNNING -> StatusRunningLight
        TranslationStatus.DONE -> StatusDoneLight
        TranslationStatus.PARTIAL -> StatusPartialLight
        TranslationStatus.FAILED -> StatusFailedLight
        TranslationStatus.SKIPPED -> StatusQueuedLight
    }

    val isClickable = translatedPdfExists(context, item.outputPath)

    Surface(
        shape = RoundedCornerShape(8.dp),
        color = Color.Transparent,
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = isClickable) {
                item.outputPath?.let { path -> openTranslatedPdf(context, path) }
            }
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = item.status.mark,
                color = statusColor,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(end = 8.dp)
            )

            Column(
                modifier = Modifier.weight(1f)
            ) {
                Text(
                    text = item.name,
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (item.status == TranslationStatus.QUEUED) MaterialTheme.colorScheme.onSurface.copy(alpha = 0.8f) else statusColor,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )

                if (item.detail.isNotEmpty()) {
                    Text(
                        text = item.detail,
                        style = MaterialTheme.typography.bodySmall,
                        color = statusColor.copy(alpha = 0.85f),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }

            if (isClickable) {
                TextButton(
                    onClick = { item.outputPath?.let { path -> openTranslatedPdf(context, path) } },
                    modifier = Modifier.height(32.dp)
                ) {
                    Text(
                        text = "Mở PDF",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.primary
                    )
                }
            } else if (item.status == TranslationStatus.QUEUED && !isTranslating) {
                IconButton(
                    onClick = { onRemoveItem(item.id) },
                    modifier = Modifier.size(24.dp)
                ) {
                    Text(
                        text = "✕",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                    )
                }
            }
        }
    }
}
