package com.vitranslate.pdf.ui.components

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PictureAsPdf
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vitranslate.pdf.model.UpdateInfo

@Composable
fun HeaderView(
    appVersion: String = "1.9.11",
    updateInfo: UpdateInfo? = null
) {
    val context = LocalContext.current

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp, vertical = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                imageVector = Icons.Default.PictureAsPdf,
                contentDescription = "PDF Translate Icon",
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier
                    .size(38.dp)
                    .padding(end = 10.dp)
            )
            Column {
                Text(
                    text = "PDF Translate",
                    style = MaterialTheme.typography.titleLarge
                )
                Text(
                    text = "Dịch PDF, giữ nguyên bố cục",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                )
            }
        }

        Column(horizontalAlignment = Alignment.End) {
            if (updateInfo != null && updateInfo.isNewerAvailable) {
                Text(
                    text = "● Có bản mới ${updateInfo.latestVersion}",
                    color = MaterialTheme.colorScheme.primary,
                    style = MaterialTheme.typography.bodyMedium.copy(
                        fontWeight = FontWeight.Bold,
                        fontSize = 12.sp
                    ),
                    modifier = Modifier.clickable {
                        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(updateInfo.releaseUrl))
                        context.startActivity(intent)
                    }
                )
            }
            Text(
                text = "v$appVersion",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
            )
        }
    }
}
