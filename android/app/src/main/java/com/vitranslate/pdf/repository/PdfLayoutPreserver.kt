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
        onLog: ((String) -> Unit)? = null
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

        outputStream.use { outStream ->
            context.contentResolver.openInputStream(inputUri)?.use { inputStream ->
                PDDocument.load(inputStream).use { document ->
                    val totalPages = document.numberOfPages
                    onProgress(0, totalPages)
                    onLog?.invoke("Mở file PDF thành công. Tổng số trang: $totalPages")
                    val font: PDFont = loadBundledFont(document)

                    for (pageIndex in 0 until totalPages) {
                        val page = document.getPage(pageIndex)
                        val textCollector = PageTextCollector()
                        textCollector.extractPageText(document, page, pageIndex)
                        val textBlocks = groupIntoLineRuns(textCollector.blocks)

                        if (textBlocks.isNotEmpty()) {
                            val translations = mutableListOf<BlockTranslation>()
                            var skippedMathCount = 0

                            for (block in textBlocks) {
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

        return TranslationResult(
            outputPath = resultPath,
            untranslatedCount = untranslatedCount
        )
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

    private val MATH_FUNCTION_WORDS = Regex(
        "\\b(?:ln|log|lim|sin|cos|tan|cot|sec|csc|exp|max|min|mod|sqrt)\\b",
        RegexOption.IGNORE_CASE
    )
    private val MATH_SYMBOL_ONLY_PATTERN = Pattern.compile(
        "^[0-9+\\-*/=()<>\\[\\]{},._:;^√∫∑∞≤≥≠±∓×÷%'\"\\\\|\\s]*$"
    )
    private val LETTER_RUN_PATTERN = Regex("[A-Za-z]+")

    /**
     * Checks if text consists of mathematical expressions, variables, or functions rather than plain prose sentences.
     */
    private fun isPureMathOrFormula(text: String): Boolean {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return false

        val withoutFunctionWords = trimmed.replace(MATH_FUNCTION_WORDS, " ")

        val hasRealWord = LETTER_RUN_PATTERN.findAll(withoutFunctionWords)
            .any { it.value.length > 2 }
        if (hasRealWord) return false

        val withoutVariableLetters = withoutFunctionWords.replace(Regex("[A-Za-z]"), "")
        return MATH_SYMBOL_ONLY_PATTERN.matcher(withoutVariableLetters).matches()
    }

    private fun stripTagsAndPlaceholders(text: String): String {
        return text
            .replace(Regex("</?b\\d+>"), "")
            .replace(Regex("</?s[123]>"), "")
            .replace(Regex("\\{\\s*v\\d+\\s*\\}"), "")
    }

    /**
     * Groups raw text fragments into horizontal lines and runs, retaining superscript positioning.
     */
    private fun groupIntoLineRuns(raw: List<TextBlock>): List<TextBlock> {
        if (raw.isEmpty()) return emptyList()

        val lines = mutableListOf<MutableList<TextBlock>>()
        for (frag in raw) {
            val lastLine = lines.lastOrNull()
            if (lastLine != null) {
                val refFrag = lastLine.maxByOrNull { it.fontSize } ?: lastLine.last()
                val maxFontSize = maxOf(refFrag.fontSize, frag.fontSize)
                if (abs(frag.y - refFrag.y) <= maxFontSize * 0.9f) {
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
        private val SUPERSCRIPT_DIGIT_MAP = mapOf(
            '0' to '⁰', '1' to '¹', '2' to '²', '3' to '³', '4' to '⁴',
            '5' to '⁵', '6' to '⁶', '7' to '⁷', '8' to '⁸', '9' to '⁹',
            '+' to '⁺', '-' to '⁻'
        )

        fun toSuperscriptToken(raw: String): String {
            val trimmed = raw.trim()
            if (trimmed.isEmpty()) return trimmed
            return trimmed.map { SUPERSCRIPT_DIGIT_MAP[it] ?: it }.joinToString("")
        }
    }

    /**
     * Combines line fragments into a single text block, handling superscript formatting for exponents.
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
            val piece = if (isSuperscript) toSuperscriptToken(frag.text) else frag.text
            when {
                index == 0 -> sb.append(piece)
                isSuperscript -> sb.append(piece)
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
            if (char.code < 32) continue
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

    private class TextBlock(
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
                    val ch = tp.unicode ?: ""
                    if (ch.isEmpty()) continue

                    // Check if character is a superscript exponent
                    val isSuperscript = i > 0 &&
                        (tp.fontSizeInPt <= baseFontSize * 0.85f || tp.yDirAdj < refDirAdj - baseFontSize * 0.12f)

                    if (isSuperscript) {
                        sb.append(toSuperscriptToken(ch))
                    } else {
                        sb.append(ch)
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

    private class NullWriter : java.io.Writer() {
        override fun write(cbuf: CharArray, off: Int, len: Int) {}
        override fun flush() {}
        override fun close() {}
    }
}
