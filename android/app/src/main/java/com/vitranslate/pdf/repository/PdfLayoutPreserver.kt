package com.vitranslate.pdf.repository

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import com.tom_roush.pdfbox.android.PDFBoxResourceLoader
import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.pdmodel.PDPage
import com.tom_roush.pdfbox.pdmodel.PDPageContentStream
import com.tom_roush.pdfbox.pdmodel.common.PDRectangle
import com.tom_roush.pdfbox.pdmodel.font.PDFont
import com.tom_roush.pdfbox.pdmodel.font.PDSimpleFont
import com.tom_roush.pdfbox.pdmodel.font.PDType0Font
import com.tom_roush.pdfbox.text.PDFTextStripper
import com.tom_roush.pdfbox.text.TextPosition
import com.tom_roush.pdfbox.contentstream.operator.Operator
import com.tom_roush.pdfbox.cos.COSArray
import com.tom_roush.pdfbox.cos.COSString
import com.tom_roush.pdfbox.pdfparser.PDFStreamParser
import com.tom_roush.pdfbox.pdfwriter.ContentStreamWriter
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStream
import java.util.regex.Pattern
import kotlin.math.abs

data class TranslationResult(
    val outputPath: String,
    val untranslatedCount: Int
)

/**
 * Raised when the user stops a run. A half-written PDF is worse than none, so
 * whoever throws this is responsible for having deleted the partial output.
 */
class TranslationCancelledException : Exception("Translation cancelled")

class PdfLayoutPreserver(private val context: Context) {

    init {
        try {
            PDFBoxResourceLoader.init(context)
        } catch (_: Exception) {
            // Already initialized or fallback
        }
    }

    fun countPages(uri: Uri): Int {
        return try {
            context.contentResolver.openInputStream(uri)?.use { inputStream ->
                PDDocument.load(inputStream).use { doc ->
                    doc.numberOfPages
                }
            } ?: 0
        } catch (_: Exception) {
            0
        }
    }

    fun translatePdf(
        inputUri: Uri,
        outputDirUriOrPath: String?,
        targetLang: String,
        overwrite: Boolean,
        onProgress: (done: Int, total: Int) -> Unit,
        onLog: ((String) -> Unit)? = null,
        isCancelled: () -> Boolean = { false }
    ): TranslationResult {
        val originalFileName = getFileName(inputUri)
        onLog?.invoke("Bắt đầu xử lý file: $originalFileName (Ngôn ngữ đích: $targetLang, Ghi đè: $overwrite)")

        val baseName = if (originalFileName.endsWith(".pdf", ignoreCase = true)) {
            originalFileName.substring(0, originalFileName.length - 4)
        } else {
            originalFileName
        }
        val outputFileName = "$baseName-$targetLang.pdf"
        val (outputStream, resultPath) = prepareOutputStream(outputDirUriOrPath, outputFileName, overwrite)
        val engine = GoogleTranslateEngine(sourceLang = "auto", targetLang = targetLang)
        var untranslatedCount = 0

        try {
            outputStream.use { outStream ->
                context.contentResolver.openInputStream(inputUri)?.use { inputStream ->
                    PDDocument.load(inputStream).use { document ->
                        val totalPages = document.numberOfPages
                        onProgress(0, totalPages)
                        onLog?.invoke("Mở file PDF thành công. Tổng số trang: $totalPages")
                        val font: PDFont = loadBundledFont(document)

                        for (pageIndex in 0 until totalPages) {
                            if (isCancelled()) throw TranslationCancelledException()
                            val page = document.getPage(pageIndex)
                            val textCollector = PageTextCollector()
                            textCollector.extractPageText(document, page, pageIndex)
                            val collapsedBlocks = collapseVerticalFractions(textCollector.blocks)
                            val textBlocks = groupIntoLineRuns(collapsedBlocks)

                            if (textBlocks.isNotEmpty()) {
                                val translations = mutableListOf<BlockTranslation>()
                                var skippedMathCount = 0

                                for (block in textBlocks) {
                                    if (isCancelled()) throw TranslationCancelledException()
                                    val originalText = block.text.trim()
                                    if (originalText.isBlank()) continue

                                    // Separate option label prefix (e.g. "A.") from content before translating
                                    val (optionLabel, remainder) = splitOptionLabel(originalText)
                                    val textToTranslate = remainder.trim()

                                    // Skip translating standalone math formulas and numeric choices, but preserve them in translations list
                                    if (textToTranslate.isBlank() || isPureMathOrFormula(textToTranslate)) {
                                        skippedMathCount++
                                        translations.add(BlockTranslation(block, originalText))
                                        continue
                                    }

                                    val encodedText = FormulaPlaceholder.encodeFormulaPlaceholders(textToTranslate)
                                    var translatedRaw = encodedText
                                    var translationSuccess = false
                                    try {
                                        translatedRaw = engine.translate(encodedText)
                                        translationSuccess = true
                                    } catch (_: Exception) {
                                        untranslatedCount++
                                    }
                                    var translatedRemainder = translatedRaw
                                    if (translationSuccess) {
                                        try {
                                            translatedRemainder = FormulaPlaceholder.restoreFormulaPlaceholders(textToTranslate, translatedRaw)
                                        } catch (_: Exception) {
                                            translatedRemainder = FormulaPlaceholder.removeControlCharacters(translatedRaw)
                                                .replace(Regex("</?b\\d+>"), "")
                                                .replace(Regex("</?s[123]>"), "")
                                        }
                                    }

                                    // Re-attach option label prefix if it was present
                                    val translatedText = if (optionLabel != null) {
                                        optionLabel + translatedRemainder
                                    } else {
                                        translatedRemainder
                                    }
                                    translations.add(BlockTranslation(block, translatedText))
                                }

                                onLog?.invoke("Trang ${pageIndex + 1}/$totalPages: Tìm thấy ${textBlocks.size} đoạn. Đã dịch: ${translations.size}, Bỏ qua công thức: $skippedMathCount")

                                if (translations.isNotEmpty()) {
                                    // Strip original text from page streams so vector drawings & diagrams remain 100% pristine
                                    stripTextFromPage(document, page)

                                    PDPageContentStream(
                                        document,
                                        page,
                                        PDPageContentStream.AppendMode.APPEND,
                                        true,
                                        true
                                    ).use { stream ->
                                        for (i in translations.indices) {
                                            val translation = translations[i]
                                            val block = translation.block
                                            val cleanedText = stripTagsAndPlaceholders(translation.translated)
                                            val text = sanitizeForFont(cleanedText, font)
                                            if (text.isBlank()) continue

                                            val nextY = if (i + 1 < translations.size) translations[i + 1].block.y else 0f
                                            val maxAllowedHeight = if (nextY > 0f && block.y > nextY) (block.y - nextY) * 0.9f else Float.MAX_VALUE
                                            coverSourceText(stream, block)
                                            drawTextWithWrapping(stream, font, block, text, maxAllowedHeight, textCollector.cropBox.width)
                                        }
                                    }
                                }
                            } else {
                                onLog?.invoke("Trang ${pageIndex + 1}/$totalPages: Không tìm thấy văn bản nào.")
                            }
                            onProgress(pageIndex + 1, totalPages)
                        }
                        document.save(outStream)
                        onLog?.invoke("Lưu file dịch thành công: $resultPath")
                    }
                } ?: throw Exception("Unable to open input PDF stream")
            }
        } catch (cancelled: TranslationCancelledException) {
            deleteOutput(resultPath)
            onLog?.invoke("Đã huỷ khi đang dịch $originalFileName, đã xoá file dở dang.")
            throw cancelled
        }

        return TranslationResult(
            outputPath = resultPath,
            untranslatedCount = untranslatedCount
        )
    }

