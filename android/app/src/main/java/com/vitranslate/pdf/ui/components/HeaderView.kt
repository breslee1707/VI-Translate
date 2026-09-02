package com.vitranslate.pdf.ui.components

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.vitranslate.pdf.R
import com.vitranslate.pdf.model.UpdateInfo

@Composable
fun HeaderView(
    appVersion: String,
    updateInfo: UpdateInfo? = null,
    onShowLog: (() -> Unit)? = null,
    onShowAbout: (() -> Unit)? = null
) {
    val context = LocalContext.current

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .statusBarsPadding()
            .padding(start = 16.dp, end = 8.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        // The app's own mark rather than a generic PDF glyph, so the header and
        // the launcher icon are recognisably the same product.
        Image(
            painter = painterResource(R.drawable.ic_app_mark),
            contentDescription = null,
            modifier = Modifier
                .size(36.dp)
                .clip(RoundedCornerShape(8.dp))
        )
        Spacer(modifier = Modifier.width(12.dp))

        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = "PDF Translate",
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.onBackground,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            if (updateInfo != null && updateInfo.isNewerAvailable) {
                Text(
                    text = "Có bản mới ${updateInfo.latestVersion}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clickable {
                        runCatching {
                            context.startActivity(
                                Intent(Intent.ACTION_VIEW, Uri.parse(updateInfo.releaseUrl))
                            )
                        }
                    }
                )
            } else {
                Text(
                    text = "Dịch PDF, giữ nguyên bố cục · v$appVersion",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
            }
        }

        // IconButton was constrained to 36dp, below the 48dp minimum touch
        // target. The icon inside stays small; only the tappable area grows.
        if (onShowLog != null) {
            IconButton(onClick = onShowLog) {
                Icon(
                    imageVector = Icons.Default.Description,
                    contentDescription = "Xem nhật ký",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(22.dp)
                )
            }
        }
        if (onShowAbout != null) {
            IconButton(onClick = onShowAbout) {
                Icon(
                    imageVector = Icons.Default.Info,
                    contentDescription = "Thông tin ứng dụng",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(22.dp)
                )
            }
        }
    }
}
