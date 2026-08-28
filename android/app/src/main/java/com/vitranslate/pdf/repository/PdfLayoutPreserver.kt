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
        onProgress: (done: Int, total: Int) -> Unit
    ): TranslationResult {
        val originalFileName = getFileName(inputUri)
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
                    val font: PDFont = loadBundledFont(document)

                    for (pageIndex in 0 until totalPages) {
                        val page = document.getPage(pageIndex)
                        val textCollector = PageTextCollector()
                        textCollector.extractPageText(document, page, pageIndex)

                        // KEY FIX: merge granular word/phrase fragments into
                        // line/run-level blocks before translating & drawing.
                        val textBlocks = groupIntoLineRuns(textCollector.blocks)

                        if (textBlocks.isNotEmpty()) {
                            val translations = mutableListOf<BlockTranslation>()
                            for (block in textBlocks) {
                                val originalText = block.text.trim()
                                if (originalText.isBlank() || isPureMathOrFormula(originalText)) {
                                    continue
                                }
                                val encodedText = FormulaPlaceholder.encodeFormulaPlaceholders(originalText)
                                var translatedRaw = encodedText
                                var translationSuccess = false
                                try {
                                    translatedRaw = engine.translate(encodedText)
                                    translationSuccess = true
                                } catch (_: Exception) {
                                    untranslatedCount++
                                }
                                var translatedText = translatedRaw
                                if (translationSuccess) {
                                    try {
                                        translatedText = FormulaPlaceholder.restoreFormulaPlaceholders(originalText, translatedRaw)
                                    } catch (_: Exception) {
                                        translatedText = FormulaPlaceholder.removeControlCharacters(translatedRaw)
                                            .replace(Regex("</?b\\d+>"), "")
                                            .replace(Regex("</?s[123]>"), "")
                                    }
                                }
                                translations.add(BlockTranslation(block, translatedText))
                            }

                            if (translations.isNotEmpty()) {
                                PDPageContentStream(
                                    document,
                                    page,
                                    PDPageContentStream.AppendMode.APPEND,
                                    true,
                                    true
                                ).use { stream ->
                                    // Cover every full line/run box first, in one pass,
                                    // before any new text is drawn over the page.
                                    for (translation in translations) {
                                        coverSourceText(stream, translation.block)
                                    }
                                    for (translation in translations) {
                                        val block = translation.block
                                        val cleanedText = stripTagsAndPlaceholders(translation.translated)
                                        val text = sanitizeForFont(cleanedText, font)
                                        if (text.isBlank()) continue
                                        drawTextWithWrapping(stream, font, block, text)
                                    }
                                }
                            }
                        }
                        onProgress(pageIndex + 1, totalPages)
                    }
                    document.save(outStream)
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
                    val existing = docTree.findFile(outputFileName)
                    if (existing != null) {
                        if (!overwrite) {
                            throw Exception("Output file $outputFileName already exists")
                        }
                        existing.delete()
                    }
                    val newFile = docTree.createFile("application/pdf", outputFileName)
                    if (newFile != null) {
                        val outStream = context.contentResolver.openOutputStream(newFile.uri, "w")
                        if (outStream != null) {
                            return Pair(outStream, newFile.uri.toString())
                        }
                    }
                }
            } catch (e: Exception) {
                if (e.message?.contains("already exists") == true) throw e
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
        val outputFile = File(outputDir, outputFileName)
        if (outputFile.exists() && !overwrite) {
            throw Exception("Output file ${outputFile.name} already exists")
        }
        return Pair(FileOutputStream(outputFile), outputFile.absolutePath)
    }

    private fun isPureMathOrFormula(text: String): Boolean {
        val pureMathPattern = Pattern.compile("^[0-9+\\-*/=()<>\\[\\]{},._:;^√∫∑∞\\\\|\\s]+$")
        return pureMathPattern.matcher(text).matches()
    }

    private fun stripTagsAndPlaceholders(text: String): String {
        return text
            .replace(Regex("</?b\\d+>"), "")
            .replace(Regex("</?s[123]>"), "")
            .replace(Regex("\\{\\s*v\\d+\\s*\\}"), "")
    }

    /**
     * Groups word/phrase-level fragments produced by PDFTextStripper into
     * line-level runs:
     *  - Fragments are bucketed into a "line" when their y is within
     *    fontSize * 0.5 of the running line average.
     *  - Within a line, fragments are merged left-to-right into a single run
     *    as long as the horizontal gap between them looks like normal word
     *    spacing (<= 1.5x font size). A larger gap (tab stops, multi-column
     *    answer choices like "A. ... B. ...") starts a new run instead of
     *    merging, so unrelated columns don't get glued into one translation.
     */
    private fun groupIntoLineRuns(raw: List<TextBlock>): List<TextBlock> {
        if (raw.isEmpty()) return emptyList()

        val sorted = raw.sortedWith(compareByDescending<TextBlock> { it.y }.thenBy { it.x })
        val lines = mutableListOf<MutableList<TextBlock>>()

        for (frag in sorted) {
            val lastLine = lines.lastOrNull()
            val refY = lastLine?.let { line -> line.map { it.y }.average().toFloat() }
            if (lastLine != null && refY != null && abs(frag.y - refY) <= frag.fontSize * 0.5f) {
                lastLine.add(frag)
            } else {
                lines.add(mutableListOf(frag))
            }
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

    private fun mergeFragments(frags: List<TextBlock>): TextBlock {
        val sorted = frags.sortedBy { it.x }
        val combinedText = sorted.joinToString(" ") { it.text }
            .replace(Regex("\\s+"), " ")
            .trim()
        val minX = sorted.minOf { it.x }
        val maxRight = sorted.maxOf { it.x + it.width }
        val avgY = sorted.map { it.y }.average().toFloat()
        val maxAscent = sorted.maxOf { it.ascent }
        val maxDescent = sorted.maxOf { it.descent }
        val fontSize = sorted.map { it.fontSize }.average().toFloat()
        return TextBlock(
            text = combinedText,
            x = minX,
            y = avgY,
            fontSize = fontSize,
            width = maxRight - minX,
            ascent = maxAscent,
            descent = maxDescent
        )
    }

    private fun drawTextWithWrapping(
        stream: PDPageContentStream,
        font: PDFont,
        block: TextBlock,
        text: String
    ) {
        val baseFontSize = block.fontSize.coerceIn(6f, 72f)
        val availableWidth = maxOf(block.width, 30f)
        val minSingleLineFontSize = maxOf(baseFontSize * 0.6f, 5.5f)

        // 1) Try to fit on a single line, shrinking the font gradually first.
        //    This is preferred over wrapping since an extra line can collide
        //    with whatever content sits directly below this block.
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

        // 2) Fall back to wrapping at the smallest single-line font size.
        val wrapFontSize = minSingleLineFontSize
        val lines = wrapText(text, font, wrapFontSize, availableWidth)
        val effectiveFontSize = if (lines.size > 3) (wrapFontSize * 0.9f).coerceAtLeast(5f) else wrapFontSize
        val lineHeight = effectiveFontSize * 1.25f

        for ((index, line) in lines.withIndex()) {
            val lineY = block.y - (index * lineHeight)
            val sanitizedLine = sanitizeForFont(line, font)
            if (sanitizedLine.isBlank()) continue
            stream.beginText()
            stream.setFont(font, effectiveFontSize)
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
        return try {
            context.assets.open("fonts/NotoSans-Regular.ttf").use { fontStream ->
                PDType0Font.load(document, fontStream)
            }
        } catch (e: Exception) {
            throw Exception(
                "Failed to load bundled NotoSans font. " +
                    "Ensure fonts/NotoSans-Regular.ttf exists in assets.", e
            )
        }
    }

    private fun coverSourceText(
        stream: PDPageContentStream,
        block: TextBlock
    ) {
        val padX = 3.0f
        val padTop = 2.5f
        val padBottom = 2.5f
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
                sb.append(' ')
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

    private class PageTextCollector : PDFTextStripper() {
        val blocks = mutableListOf<TextBlock>()
        private var cropBox: PDRectangle = PDRectangle(0f, 0f, 612f, 792f)

        fun extractPageText(document: PDDocument, page: PDPage, pageIndex: Int) {
            cropBox = page.cropBox ?: page.mediaBox
            startPage = pageIndex + 1
            endPage = pageIndex + 1
            blocks.clear()
            writeText(document, NullWriter())
        }

        override fun writeString(text: String, textPositions: MutableList<TextPosition>) {
            if (textPositions.isEmpty()) return
            val first = textPositions.first()
            val last = textPositions.last()
            val x = cropBox.lowerLeftX + first.xDirAdj
            val y = cropBox.upperRightY - first.yDirAdj
            val fontSize = first.fontSizeInPt
            val width = (last.xDirAdj + last.widthDirAdj) - first.xDirAdj
            val ascent = fontSize * 0.95f
            val descent = fontSize * 0.35f
            blocks.add(TextBlock(text, x, y, fontSize, width, ascent, descent))
        }

        private class NullWriter : java.io.Writer() {
            override fun write(cbuf: CharArray, off: Int, len: Int) {}
            override fun flush() {}
            override fun close() {}
        }
    }
}