    /** Removes a half-written output, whether it landed in SAF or on a path. */
    private fun deleteOutput(resultPath: String) {
        try {
            if (resultPath.startsWith("content://")) {
                DocumentFile.fromSingleUri(context, Uri.parse(resultPath))?.delete()
            } else {
                File(resultPath).delete()
            }
        } catch (_: Exception) {
            // Nothing left to do; the caller is already unwinding.
        }
    }

    private fun prepareOutputStream(
        outputDirUriOrPath: String?,
        outputFileName: String,
        overwrite: Boolean
    ): Pair<OutputStream, String> {
        if (!outputDirUriOrPath.isNullOrBlank() && outputDirUriOrPath.startsWith("content://")) {
            try {
                val treeUri = Uri.parse(outputDirUriOrPath)
                val docTree = DocumentFile.fromTreeUri(context, treeUri)
                if (docTree != null) {
                    val targetFile = getUniqueSafFile(docTree, outputFileName, overwrite)
                    if (targetFile != null) {
                        val outStream = context.contentResolver.openOutputStream(targetFile.uri, "w")
                        if (outStream != null) {
                            return Pair(outStream, targetFile.uri.toString())
                        }
                    }
                }
            } catch (_: Exception) {
                // Fallback to standard file path below
            }
        }
        val defaultDir = File(context.getExternalFilesDir(null), "translated")
        val outputDir = try {
            if (!outputDirUriOrPath.isNullOrBlank() && !outputDirUriOrPath.startsWith("content://")) {
                val dir = File(outputDirUriOrPath)
                if (!dir.exists()) dir.mkdirs()
                if (dir.exists() && dir.canWrite()) dir else defaultDir
            } else {
                defaultDir
            }
        } catch (_: Exception) {
            defaultDir
        }
        if (!outputDir.exists()) {
            outputDir.mkdirs()
        }

        val outputFile = getUniqueFile(outputDir, outputFileName, overwrite)
        return Pair(FileOutputStream(outputFile, false), outputFile.absolutePath)
    }

    private fun getUniqueFile(dir: File, baseOutputName: String, overwrite: Boolean): File {
        val file = File(dir, baseOutputName)
        if (overwrite || !file.exists()) {
            if (overwrite && file.exists()) {
                try { file.delete() } catch (_: Exception) {}
            }
            return file
        }

        val nameWithoutExt = if (baseOutputName.endsWith(".pdf", ignoreCase = true)) {
            baseOutputName.substring(0, baseOutputName.length - 4)
        } else {
            baseOutputName
        }
        var counter = 1
        while (true) {
            val candidate = File(dir, "$nameWithoutExt ($counter).pdf")
            if (!candidate.exists()) {
                return candidate
            }
            counter++
        }
    }

    private fun getUniqueSafFile(docTree: DocumentFile, baseOutputName: String, overwrite: Boolean): DocumentFile? {
        val existing = docTree.findFile(baseOutputName)
        if (existing != null) {
            if (overwrite) {
                try { existing.delete() } catch (_: Exception) {}
                return docTree.createFile("application/pdf", baseOutputName)
            }
            val nameWithoutExt = if (baseOutputName.endsWith(".pdf", ignoreCase = true)) {
                baseOutputName.substring(0, baseOutputName.length - 4)
            } else {
                baseOutputName
            }
            var counter = 1
            while (true) {
                val candidateName = "$nameWithoutExt ($counter).pdf"
                if (docTree.findFile(candidateName) == null) {
                    return docTree.createFile("application/pdf", candidateName)
                }
                counter++
            }
        }
        return docTree.createFile("application/pdf", baseOutputName)
    }

