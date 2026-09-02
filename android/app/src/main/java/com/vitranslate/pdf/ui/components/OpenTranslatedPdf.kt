package com.vitranslate.pdf.ui.components

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import java.io.File

/**
 * Opens a finished translation, wherever it was written.
 *
 * The output path is a plain file path when the app writes to its own storage
 * and a `content://` tree document URI when the user picked a folder through
 * the storage access framework. The two callers each handled only one of those:
 * a document saved to a chosen folder tested `File(path).exists()`, came back
 * false, and could never be opened.
 */
fun translatedPdfExists(context: Context, pathOrUri: String?): Boolean {
    if (pathOrUri.isNullOrBlank()) return false
    return if (pathOrUri.startsWith("content://")) {
        try {
            context.contentResolver.openInputStream(Uri.parse(pathOrUri))?.use { true } ?: false
        } catch (_: Exception) {
            false
        }
    } else {
        File(pathOrUri).exists()
    }
}

fun openTranslatedPdf(context: Context, pathOrUri: String) {
    try {
        // A file:// URI would raise FileUriExposedException on anything since
        // API 24, so a path has to be handed over through the FileProvider.
        val contentUri = if (pathOrUri.startsWith("content://")) {
            Uri.parse(pathOrUri)
        } else {
            val file = File(pathOrUri)
            if (!file.exists()) return
            FileProvider.getUriForFile(context, "${context.packageName}.provider", file)
        }

        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(contentUri, "application/pdf")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        val chooser = Intent.createChooser(intent, "Mở bằng PDF Viewer")
        chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(chooser)
    } catch (_: Exception) {
    }
}
