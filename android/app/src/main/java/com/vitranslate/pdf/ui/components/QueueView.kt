package com.vitranslate.pdf.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.HourglassEmpty
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.WarningAmber
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.vitranslate.pdf.model.QueueItem
import com.vitranslate.pdf.model.TranslationStatus
import com.vitranslate.pdf.ui.theme.*

/**
 * Status colours per theme.
 *
 * Every row used the Light variants unconditionally, so in dark mode a finished
 * file was drawn in #1A7F37 — a dark green on a near-black surface, effectively
 * invisible. The dark values existed in Color.kt all along and were never read.
 */
@Composable
private fun statusColor(status: TranslationStatus): Color {
    val dark = isSystemInDarkTheme()
    return when (status) {
        TranslationStatus.QUEUED, TranslationStatus.SKIPPED ->
            if (dark) StatusQueuedDark else StatusQueuedLight
        TranslationStatus.RUNNING -> if (dark) StatusRunningDark else StatusRunningLight
        TranslationStatus.DONE -> if (dark) StatusDoneDark else StatusDoneLight
        TranslationStatus.PARTIAL -> if (dark) StatusPartialDark else StatusPartialLight
        TranslationStatus.FAILED -> if (dark) StatusFailedDark else StatusFailedLight
    }
}

private fun statusIcon(status: TranslationStatus): ImageVector = when (status) {
    TranslationStatus.QUEUED -> Icons.Default.Schedule
    TranslationStatus.RUNNING -> Icons.Default.Sync
    TranslationStatus.DONE -> Icons.Default.Check
    TranslationStatus.PARTIAL -> Icons.Default.WarningAmber
    TranslationStatus.FAILED -> Icons.Default.ErrorOutline
    TranslationStatus.SKIPPED -> Icons.Default.HourglassEmpty
}

@Composable
fun QueueView(
    items: List<QueueItem>,
    isTranslating: Boolean,
    onRemoveItem: (String) -> Unit,
    onClearQueue: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(modifier = modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = if (items.isNotEmpty()) "Hàng đợi (${items.size})" else "Hàng đợi",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onBackground,
                modifier = Modifier.weight(1f)
            )
            if (items.isNotEmpty() && !isTranslating) {
                TextButton(onClick = onClearQueue) {
                    Text(
                        text = "Xoá tất cả",
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }

        if (items.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .padding(24.dp),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = "Chưa có file nào trong hàng đợi.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center
                )
            }
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
                contentPadding = PaddingValues(horizontal = 16.dp, vertical = 4.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                items(items, key = { it.id }) { item ->
                    QueueRow(item = item, isTranslating = isTranslating, onRemoveItem = onRemoveItem)
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
    val tint = statusColor(item.status)
    val canOpen = translatedPdfExists(context, item.outputPath)

    Surface(
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceContainerLow,
        modifier = Modifier.fillMaxWidth()
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 60.dp)
                .clickable(enabled = canOpen) {
                    item.outputPath?.let { openTranslatedPdf(context, it) }
                }
                .padding(start = 12.dp, end = 4.dp, top = 8.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // A tinted disc rather than a bare •/▶/✓ character: the glyphs the
            // status enum carries are not all present in every system font.
            Box(
                modifier = Modifier
                    .size(32.dp)
                    .clip(CircleShape)
                    .background(tint.copy(alpha = 0.14f)),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = statusIcon(item.status),
                    contentDescription = null,
                    tint = tint,
                    modifier = Modifier.size(18.dp)
                )
            }
            Spacer(Modifier.width(12.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = item.name,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                if (item.detail.isNotEmpty()) {
                    Text(
                        text = item.detail,
                        style = MaterialTheme.typography.bodySmall,
                        color = tint,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }

            // Both of these were well under the 48dp minimum target; the remove
            // button was a 24dp box holding a ✕ character.
            if (canOpen) {
                IconButton(onClick = { item.outputPath?.let { openTranslatedPdf(context, it) } }) {
                    Icon(
                        imageVector = Icons.AutoMirrored.Filled.OpenInNew,
                        contentDescription = "Mở ${item.name}",
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(20.dp)
                    )
                }
            } else if (!isTranslating) {
                IconButton(onClick = { onRemoveItem(item.id) }) {
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = "Bỏ ${item.name} khỏi hàng đợi",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(18.dp)
                    )
                }
            }
        }
    }
}