    private val OPTION_LABEL_PATTERN = Pattern.compile("^([(]?[A-Da-d1-9][.)])\\s*")

    /**
     * Extracts multiple-choice option prefixes like "A.", "B)", "C." so they aren't mangled by translation.
     */
    private fun splitOptionLabel(text: String): Pair<String?, String> {
        val matcher = OPTION_LABEL_PATTERN.matcher(text)
        return if (matcher.find() && matcher.start() == 0) {
            val label = matcher.group()
            Pair(label, text.substring(matcher.end()))
        } else {
            Pair(null, text)
        }
    }

    private fun stripTagsAndPlaceholders(text: String): String {
        return text
            .replace(Regex("</?b\\d+>"), "")
            .replace(Regex("</?s[123]>"), "")
            .replace(Regex("\\{\\s*v\\d+\\s*\\}"), "")
    }

    /**
     * Groups raw text fragments into horizontal lines and runs, retaining superscript
     * positioning, and collapses vertical fraction stacks (numerator / bar / denominator)
     * into a single inline "(numerator) / (denominator)" line before run-splitting.
     */
    private fun groupIntoLineRuns(raw: List<TextBlock>): List<TextBlock> {
        if (raw.isEmpty()) return emptyList()

        val lines = mutableListOf<MutableList<TextBlock>>()
        for (frag in raw) {
            val lastLine = lines.lastOrNull()
            if (lastLine != null) {
                val refFrag = lastLine.maxByOrNull { it.fontSize } ?: lastLine.last()
                val maxFontSize = maxOf(refFrag.fontSize, frag.fontSize)
                if (abs(frag.y - refFrag.y) <= maxFontSize * 0.55f) {
                    lastLine.add(frag)
                    continue
                }
            }
            lines.add(mutableListOf(frag))
        }

        val result = mutableListOf<TextBlock>()
        for (line in lines) {
            val lineSorted = line.sortedBy { it.x }
            var runFrags = mutableListOf<TextBlock>()
            var prev: TextBlock? = null

            fun flushRun() {
                if (runFrags.isNotEmpty()) {
                    result.add(mergeFragments(runFrags))
                    runFrags = mutableListOf()
                }
            }

            for (frag in lineSorted) {
                if (prev == null) {
                    runFrags.add(frag)
                } else {
                    val gap = frag.x - (prev.x + prev.width)
                    val threshold = maxOf(prev.fontSize, frag.fontSize) * 1.5f
                    if (gap <= threshold) {
                        runFrags.add(frag)
                    } else {
                        flushRun()
                        runFrags.add(frag)
                    }
                }
                prev = frag
            }
            flushRun()
        }
        return result
    }

