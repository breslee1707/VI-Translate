package com.vitranslate.pdf.repository

import com.vitranslate.pdf.BuildConfig
import com.vitranslate.pdf.model.UpdateInfo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import java.util.concurrent.TimeUnit

/**
 * Reads the newest Android release from GitHub.
 *
 * The repository publishes two products from one tag list. Desktop builds own
 * `v<APP_VERSION>` and their in-app updater treats that namespace as a
 * contract, so the Android build tags itself `android-v<versionName>` and this
 * checker only looks at that prefix. Asking for `/releases/latest` would return
 * whichever product released last, which is why the check never fired before.
 */
class UpdateChecker(private val currentVersion: String = BuildConfig.VERSION_NAME) {

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    private val releasesUrl = "https://api.github.com/repos/breslee1707/VI-Translate/releases?per_page=30"

    suspend fun checkForUpdate(): UpdateInfo? = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url(releasesUrl)
                .header("Accept", "application/vnd.github+json")
                .header("User-Agent", "PDFTranslate-Android/$currentVersion")
                .build()

            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@withContext null
                val body = response.body?.string() ?: return@withContext null
                val releases = JSONArray(body)

                for (index in 0 until releases.length()) {
                    val release = releases.optJSONObject(index) ?: continue
                    if (release.optBoolean("draft", false)) continue
                    if (release.optBoolean("prerelease", false)) continue

                    val tagName = release.optString("tag_name", "").trim()
                    if (!tagName.startsWith(ANDROID_TAG_PREFIX)) continue

                    // The list arrives newest first, so the first Android tag is
                    // the one to compare against; older ones cannot win anyway.
                    val webUrl = release.optString("html_url", "").trim()
                        .ifEmpty { FALLBACK_RELEASES_URL }

                    return@withContext UpdateInfo(
                        latestVersion = tagName,
                        releaseUrl = webUrl,
                        isNewerAvailable = isNewer(tagName, currentVersion)
                    )
                }
                null
            }
        } catch (_: Exception) {
            null
        }
    }

    companion object {
        const val ANDROID_TAG_PREFIX = "android-v"

        private const val FALLBACK_RELEASES_URL =
            "https://github.com/breslee1707/VI-Translate/releases"

        fun versionParts(version: String): List<Int> {
            val clean = version.trim()
                .removePrefix(ANDROID_TAG_PREFIX)
                .removePrefix("v")
                .removePrefix("V")
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
