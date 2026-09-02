package com.vitranslate.pdf.repository

import com.vitranslate.pdf.model.UpdateInfo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class UpdateChecker(private val currentVersion: String = "1.9.11") {

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    private val releasesUrl = "https://api.github.com/repos/breslee1707/VI-Translate/releases/latest"
    private val webReleaseUrl = "https://github.com/breslee1707/VI-Translate/releases/latest"

    suspend fun checkForUpdate(): UpdateInfo? = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url(releasesUrl)
                .header("User-Agent", "PDFTranslate-Android/$currentVersion")
                .build()

            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@withContext null
                val body = response.body?.string() ?: return@withContext null
                val json = JSONObject(body)
                val tagName = json.optString("tag_name", "").trim()
                if (tagName.isEmpty()) return@withContext null

                val latestVer = tagName.removePrefix("v").removePrefix("V")
                val isNewer = isNewer(latestVer, currentVersion)

                UpdateInfo(
                    latestVersion = tagName,
                    releaseUrl = webReleaseUrl,
                    isNewerAvailable = isNewer
                )
            }
        } catch (_: Exception) {
            null
        }
    }

    companion object {
        fun versionParts(version: String): List<Int> {
            val clean = version.trim().removePrefix("v").removePrefix("V")
            return clean.split(".").mapNotNull { part ->
                part.takeWhile { it.isDigit() }.toIntOrNull()
            }
        }

        fun isNewer(latest: String, current: String): Boolean {
            val latestParts = versionParts(latest)
            val currentParts = versionParts(current)
            val maxSize = maxOf(latestParts.size, currentParts.size)

            for (i in 0 until maxSize) {
                val l = latestParts.getOrElse(i) { 0 }
                val c = currentParts.getOrElse(i) { 0 }
                if (l > c) return true
                if (l < c) return false
            }
            return false
        }
    }
}