    companion object {
        private val MATH_FUNCTION_WORDS = Regex(
            "\\b(?:ln|log|lim|sin|cos|tan|cot|sec|csc|exp|max|min|mod|sqrt|rad|deg|fnc|fnt|fn)\\b",
            RegexOption.IGNORE_CASE
        )
        private val MATH_SYMBOL_ONLY_PATTERN = Pattern.compile(
            "^[0-9+\\-*/=()<>\\[\\]{},._:;^√∫∑∞≤≥≠±∓×÷%'\"\\\\|\\s]*$"
        )
        private val LETTER_RUN_PATTERN = Regex("[\\p{L}]+")
        private val FRACTION_BAR_PATTERN = Regex("^[-_–—―─═]{1,}$")

        private val SUPERSCRIPT_DIGIT_MAP = mapOf(
            '0' to '⁰', '1' to '¹', '2' to '²', '3' to '³', '4' to '⁴',
            '5' to '⁵', '6' to '⁶', '7' to '⁷', '8' to '⁸', '9' to '⁹',
            '+' to '⁺', '-' to '⁻'
        )

        private val SUBSCRIPT_DIGIT_MAP = mapOf(
            '0' to '₀', '1' to '₁', '2' to '₂', '3' to '₃', '4' to '₄',
            '5' to '₅', '6' to '₆', '7' to '₇', '8' to '₈', '9' to '₉',
            '+' to '₊', '-' to '₋'
        )

        fun toSuperscriptToken(raw: String): String {
            val trimmed = raw.trim()
            if (trimmed.isEmpty()) return trimmed
            return trimmed.map { SUPERSCRIPT_DIGIT_MAP[it] ?: it }.joinToString("")
        }

        fun toSubscriptToken(raw: String): String {
            val trimmed = raw.trim()
            if (trimmed.isEmpty()) return trimmed
            return trimmed.map { SUBSCRIPT_DIGIT_MAP[it] ?: it }.joinToString("")
        }

        /**
         * Checks if text consists of mathematical expressions, variables, or functions rather than plain prose sentences.
         */
        fun isPureMathOrFormula(text: String): Boolean {
            val trimmed = text.trim()
            if (trimmed.isEmpty()) return false

            val withoutFunctionWords = trimmed.replace(MATH_FUNCTION_WORDS, " ")
            val letterRuns = LETTER_RUN_PATTERN.findAll(withoutFunctionWords).map { it.value }.toList()

            val hasLongProseWord = letterRuns.any { it.length > 2 }
            if (!hasLongProseWord) {
                val withoutVariableLetters = withoutFunctionWords.replace(Regex("[\\p{L}]"), "")
                if (MATH_SYMBOL_ONLY_PATTERN.matcher(withoutVariableLetters).matches()) {
                    return true
                }
            }

            val hasMathOperators = Regex("[=/^√≤≥≠±∈∉⊂⊃∩∪]").containsMatchIn(trimmed)
            if (hasMathOperators && trimmed.length <= 60 && letterRuns.count { it.length > 2 } <= 2) {
                return true
            }

            return false
        }

        /**
         * Scans raw extracted text blocks and merges vertical fraction stacks
         * ONLY when an explicit fraction bar (a run of dashes/underscores) is
         * found between the numerator and denominator.  Without a bar, blocks
         * are never merged — this avoids destroying normal consecutive text lines.
         */
        // Strict pattern: only digits, single letters, operators, parens, superscript/subscript
        private val SHORT_MATH_PATTERN = Pattern.compile(
            "^[0-9a-zA-Z⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻₀₁₂₃₄₅₆₇₈₉₊₋+\\-*/^().√∞παβγθ\\s]{1,8}$"
        )

        fun collapseVerticalFractions(raw: List<TextBlock>): List<TextBlock> {
            if (raw.size < 2) return raw

            fun overlapRatio(aMin: Float, aMax: Float, bMin: Float, bMax: Float): Float {
                val overlap = minOf(aMax, bMax) - maxOf(aMin, bMin)
                if (overlap <= 0f) return 0f
                val smaller = minOf(aMax - aMin, bMax - bMin)
                return if (smaller <= 0f) 0f else overlap / smaller
            }

            fun isBar(text: String) = FRACTION_BAR_PATTERN.matches(text.trim())
            fun isShortMath(text: String) = SHORT_MATH_PATTERN.matcher(text.trim()).matches()

            val unused = raw.sortedWith(compareByDescending<TextBlock> { it.y }.thenBy { it.x }).toMutableList()
            val collapsed = mutableListOf<TextBlock>()
            val consumed = mutableSetOf<TextBlock>()

            // Pass 1: explicit  numerator ── bar ── denominator  triples
            for (bar in unused) {
                if (!isBar(bar.text)) continue
                if (consumed.contains(bar)) continue

                val barFont = bar.fontSize
                val barRight = bar.x + bar.width

                val numCandidate = unused.firstOrNull { c ->
                    !consumed.contains(c) && !isBar(c.text) &&
                    c.y > bar.y && (c.y - bar.y) <= barFont * 1.8f &&
                    overlapRatio(c.x, c.x + c.width, bar.x, barRight) > 0.5f &&
                    c.text.trim().length <= 20
                }
                if (numCandidate == null) continue

                val denCandidate = unused.firstOrNull { c ->
                    !consumed.contains(c) && !isBar(c.text) && c != numCandidate &&
                    bar.y > c.y && (bar.y - c.y) <= barFont * 1.8f &&
                    overlapRatio(c.x, c.x + c.width, bar.x, barRight) > 0.5f &&
                    c.text.trim().length <= 20
                }
                if (denCandidate == null) continue

                consumed.add(bar)
                consumed.add(numCandidate)
                consumed.add(denCandidate)

                val numText = numCandidate.text.trim()
                val denText = denCandidate.text.trim()
                val needsNumP = numText.contains(' ') || Regex("[+\\-]").containsMatchIn(numText)
                val needsDenP = denText.contains(' ') || Regex("[+\\-]").containsMatchIn(denText)
                val fN = if (needsNumP && !numText.startsWith("(")) "($numText)" else numText
                val fD = if (needsDenP && !denText.startsWith("(")) "($denText)" else denText
                val minX = minOf(numCandidate.x, denCandidate.x, bar.x)
                val maxR = maxOf(numCandidate.x + numCandidate.width, denCandidate.x + denCandidate.width, barRight)
                val avgY = (numCandidate.y + denCandidate.y) / 2f
                val topY = numCandidate.y + numCandidate.ascent
                val botY = denCandidate.y - denCandidate.descent

                collapsed.add(TextBlock(
                    text = "$fN/$fD",
                    x = minX, y = avgY,
                    fontSize = maxOf(numCandidate.fontSize, denCandidate.fontSize),
                    width = maxR - minX,
                    ascent = topY - avgY,
                    descent = avgY - botY
                ))
            }

            // Pass 2: bar-less fractions — ONLY very short pure-math blocks
            val remaining = unused.filter { !consumed.contains(it) }
                .sortedWith(compareByDescending<TextBlock> { it.y }.thenBy { it.x })
                .toMutableList()

            val consumed2 = mutableSetOf<TextBlock>()
            for (top in remaining) {
                if (consumed2.contains(top)) continue
                if (!isShortMath(top.text)) continue

                val topFont = top.fontSize
                val topRight = top.x + top.width

                val bot = remaining.firstOrNull { c ->
                    !consumed2.contains(c) && c != top &&
                    top.y > c.y &&                                         // below
                    (top.y - c.y) <= topFont * 1.2f &&                     // very tight gap
                    overlapRatio(top.x, topRight, c.x, c.x + c.width) > 0.6f &&
                    isShortMath(c.text) &&
                    abs(c.fontSize - topFont) <= topFont * 0.3f            // similar font size
                }
                if (bot == null) continue

                consumed2.add(top)
                consumed2.add(bot)

                // Consume any intervening bar/dash block between top and bot
                for (mid in remaining) {
                    if (!consumed2.contains(mid) && mid.y < top.y && mid.y > bot.y) {
                        if (overlapRatio(top.x, topRight, mid.x, mid.x + mid.width) > 0.3f) {
                            consumed2.add(mid)
                        }
                    }
                }

                val nT = top.text.trim(); val dT = bot.text.trim()
                val minX = minOf(top.x, bot.x)
                val maxR = maxOf(topRight, bot.x + bot.width)
                val avgY = (top.y + bot.y) / 2f
                val topY = top.y + top.ascent
                val botY = bot.y - bot.descent

                collapsed.add(TextBlock(
                    text = "$nT/$dT",
                    x = minX, y = avgY,
                    fontSize = maxOf(top.fontSize, bot.fontSize),
                    width = maxR - minX,
                    ascent = topY - avgY,
                    descent = avgY - botY
                ))
            }

            // Keep unconsumed blocks
            for (block in remaining) {
                if (!consumed2.contains(block)) collapsed.add(block)
            }

            return collapsed.sortedWith(compareByDescending<TextBlock> { it.y }.thenBy { it.x })
        }
    }

