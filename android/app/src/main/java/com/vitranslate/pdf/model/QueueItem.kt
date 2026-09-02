package com.vitranslate.pdf.model

import android.net.Uri

enum class TranslationStatus(val mark: String) {
    QUEUED("•"),
    RUNNING("▶"),
    DONE("✓"),
    PARTIAL("!"),
    FAILED("✕"),
    SKIPPED("–")
}

data class QueueItem(
    val id: String,
    val uri: Uri,
    val name: String,
    val status: TranslationStatus = TranslationStatus.QUEUED,
    val detail: String = "",
    val untranslated: Int = 0,
    val outputPath: String? = null
)
