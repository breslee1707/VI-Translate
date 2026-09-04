package com.vitranslate.pdf.repository

import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.net.URLDecoder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.regex.Pattern

class GoogleTranslateEngine(
    private val sourceLang: String = "auto",
    private val targetLang: String = "vi"
) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private val cache = ConcurrentHashMap<String, String>()
    private val userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    private val resultPattern = Pattern.compile("(?s)class=\"(?:t0|result-container)\">(.*?)<")

    @Throws(IOException::class, FormulaPlaceholderException::class)
    fun translate(rawText: String, ignoreCache: Boolean = false): String {
        if (rawText.isBlank()) return rawText
        // Normalise before the cache lookup so one spelling of a segment is
        // never served from a key written under another.
        val text = SourceTextNormaliser.normalise(rawText)
        if (!ignoreCache && cache.containsKey(text)) {
            return cache[text]!!
        }

        val encodedText = FormulaPlaceholder.encodeFormulaPlaceholders(text)
        val rawTranslation = fetchGoogleTranslation(encodedText)
        val restoredText = FormulaPlaceholder.restoreFormulaPlaceholders(text, rawTranslation)

        if (!ignoreCache) {
            cache[text] = restoredText
        }
        return restoredText
    }

    private fun fetchGoogleTranslation(query: String): String {
        val url = "https://translate.google.com/m".toHttpUrl().newBuilder()
            .addQueryParameter("sl", sourceLang)
            .addQueryParameter("tl", targetLang)
            .addQueryParameter("q", query.take(5000))
            .build()

        val request = Request.Builder()
            .url(url)
            .header("User-Agent", userAgent)
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw IOException("Google Translate error HTTP ${response.code}: ${response.message}")
            }
            val body = response.body?.string() ?: throw IOException("Empty response from Google Translate")
            val matcher = resultPattern.matcher(body)
            if (!matcher.find()) {
                throw IOException("Google Translate response did not contain a translation result")
            }
            val matchGroup = matcher.group(1) ?: ""
            val unescaped = unescapeHtml(matchGroup)
            return FormulaPlaceholder.removeControlCharacters(unescaped)
        }
    }

    private fun unescapeHtml(html: String): String {
        return html
            .replace("&quot;", "\"")
            .replace("&apos;", "'")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&#39;", "'")
    }
}