    /**
     * Combines line fragments into a single text block, handling superscript and subscript formatting.
     */
    private fun mergeFragments(frags: List<TextBlock>): TextBlock {
        val sorted = frags.sortedBy { it.x }
        val refFrag = sorted.maxByOrNull { it.fontSize } ?: sorted.first()
        val refFontSize = refFrag.fontSize
        val refY = refFrag.y

        val sb = StringBuilder()
        for ((index, frag) in sorted.withIndex()) {
            val isSuperscript = frag.fontSize <= refFontSize * 0.8f &&
                frag.y > refY + refFontSize * 0.12f
            val isSubscript = frag.fontSize <= refFontSize * 0.8f &&
                frag.y < refY - refFontSize * 0.12f
            val piece = when {
                isSuperscript -> toSuperscriptToken(frag.text)
                isSubscript -> toSubscriptToken(frag.text)
                else -> frag.text
            }
            when {
                index == 0 -> sb.append(piece)
                isSuperscript || isSubscript -> sb.append(piece)
                sb.endsWith("sqrt") || sb.endsWith("√") -> sb.append(piece)
                else -> sb.append(' ').append(piece)
            }
        }
        val combinedText = sb.toString().replace(Regex("\\s+"), " ").trim()

        val minX = sorted.minOf { it.x }
        val maxRight = sorted.maxOf { it.x + it.width }
        val maxAscent = sorted.maxOf { it.ascent }
        val maxDescent = sorted.maxOf { it.descent }
        return TextBlock(
            text = combinedText,
            x = minX,
            y = refY,
            fontSize = refFontSize,
            width = maxRight - minX,
            ascent = maxAscent,
            descent = maxDescent
        )
    }

    private fun drawTextWithWrapping(
        stream: PDPageContentStream,
        font: PDFont,
        block: TextBlock,
        text: String,
        maxAllowedHeight: Float = Float.MAX_VALUE,
        cropBoxWidth: Float = 612f
    ) {
        val baseFontSize = block.fontSize.coerceIn(6f, 72f)
        val availableWidth = if (cropBoxWidth > 0f) {
            maxOf(block.width, cropBoxWidth - block.x - 40f).coerceAtLeast(30f)
        } else {
            maxOf(block.width, 30f)
        }
        val minSingleLineFontSize = maxOf(baseFontSize * 0.6f, 5.5f)

        var chosenFontSize = baseFontSize
        var fits = measureStringWidth(text, font, chosenFontSize) <= availableWidth * 1.05f
        if (!fits) {
            var size = baseFontSize - 0.5f
            while (size >= minSingleLineFontSize) {
                if (measureStringWidth(text, font, size) <= availableWidth * 1.05f) {
                    chosenFontSize = size
                    fits = true
                    break
                }
                size -= 0.5f
            }
        }

        if (fits) {
            stream.beginText()
            stream.setFont(font, chosenFontSize)
            stream.newLineAtOffset(block.x, block.y)
            try {
                stream.showText(text)
            } catch (_: Exception) {}
            stream.endText()
            return
        }

        var wrapFontSize = minSingleLineFontSize
        var lines = wrapText(text, font, wrapFontSize, availableWidth)
        var lineHeight = wrapFontSize * 1.25f

        // Shrink font size if wrapped lines would overflow downward into the element below
        while (lines.size * lineHeight > maxAllowedHeight && wrapFontSize > 4.5f) {
            wrapFontSize *= 0.9f
            lineHeight = wrapFontSize * 1.25f
            lines = wrapText(text, font, wrapFontSize, availableWidth)
        }

        for ((index, line) in lines.withIndex()) {
            val lineY = block.y - (index * lineHeight)
            val sanitizedLine = sanitizeForFont(line, font)
            if (sanitizedLine.isBlank()) continue
            stream.beginText()
            stream.setFont(font, wrapFontSize)
            stream.newLineAtOffset(block.x, lineY)
            try {
                stream.showText(sanitizedLine)
            } catch (_: Exception) {}
            stream.endText()
        }
    }

    private fun wrapText(
        text: String,
        font: PDFont,
        fontSize: Float,
        maxWidth: Float
    ): List<String> {
        val words = text.split(" ")
        val lines = mutableListOf<String>()
        var currentLine = StringBuilder()
        for (word in words) {
            if (currentLine.isEmpty()) {
                currentLine.append(word)
            } else {
                val candidate = "${currentLine} $word"
                if (measureStringWidth(candidate, font, fontSize) <= maxWidth) {
                    currentLine.append(" ").append(word)
                } else {
                    lines.add(currentLine.toString())
                    currentLine = StringBuilder(word)
                }
            }
        }
        if (currentLine.isNotEmpty()) {
            lines.add(currentLine.toString())
        }
        return if (lines.isEmpty()) listOf(text) else lines
    }

    private fun measureStringWidth(text: String, font: PDFont, fontSize: Float): Float {
        return try {
            font.getStringWidth(text) / 1000f * fontSize
        } catch (_: Exception) {
            text.length * fontSize * 0.5f
        }
    }

