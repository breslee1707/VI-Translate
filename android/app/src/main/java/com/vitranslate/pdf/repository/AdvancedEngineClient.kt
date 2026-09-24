package com.vitranslate.pdf.repository

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class AdvancedEngineClient(
    private var serverUrl: String = "http://localhost:8000"
) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    fun updateServerUrl(url: String) {
        serverUrl = url.trimEnd('/')
    }

    fun isAdvancedEngineAvailable(): Boolean {
        return try {
            val request = Request.Builder()
                .url("$serverUrl/health")
                .get()
                .build()
            client.newCall(request).execute().use { it.isSuccessful }
        } catch (_: Exception) {
            false
        }
    }

    fun translateSegment(text: String, sourceLang: String = "auto", targetLang: String = "vi"): Result<String> = runCatching {
        val json = JSONObject().apply {
            put("text", text)
            put("source_lang", sourceLang)
            put("target_lang", targetLang)
        }
        val body = json.toString().toRequestBody("application/json; charset=utf-8".toMediaType())
        val request = Request.Builder()
            .url("$serverUrl/translate")
            .post(body)
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw IllegalStateException("HTTP ${response.code}")
            val responseJson = JSONObject(response.body?.string() ?: "")
            responseJson.optString("translated", text)
        }
    }
}
