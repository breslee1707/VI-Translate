package com.vitranslate.pdf.repository

fun interface TranslateEngine {
    fun translate(text: String): String
}
