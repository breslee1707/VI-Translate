package com.vitranslate.pdf.repository

data class TranslationPlan(
    val selectedPages: List<Int>,
    val skippedPages: List<Int>,
    val totalPages: Int,
    val validationError: String? = null
)

object PageSelectionParser {

    /**
     * Parses omit string (e.g. "2,3", "2-3", "2,5,7-9") into a set of 1-based page numbers.
     */
    fun parseOmitList(omitInput: String, totalPages: Int): Set<Int> {
        if (omitInput.isBlank()) return emptySet()
        val resultSet = mutableSetOf<Int>()
        val parts = omitInput.trim().split(",")

        for (part in parts) {
            val token = part.trim()
            if (token.isBlank()) continue

            if (token.contains("-")) {
                val bounds = token.split("-")
                if (bounds.size == 2) {
                    val start = bounds[0].trim().toIntOrNull()
                    val end = bounds[1].trim().toIntOrNull()
                    if (start != null && end != null && start > 0 && end > 0) {
                        val from = minOf(start, end).coerceIn(1, totalPages)
                        val to = maxOf(start, end).coerceIn(1, totalPages)
                        for (p in from..to) {
                            resultSet.add(p)
                        }
                    }
                }
            } else {
                val pageNum = token.toIntOrNull()
                if (pageNum != null && pageNum in 1..totalPages) {
                    resultSet.add(pageNum)
                }
            }
        }
        return resultSet
    }

    /**
     * Excel-style page selection:
     * Translate from [fromPage] to [toPage], omitting pages in [omitInput] (e.g. "2,3").
     */
    fun parseExcelPageSelection(
        fromPage: Int,
        toPage: Int,
        omitInput: String,
        totalPages: Int
    ): TranslationPlan {
        if (totalPages <= 0) {
            return TranslationPlan(emptyList(), emptyList(), 0, "PDF không có trang nào")
        }

        val validFrom = fromPage.coerceIn(1, totalPages)
        val validTo = toPage.coerceIn(1, totalPages)

        if (validFrom > validTo) {
            return TranslationPlan(
                selectedPages = (1..totalPages).toList(),
                skippedPages = emptyList(),
                totalPages = totalPages,
                validationError = "Trang bắt đầu ($validFrom) lớn hơn trang kết thúc ($validTo)"
            )
        }

        val rangeSet = (validFrom..validTo).toSet()
        val omitSet = parseOmitList(omitInput, totalPages)
        val selectedSet = rangeSet - omitSet
        val selectedPages = selectedSet.sorted()

        val allPages = (1..totalPages).toSet()
        val skippedPages = (allPages - selectedSet).sorted()

        return TranslationPlan(
            selectedPages = selectedPages,
            skippedPages = skippedPages,
            totalPages = totalPages
        )
    }

    /**
     * Legacy & flexible string parser supporting:
     * - "all"
     * - "1-5"
     * - "1,3,5"
     * - "Từ 1 đến 5 (bỏ 2,3)"
     */
    fun parsePageSelection(rawInput: String, totalPages: Int): List<Int> {
        if (totalPages <= 0) return emptyList()
        val trimmed = rawInput.trim()
        if (trimmed.isBlank() || trimmed.equals("all", ignoreCase = true)) {
            return (1..totalPages).toList()
        }

        // Check if string matches "Từ X đến Y (bỏ Z)"
        val tuDenRegex = Regex("(?i)T[ừu]\\s*(\\d+)\\s*đ[ếe]n\\s*(\\d+)(?:\\s*\\(b[ỏo]\\s*(.*?)\\)?)?")
        val match = tuDenRegex.find(trimmed)
        if (match != null) {
            val from = match.groupValues[1].toIntOrNull() ?: 1
            val to = match.groupValues[2].toIntOrNull() ?: totalPages
            val omit = match.groupValues.getOrNull(3) ?: ""
            return parseExcelPageSelection(from, to, omit, totalPages).selectedPages
        }

        val resultSet = mutableSetOf<Int>()
        val parts = trimmed.split(",")

        for (part in parts) {
            val token = part.trim()
            if (token.isBlank()) continue

            if (token.contains("-")) {
                val bounds = token.split("-")
                if (bounds.size == 2) {
                    val start = bounds[0].trim().toIntOrNull()
                    val end = bounds[1].trim().toIntOrNull()
                    if (start != null && end != null && start > 0 && end > 0) {
                        val from = minOf(start, end).coerceIn(1, totalPages)
                        val to = maxOf(start, end).coerceIn(1, totalPages)
                        for (p in from..to) {
                            resultSet.add(p)
                        }
                    }
                }
            } else {
                val pageNum = token.toIntOrNull()
                if (pageNum != null && pageNum in 1..totalPages) {
                    resultSet.add(pageNum)
                }
            }
        }

        val sorted = resultSet.sorted()
        return if (sorted.isEmpty()) (1..totalPages).toList() else sorted
    }
}