    private fun loadBundledFont(document: PDDocument): PDFont {
        val candidatePaths = listOf(
            "fonts/NotoSerif-Regular.ttf",
            "fonts/NotoSans-Regular.ttf"
        )
        var lastError: Exception? = null
        for (path in candidatePaths) {
            try {
                context.assets.open(path).use { fontStream ->
                    return PDType0Font.load(document, fontStream)
                }
            } catch (e: Exception) {
                lastError = e
            }
        }
        throw Exception(
            "Failed to load bundled font. Ensure fonts/NotoSerif-Regular.ttf " +
                "or fonts/NotoSans-Regular.ttf exists in assets.",
            lastError
        )
    }

    private fun coverSourceText(
        stream: PDPageContentStream,
        block: TextBlock
    ) {
        val padX = 1.0f
        val padTop = 1.0f
        val padBottom = 1.0f
        val rectX = block.x - padX
        val rectY = block.y - block.descent - padBottom
        val rectW = block.width + padX * 2.0f
        val rectH = block.ascent + block.descent + padTop + padBottom
        if (rectW <= 0f || rectH <= 0f) return
        stream.saveGraphicsState()
        @Suppress("DEPRECATION")
        stream.setNonStrokingColor(255, 255, 255)
        stream.addRect(rectX, rectY, rectW, rectH)
        stream.fill()
        stream.restoreGraphicsState()
    }

    private fun stripTextFromPage(document: PDDocument, page: PDPage) {
        try {
            val parser = PDFStreamParser(page)
            parser.parse()
            val tokens = parser.tokens
            val newTokens = mutableListOf<Any>()
            var inTextObject = false

            for (token in tokens) {
                if (token is Operator) {
                    val opName = token.name
                    if (opName == "BT") {
                        inTextObject = true
                        newTokens.add(token)
                    } else if (opName == "ET") {
                        inTextObject = false
                        newTokens.add(token)
                    } else if (inTextObject && (opName == "Tj" || opName == "'")) {
                        if (newTokens.isNotEmpty() && newTokens.last() is COSString) {
                            newTokens[newTokens.size - 1] = COSString("")
                        }
                        newTokens.add(token)
                    } else if (inTextObject && opName == "TJ") {
                        if (newTokens.isNotEmpty() && newTokens.last() is COSArray) {
                            newTokens[newTokens.size - 1] = COSArray()
                        }
                        newTokens.add(token)
                    } else if (inTextObject && opName == "\"") {
                        if (newTokens.isNotEmpty() && newTokens.last() is COSString) {
                            newTokens[newTokens.size - 1] = COSString("")
                        }
                        newTokens.add(token)
                    } else {
                        newTokens.add(token)
                    }
                } else {
                    newTokens.add(token)
                }
            }

            val newStream = com.tom_roush.pdfbox.pdmodel.common.PDStream(document)
            val out = newStream.createOutputStream()
            val contentWriter = ContentStreamWriter(out)
            contentWriter.writeTokens(newTokens)
            out.close()
            page.setContents(newStream)
        } catch (_: Exception) {}
    }

    private fun sanitizeForFont(text: String, font: PDFont): String {
        val sb = StringBuilder(text.length)
        for (char in text) {
            if (char == '\n' || char == '\r' || char == '\t') {
                sb.append(' ')
                continue
            }
            if (char.code < 32 || char == '¯' || char == '‾' || char == '\u02C9') continue
            try {
                font.encode(char.toString())
                sb.append(char)
            } catch (_: Exception) {
                when (char) {
                    '⁰' -> sb.append('0')
                    '¹' -> sb.append('1')
                    '²' -> sb.append('2')
                    '³' -> sb.append('3')
                    '⁴' -> sb.append('4')
                    '⁵' -> sb.append('5')
                    '⁶' -> sb.append('6')
                    '⁷' -> sb.append('7')
                    '⁸' -> sb.append('8')
                    '⁹' -> sb.append('9')
                    '⁺' -> sb.append('+')
                    '⁻' -> sb.append('-')
                    '∞' -> sb.append("inf")
                    '≤' -> sb.append("<=")
                    '≥' -> sb.append(">=")
                    '≠' -> sb.append("!=")
                    '±' -> sb.append("+/-")
                    '×' -> sb.append("*")
                    '÷' -> sb.append("/")
                    'π' -> sb.append("pi")
                    'α' -> sb.append("alpha")
                    'β' -> sb.append("beta")
                    'γ' -> sb.append("gamma")
                    'θ' -> sb.append("theta")
                    // Subscript digits (u₁, Q₁, ...) — degrade to the plain digit rather
                    // than vanishing if the bundled font lacks the subscript block.
                    '₀' -> sb.append('0')
                    '₁' -> sb.append('1')
                    '₂' -> sb.append('2')
                    '₃' -> sb.append('3')
                    '₄' -> sb.append('4')
                    '₅' -> sb.append('5')
                    '₆' -> sb.append('6')
                    '₇' -> sb.append('7')
                    '₈' -> sb.append('8')
                    '₉' -> sb.append('9')
                    '₊' -> sb.append('+')
                    '₋' -> sb.append('-')
                    // Blackboard-bold set letters (ℝ, ℕ, ...) — degrade to the plain letter.
                    'ℂ' -> sb.append('C')
                    'ℍ' -> sb.append('H')
                    'ℕ' -> sb.append('N')
                    'ℙ' -> sb.append('P')
                    'ℚ' -> sb.append('Q')
                    'ℝ' -> sb.append('R')
                    'ℤ' -> sb.append('Z')
                    // TeX/AMS math symbols resolved by TexMathSymbols — degrade to a short
                    // ASCII gloss rather than disappearing if the bundled font can't render them.
                    '∈' -> sb.append(" in ")
                    '∉' -> sb.append(" not in ")
                    '∋' -> sb.append(" contains ")
                    '∅' -> sb.append(" empty set ")
                    '∃' -> sb.append(" exists ")
                    '∀' -> sb.append(" for all ")
                    '∩' -> sb.append(" intersect ")
                    '∪' -> sb.append(" union ")
                    '⊂' -> sb.append(" subset ")
                    '⊃' -> sb.append(" superset ")
                    '⊆' -> sb.append(" subset-eq ")
                    '⊇' -> sb.append(" superset-eq ")
                    '∧' -> sb.append(" and ")
                    '∨' -> sb.append(" or ")
                    '¬' -> sb.append(" not ")
                    '→' -> sb.append(" -> ")
                    '←' -> sb.append(" <- ")
                    '↔' -> sb.append(" <-> ")
                    '⇒' -> sb.append(" => ")
                    '⇐' -> sb.append(" <= ")
                    '⇔' -> sb.append(" <=> ")
                    '∼' -> sb.append(" ~ ")
                    '≅' -> sb.append(" ~= ")
                    '≈' -> sb.append(" ~~ ")
                    '⊗' -> sb.append("(x)")
                    '⊕' -> sb.append("(+)")
                    '√' -> {}  // radical glyph — radicand already extracted separately
                    '∇' -> sb.append("nabla")
                    '∂' -> sb.append('d')
                    '∑' -> sb.append("sum")
                    '∏' -> sb.append("prod")
                    '∫' -> sb.append("integral")
                    '⊥' -> sb.append(" perp ")
                    '∠' -> sb.append(" angle ")
                    '∴' -> sb.append(" therefore ")
                    else -> sb.append(' ')
                }
            }
        }
        return sb.toString()
    }

