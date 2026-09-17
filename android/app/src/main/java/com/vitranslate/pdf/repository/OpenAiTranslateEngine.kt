package com.vitranslate.pdf.repository

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class OpenAiTranslateEngine(
    private val apiKey: String,
    private val baseUrl: String = "https://api.openai.com/v1",
    private val modelName: String = "gpt-4o-mini",
    private val targetLang: String = "vi"
) : TranslateEngine {

    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    override fun translate(text: String): String {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return text

        val endpoint = "${baseUrl.trimEnd('/')}/chat/completions"

        val systemPrompt = "You are a professional document translator. Translate the text into $targetLang.\n" +
                "CRITICAL INSTRUCTIONS:\n" +
                "1. Preserve ALL structural tags like <b0></b0>, <b1></b1>, <s1></s1>, <s2></s2> in their exact positions.\n" +
                "2. Do NOT translate or alter tag IDs, formula placeholders, or math expressions.\n" +
                "3. Output ONLY the raw translated text without markdown formatting or extra text."

        val messages = JSONArray().apply {
            put(JSONObject().apply {
                put("role", "system")
                put("content", systemPrompt)
            })
            put(JSONObject().apply {
                put("role", "user")
                put("content", trimmed)
            })
        }

        val payload = JSONObject().apply {
            put("model", modelName)
            put("messages", messages)
            put("temperature", 0.1)
        }

        val requestBody = payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType())
        val requestBuilder = Request.Builder()
            .url(endpoint)
            .post(requestBody)

        if (apiKey.isNotBlank()) {
            requestBuilder.header("Authorization", "Bearer $apiKey")
        }

        val response = client.newCall(requestBuilder.build()).execute()
        if (!response.isSuccessful) {
            val errorBody = response.body?.string() ?: ""
            throw RuntimeException("LLM API returned HTTP ${response.code}: $errorBody")
        }

        val responseBody = response.body?.string() ?: throw RuntimeException("Empty response from LLM API")
        val jsonResponse = JSONObject(responseBody)
        val choices = jsonResponse.optJSONArray("choices")
            ?: throw RuntimeException("Invalid response format: missing choices")

        if (choices.length() == 0) throw RuntimeException("Empty choices array from LLM API")

        val messageObj = choices.getJSONObject(0).optJSONObject("message")
            ?: throw RuntimeException("Invalid response format: missing message")

        val content = messageObj.optString("content", "").trim()
        return FormulaPlaceholder.removeControlCharacters(content)
    }
}
