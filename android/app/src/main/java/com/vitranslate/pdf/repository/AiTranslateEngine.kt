package com.vitranslate.pdf.repository

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

enum class AiProvider {
    OPENAI,
    DEEPSEEK,
    GEMINI,
    OPENROUTER,
    GROQ,
    SILICONFLOW,
    CUSTOM_OPENAI
}

class AiTranslateEngine(
    val provider: AiProvider = AiProvider.OPENAI,
    private val apiKey: String = "",
    val modelName: String = "gpt-4o-mini",
    val customEndpoint: String = "",
    val targetLang: String = "vi"
) : TranslationEngine {

    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    private val cache = ConcurrentHashMap<String, String>()

    @Throws(IOException::class, FormulaPlaceholderException::class)
    override fun translate(rawText: String): String {
        if (rawText.isBlank()) return rawText

        val text = SourceTextNormaliser.normalise(rawText)
        if (cache.containsKey(text)) return cache[text]!!

        val encodedText = FormulaPlaceholder.encodeFormulaPlaceholders(text)
        val rawTranslation = when (provider) {
            AiProvider.GEMINI -> fetchGeminiTranslation(encodedText)
            else -> fetchOpenAiCompatibleTranslation(encodedText)
        }

        val restoredText = FormulaPlaceholder.restoreFormulaPlaceholders(text, rawTranslation)
        cache[text] = restoredText
        return restoredText
    }

    private fun systemPrompt(): String {
        val langName = if (targetLang.equals("vi", ignoreCase = true)) "Vietnamese" else targetLang
        return "You are a professional document translator. Translate into $langName.\n" +
                "CRITICAL INSTRUCTIONS:\n" +
                "1. Preserve ALL tags like <b0></b0>, <b1></b1>, <s1></s1> in their exact position.\n" +
                "2. Do NOT translate or alter tag IDs.\n" +
                "3. Output ONLY the raw translated text with tags intact. Do NOT add Markdown code blocks or explanation."
    }

    private fun resolveEndpoint(): String {
        if (customEndpoint.isNotBlank()) return customEndpoint
        return when (provider) {
            AiProvider.OPENAI -> "https://api.openai.com/v1/chat/completions"
            AiProvider.DEEPSEEK -> "https://api.deepseek.com/v1/chat/completions"
            AiProvider.OPENROUTER -> "https://openrouter.ai/api/v1/chat/completions"
            AiProvider.GROQ -> "https://api.groq.com/openai/v1/chat/completions"
            AiProvider.SILICONFLOW -> "https://api.siliconflow.cn/v1/chat/completions"
            AiProvider.CUSTOM_OPENAI -> "http://10.0.2.2:11434/v1/chat/completions"
            else -> "https://api.openai.com/v1/chat/completions"
        }
    }

    private fun fetchOpenAiCompatibleTranslation(query: String): String {
        val endpoint = resolveEndpoint()
        val messages = JSONArray().apply {
            put(JSONObject().apply {
                put("role", "system")
                put("content", systemPrompt())
            })
            put(JSONObject().apply {
                put("role", "user")
                put("content", query)
            })
        }

        val json = JSONObject().apply {
            put("model", modelName)
            put("messages", messages)
            put("temperature", 0.1)
        }

        val requestBuilder = Request.Builder()
            .url(endpoint)
            .post(json.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))

        if (apiKey.isNotBlank()) {
            requestBuilder.header("Authorization", "Bearer $apiKey")
        }

        client.newCall(requestBuilder.build()).execute().use { response ->
            if (!response.isSuccessful) throw IOException("AI API HTTP ${response.code}: ${response.message}")
            val jsonResp = JSONObject(response.body?.string() ?: "")
            val choices = jsonResp.optJSONArray("choices") ?: throw IOException("Invalid AI response")
            if (choices.length() == 0) throw IOException("AI returned no content")
            val content = choices.getJSONObject(0).getJSONObject("message").optString("content", "")

            val usage = jsonResp.optJSONObject("usage")
            val promptTok = usage?.optInt("prompt_tokens") ?: estimateTokenCount(systemPrompt() + query)
            val compTok = usage?.optInt("completion_tokens") ?: estimateTokenCount(content)
            recordTokens(promptTok, compTok)

            return FormulaPlaceholder.removeControlCharacters(content.trim())
        }
    }

    private fun fetchGeminiTranslation(query: String): String {
        val model = if (modelName.isNotBlank()) modelName else "gemini-2.0-flash"
        val url = "https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent?key=$apiKey"

        val contents = JSONArray().apply {
            put(JSONObject().apply {
                put("parts", JSONArray().apply {
                    put(JSONObject().apply {
                        put("text", "${systemPrompt()}\n\n$query")
                    })
                })
            })
        }

        val json = JSONObject().apply { put("contents", contents) }

        val request = Request.Builder()
            .url(url)
            .post(json.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw IOException("Gemini API HTTP ${response.code}: ${response.message}")
            val jsonResp = JSONObject(response.body?.string() ?: "")
            val candidates = jsonResp.optJSONArray("candidates") ?: throw IOException("Invalid Gemini response")
            if (candidates.length() == 0) throw IOException("Gemini returned no candidates")
            val parts = candidates.getJSONObject(0).getJSONObject("content").optJSONArray("parts") ?: throw IOException("Invalid Gemini parts")
            val content = parts.getJSONObject(0).optString("text", "")

            val usage = jsonResp.optJSONObject("usageMetadata")
            val promptTok = usage?.optInt("promptTokenCount") ?: estimateTokenCount(systemPrompt() + query)
            val compTok = usage?.optInt("candidatesTokenCount") ?: estimateTokenCount(content)
            recordTokens(promptTok, compTok)

            return FormulaPlaceholder.removeControlCharacters(content.trim())
        }
    }

    data class ApiHealthResult(
        val latencyMs: Long,
        val promptTokens: Int,
        val completionTokens: Int,
        val totalTokens: Int
    )

    companion object {
        private val _sessionPromptTokens = java.util.concurrent.atomic.AtomicLong(0)
        private val _sessionCompletionTokens = java.util.concurrent.atomic.AtomicLong(0)

        val sessionPromptTokens: Long get() = _sessionPromptTokens.get()
        val sessionCompletionTokens: Long get() = _sessionCompletionTokens.get()
        val sessionTotalTokens: Long get() = sessionPromptTokens + sessionCompletionTokens

        fun resetSessionTokens() {
            _sessionPromptTokens.set(0)
            _sessionCompletionTokens.set(0)
        }

        fun recordTokens(prompt: Int, completion: Int) {
            _sessionPromptTokens.addAndGet(prompt.toLong())
            _sessionCompletionTokens.addAndGet(completion.toLong())
        }

        fun estimateTokenCount(text: String): Int {
            if (text.isEmpty()) return 0
            return kotlin.math.max(1, Math.ceil(text.length / 3.8).toInt())
        }

        suspend fun testConnection(
            provider: AiProvider,
            apiKey: String,
            modelName: String,
            customEndpoint: String
        ): Result<ApiHealthResult> = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            val startTime = System.currentTimeMillis()
            val engine = AiTranslateEngine(
                provider = provider,
                apiKey = apiKey,
                modelName = modelName,
                customEndpoint = customEndpoint,
                targetLang = "vi"
            )
            runCatching {
                val testPrompt = "Hello"
                val res = engine.translate(testPrompt)
                if (res.isBlank()) throw IOException("Phản hồi từ AI rỗng")
                val latency = System.currentTimeMillis() - startTime
                val promptTok = estimateTokenCount(engine.systemPrompt() + testPrompt)
                val compTok = estimateTokenCount(res)
                ApiHealthResult(
                    latencyMs = latency,
                    promptTokens = promptTok,
                    completionTokens = compTok,
                    totalTokens = promptTok + compTok
                )
            }
        }
    }
}
