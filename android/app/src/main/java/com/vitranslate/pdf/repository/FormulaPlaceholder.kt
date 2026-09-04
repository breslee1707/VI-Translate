package com.vitranslate.pdf.repository

import java.util.regex.Pattern

class FormulaPlaceholderException(message: String) : Exception(message)

object FormulaPlaceholder {
    private val PLACEHOLDER_PATTERN = Pattern.compile("</?b\\d+>")
    private val INTERNAL_PLACEHOLDER_PATTERN = Pattern.compile("\\{\\s*v(\\d+)\\s*\\}", Pattern.CASE_INSENSITIVE)
    private val PAIRED_PLACEHOLDER_PATTERN = Pattern.compile("<b(\\d+)></b\\1>")
    private val STYLE_TAG_PATTERN = Pattern.compile("<(/?)s([123])>", Pattern.CASE_INSENSITIVE)

    /**
     * Remove control characters that cannot be emitted safely into PDF text.
     */
    fun removeControlCharacters(value: String): String {
        val sb = StringBuilder()
        for (ch in value) {
            val type = Character.getType(ch)
            if (type != Character.CONTROL.toInt() &&
                type != Character.FORMAT.toInt() &&
                type != Character.PRIVATE_USE.toInt() &&
                type != Character.SURROGATE.toInt() &&
                type != Character.UNASSIGNED.toInt()
            ) {
                sb.append(ch)
            }
        }
        return sb.toString()
    }

    private val EXPONENT_PATTERN = Pattern.compile("(?:[A-Za-z0-9_()\\[\\]{}]+(?:\\^\\{?[A-Za-z0-9_+\\-()]+\\}?|[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+))")

    /**
     * Convert converter-internal `{vN}` markers and exponent expressions into translator-safe tag pairs `<bN></bN>`.
     */
    fun encodeFormulaPlaceholders(text: String): String {
        var result = text
        val matcher = INTERNAL_PLACEHOLDER_PATTERN.matcher(result)
        val sb = StringBuffer()
        while (matcher.find()) {
            val num = matcher.group(1)?.replace(" ", "") ?: "0"
            matcher.appendReplacement(sb, "<b$num></b$num>")
        }
        matcher.appendTail(sb)
        result = sb.toString()

        val expMatcher = EXPONENT_PATTERN.matcher(result)
        val sbExp = StringBuffer()
        var expCounter = 9000
        while (expMatcher.find()) {
            val matched = expMatcher.group()
            expMatcher.appendReplacement(sbExp, "<b$expCounter>$matched</b$expCounter>")
            expCounter++
        }
        expMatcher.appendTail(sbExp)
        return sbExp.toString()
    }

    /**
     * Extract formula placeholder tags in order, e.g. ["<b0>", "</b0>"].
     */
    fun getPlaceholders(text: String): List<String> {
        val list = mutableListOf<String>()
        val matcher = PLACEHOLDER_PATTERN.matcher(text)
        while (matcher.find()) {
            list.add(matcher.group())
        }
        return list
    }

    /**
     * Validate style tag balance in text.
     */
    fun validateStyleTags(source: String, translated: String) {
        if (getStyleTagCounts(source) != getStyleTagCounts(translated)) {
            throw FormulaPlaceholderException("style tags changed during translation")
        }
    }

    private fun getStyleTagCounts(text: String): Map<String, Int> {
        val stack = mutableListOf<String>()
        val counts = mutableMapOf<String, Int>()
        val matcher = STYLE_TAG_PATTERN.matcher(text)
        while (matcher.find()) {
            val closing = matcher.group(1)?.isNotEmpty() == true
            val id = matcher.group(2) ?: ""
            if (!closing) {
                stack.add(id)
            } else {
                if (stack.isEmpty() || stack.last() != id) {
                    throw FormulaPlaceholderException("style tags are malformed or cross-nested")
                }
                stack.removeAt(stack.size - 1)
                counts[id] = (counts[id] ?: 0) + 1
            }
        }
        if (stack.isNotEmpty()) {
            throw FormulaPlaceholderException("style tags are not closed")
        }
        return counts
    }

    /**
     * Validate translator output and restore tags to converter markers `{vN}`.
     */
    fun restoreFormulaPlaceholders(source: String, translated: String): String {
        val cleanSource = source.replace(Regex("</?b9\\d{3}>"), "")
        var cleanTranslated = translated.replace(Regex("</?b9\\d{3}>"), "")

        val encodedSource = encodeFormulaPlaceholders(cleanSource)
        val sourcePlaceholders = getPlaceholders(encodedSource).filterNot { it.matches(Regex("</?b9\\d{3}>")) }
        val translatedPlaceholders = getPlaceholders(cleanTranslated).filterNot { it.matches(Regex("</?b9\\d{3}>")) }
        if (sourcePlaceholders != translatedPlaceholders) {
            throw FormulaPlaceholderException("Formula placeholders were altered during translation")
        }

        val matcher = PAIRED_PLACEHOLDER_PATTERN.matcher(cleanTranslated)
        val sb = StringBuffer()
        while (matcher.find()) {
            val num = matcher.group(1) ?: "0"
            matcher.appendReplacement(sb, "{v$num}")
        }
        matcher.appendTail(sb)
        return sb.toString()
    }
}
