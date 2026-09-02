package com.vitranslate.pdf.model

data class UpdateInfo(
    val latestVersion: String,
    val releaseUrl: String,
    val isNewerAvailable: Boolean
)
