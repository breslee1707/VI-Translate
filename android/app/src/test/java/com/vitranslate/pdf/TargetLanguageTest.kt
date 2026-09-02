package com.vitranslate.pdf

import com.vitranslate.pdf.model.TargetLanguage
import org.junit.Assert.*
import org.junit.Test

class TargetLanguageTest {

    @Test
    fun testSupportedLanguagesCount() {
        assertEquals(36, TargetLanguage.SUPPORTED_LANGUAGES.size)
    }

    @Test
    fun testDefaultLanguageIsVietnamese() {
        val defaultLang = TargetLanguage.getByCode(TargetLanguage.DEFAULT_CODE)
        assertEquals("vi", defaultLang.code)
        assertEquals("Tiếng Việt", defaultLang.name)
    }

    @Test
    fun testGetByCodeFallback() {
        val unknown = TargetLanguage.getByCode("unknown_code")
        assertEquals("vi", unknown.code)
    }

    @Test
    fun testGetByName() {
        val english = TargetLanguage.getByName("English")
        assertEquals("en", english.code)
    }
}
