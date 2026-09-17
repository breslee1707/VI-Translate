package com.vitranslate.pdf.repository

import java.io.IOException

interface TranslationEngine {
    @Throws(IOException::class, FormulaPlaceholderException::class)
    fun translate(text: String): String
}