    private fun getFileName(uri: Uri): String {
        var name = "document.pdf"
        context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val nameIndex = cursor.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
            if (nameIndex != -1 && cursor.moveToFirst()) {
                name = cursor.getString(nameIndex)
            }
        }
        return name
    }

    private class BlockTranslation(
        val block: TextBlock,
        val translated: String
    )

    class TextBlock(
        val text: String,
        val x: Float,
        val y: Float,
        val fontSize: Float,
        val width: Float,
        val ascent: Float,
        val descent: Float
    )

    private inner class PageTextCollector : PDFTextStripper() {
        val blocks = mutableListOf<TextBlock>()
        var cropBox: PDRectangle = PDRectangle(0f, 0f, 612f, 792f)

        init {
            sortByPosition = true
        }

        fun extractPageText(document: PDDocument, page: PDPage, pageIndex: Int) {
            cropBox = page.cropBox ?: page.mediaBox
            startPage = pageIndex + 1
            endPage = pageIndex + 1
            blocks.clear()
            writeText(document, NullWriter())
        }

        /**
         * Looks up a Unicode fallback for a glyph from a known TeX/AMS math symbol font by
         * resolving its PostScript glyph name via the font's simple encoding. Safe to call
         * on any TextPosition; returns null (never throws) if the font isn't a recognized
         * symbol font, isn't a simple (Type1-style) font, or the glyph name is unmapped.
         */
        private fun resolveTexFallback(tp: TextPosition): String? {
            return try {
                val font = tp.font ?: return null
                val baseName = font.name
                if (!TexMathSymbols.isSymbolFont(baseName)) return null
                val codes = tp.characterCodes
                val code = codes?.firstOrNull() ?: return null
                val glyphName = (font as? PDSimpleFont)?.encoding?.getName(code)
                TexMathSymbols.resolve(baseName, glyphName)
            } catch (_: Exception) {
                null
            }
        }

        override fun writeString(text: String, textPositions: MutableList<TextPosition>) {
            if (textPositions.isEmpty()) return

            val clusters = mutableListOf<MutableList<TextPosition>>()
            var currentCluster = mutableListOf<TextPosition>()

            for (tp in textPositions) {
                if (currentCluster.isEmpty()) {
                    currentCluster.add(tp)
                } else {
                    val prev = currentCluster.last()
                    val gap = tp.xDirAdj - (prev.xDirAdj + prev.widthDirAdj)
                    val maxFont = maxOf(prev.fontSizeInPt, tp.fontSizeInPt)

                    // Separate runs if there's a horizontal gap bigger than 1.5x font size, or a Y jump
                    if (gap > maxFont * 1.5f || abs(tp.yDirAdj - prev.yDirAdj) > maxFont * 0.4f) {
                        clusters.add(currentCluster)
                        currentCluster = mutableListOf(tp)
                    } else {
                        currentCluster.add(tp)
                    }
                }
            }
            if (currentCluster.isNotEmpty()) {
                clusters.add(currentCluster)
            }

            for (cluster in clusters) {
                if (cluster.isEmpty()) continue
                val first = cluster.first()
                val last = cluster.last()
                val baseFontSize = cluster.maxOf { it.fontSizeInPt }
                val refDirAdj = first.yDirAdj

                val sb = StringBuilder()
                for (i in cluster.indices) {
                    val tp = cluster[i]
                    var ch = tp.unicode ?: ""

                    // Detect word-spacing gaps between characters. PDFs often
                    // represent spaces as positional gaps rather than actual
                    // space characters, so we must insert them ourselves.
                    if (i > 0) {
                        val prev = cluster[i - 1]
                        val gap = tp.xDirAdj - (prev.xDirAdj + prev.widthDirAdj)
                        val avgFont = (prev.fontSizeInPt + tp.fontSizeInPt) / 2f
                        if (gap > avgFont * 0.16f) {
                            sb.append(' ')
                        }
                    }

                    // TeX math symbol fonts (CMSY10, MSBM10, MSAM10, etc.) frequently lack a
                    // usable ToUnicode mapping, so PDFBox returns blank/control characters for
                    // glyphs like "∈", or renders a blackboard-bold letter (e.g. "R" for the
                    // reals, ℝ) as a plain, unstyled letter. Resolve those via glyph name.
                    val texFallback = resolveTexFallback(tp)
                    if (texFallback != null &&
                        (ch.isEmpty() || ch.codePointAt(0) < 0x20 || ch != texFallback)
                    ) {
                        ch = texFallback
                    }
                    if (ch.isEmpty()) continue

                    // yDirAdj increases *downward* on the page (image-space), so a smaller
                    // yDirAdj than the reference baseline means the glyph sits above the line
                    // (superscript, e.g. x²) and a larger yDirAdj means it sits below the line
                    // (subscript, e.g. u₁, Q₁).
                    val isRaised = tp.yDirAdj < refDirAdj - baseFontSize * 0.08f
                    val isLowered = tp.yDirAdj > refDirAdj + baseFontSize * 0.08f

                    when {
                        i > 0 && isRaised -> sb.append(toSuperscriptToken(ch))
                        i > 0 && isLowered -> sb.append(toSubscriptToken(ch))
                        else -> sb.append(ch)
                    }
                }
                val clusterText = sb.toString()
                if (clusterText.isBlank()) continue

                val x = cropBox.lowerLeftX + first.xDirAdj
                val y = cropBox.upperRightY - first.yDirAdj
                val width = maxOf((last.xDirAdj + last.widthDirAdj) - first.xDirAdj, baseFontSize * 0.5f)
                val ascent = baseFontSize * 0.8f
                val descent = baseFontSize * 0.2f
                blocks.add(TextBlock(clusterText, x, y, baseFontSize, width, ascent, descent))
            }
        }
    }

    /**
     * Fallback Unicode resolution for glyphs from TeX/AMS math symbol fonts (cmsy10,
     * cmmi10, msam10, msbm10, ...) that PDFBox cannot map via the embedded font's
     * ToUnicode CMap — a common gap for these fonts, which produces blank/dropped
     * characters (e.g. "∈") or an unstyled plain letter for blackboard-bold set
     * symbols (e.g. plain "R" instead of ℝ).
     */
    private object TexMathSymbols {
        // Standard Adobe/TeX PostScript glyph names, as they typically appear in the
        // /Differences array of an embedded TeX symbol font's /Encoding dictionary,
        // mapped to their Unicode equivalents. If your PDF's font subset uses different
        // glyph names, inspect the font's /Differences array (e.g. via `pdffonts -v`,
        // or PDSimpleFont.encoding.differences in PDFBox) and extend this table.
        private val GLYPH_NAME_MAP: Map<String, String> = mapOf(
            "element" to "∈",
            "elementof" to "∈",
            "notelement" to "∉",
            "owner" to "∋",
            "emptyset" to "∅",
            "existential" to "∃",
            "universal" to "∀",
            "infinity" to "∞",
            "intersection" to "∩",
            "union" to "∪",
            "propersubset" to "⊂",
            "propersuperset" to "⊃",
            "reflexsubset" to "⊆",
            "reflexsuperset" to "⊇",
            "logicaland" to "∧",
            "logicalor" to "∨",
            "logicalnot" to "¬",
            "arrowright" to "→",
            "arrowleft" to "←",
            "arrowboth" to "↔",
            "arrowdblright" to "⇒",
            "arrowdblleft" to "⇐",
            "arrowdblboth" to "⇔",
            "similar" to "∼",
            "congruent" to "≅",
            "approxequal" to "≈",
            "notequal" to "≠",
            "lessequal" to "≤",
            "greaterequal" to "≥",
            "multiply" to "×",
            "divide" to "÷",
            "circlemultiply" to "⊗",
            "circleplus" to "⊕",
            "radical" to "√",
            "gradient" to "∇",
            "partialdiff" to "∂",
            "summation" to "∑",
            "product" to "∏",
            "integral" to "∫",
            "perpendicular" to "⊥",
            "angle" to "∠",
            "therefore" to "∴"
        )

        // Blackboard-bold capital letters from AMS fonts (msbm10) that have a dedicated
        // Unicode "double-struck" codepoint. Letters without one (e.g. blackboard "S")
        // fall back to the plain letter since Unicode has no precomposed glyph for them.
        private val BLACKBOARD_MAP: Map<Char, String> = mapOf(
            'C' to "ℂ", 'H' to "ℍ", 'N' to "ℕ", 'P' to "ℙ",
            'Q' to "ℚ", 'R' to "ℝ", 'Z' to "ℤ"
        )

        private val SYMBOL_FONT_TOKENS = listOf("CMSY", "MSAM", "MSBM", "CMMI", "CMEX", "STMARY")

        fun isSymbolFont(fontName: String?): Boolean {
            if (fontName == null) return false
            val upper = fontName.uppercase()
            return SYMBOL_FONT_TOKENS.any { upper.contains(it) }
        }

        fun resolve(fontName: String, glyphName: String?): String? {
            if (glyphName == null || glyphName == ".notdef") return null

            val upperFont = fontName.uppercase()
            if (upperFont.contains("MSBM")) {
                if (glyphName.length == 1) {
                    val ch = glyphName[0]
                    if (ch in 'A'..'Z') {
                        return BLACKBOARD_MAP[ch] ?: glyphName
                    }
                }
            }

            return GLYPH_NAME_MAP[glyphName]
        }
    }

    private class NullWriter : java.io.Writer() {
        override fun write(cbuf: CharArray, off: Int, len: Int) {}
        override fun flush() {}
        override fun close() {}
    }
}
