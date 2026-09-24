package com.vitranslate.pdf.repository

import org.junit.Assert.*
import org.junit.Test

class AiTranslateEngineTest {

    @Test
    fun testAiEngineInitializationOpenAI() {
        val engine = AiTranslateEngine(
            provider = AiProvider.OPENAI,
            apiKey = "test-ephemeral-key-123",
            modelName = "gpt-4o-mini",
            targetLang = "vi"
        )

        assertEquals(AiProvider.OPENAI, engine.provider)
        assertEquals("gpt-4o-mini", engine.modelName)
        assertEquals("vi", engine.targetLang)
    }

    @Test
    fun testAiEngineInitializationGemini() {
        val engine = AiTranslateEngine(
            provider = AiProvider.GEMINI,
            apiKey = "test-gemini-key",
            modelName = "gemini-1.5-flash",
            targetLang = "vi"
        )

        assertEquals(AiProvider.GEMINI, engine.provider)
        assertEquals("gemini-1.5-flash", engine.modelName)
    }

    @Test
    fun testAiEngineCustomEndpoint() {
        val customUrl = "http://localhost:11434/v1/chat/completions"
        val engine = AiTranslateEngine(
            provider = AiProvider.CUSTOM_OPENAI,
            apiKey = "ollama-local",
            modelName = "qwen2.5",
            customEndpoint = customUrl
        )

        assertEquals(AiProvider.CUSTOM_OPENAI, engine.provider)
        assertEquals(customUrl, engine.customEndpoint)
        assertEquals("qwen2.5", engine.modelName)
    }

    @Test
    fun testTokenEstimatorAndSessionCounters() {
        AiTranslateEngine.resetSessionTokens()
        assertEquals(0L, AiTranslateEngine.sessionTotalTokens)

        val sampleText = "Hello world this is a test for token estimation."
        val estimated = AiTranslateEngine.estimateTokenCount(sampleText)
        assertTrue(estimated > 5)

        AiTranslateEngine.recordTokens(prompt = 20, completion = 10)
        assertEquals(20L, AiTranslateEngine.sessionPromptTokens)
        assertEquals(10L, AiTranslateEngine.sessionCompletionTokens)
        assertEquals(30L, AiTranslateEngine.sessionTotalTokens)

        AiTranslateEngine.resetSessionTokens()
        assertEquals(0L, AiTranslateEngine.sessionTotalTokens)
    }
}
