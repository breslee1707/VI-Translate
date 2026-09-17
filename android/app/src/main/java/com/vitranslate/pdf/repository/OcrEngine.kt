package com.vitranslate.pdf.repository

import android.graphics.Bitmap
import android.graphics.RectF
import android.util.Log
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.Text
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

data class OcrTextLine(
    val text: String,
    val boundingBox: RectF? = null,
    val confidence: Float = 1.0f
)

data class OcrResult(
    val fullText: String,
    val lines: List<OcrTextLine>
)

object OcrEngine {
    private const val TAG = "OcrEngine"

    suspend fun extractTextFromBitmap(bitmap: Bitmap): Result<OcrResult> = runCatching {
        try {
            val inputImage = InputImage.fromBitmap(bitmap, 0)
            val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
            val visionText: Text = suspendCancellableCoroutine { continuation ->
                recognizer.process(inputImage)
                    .addOnSuccessListener { if (continuation.isActive) continuation.resume(it) }
                    .addOnFailureListener { if (continuation.isActive) continuation.resumeWithException(it) }
            }

            val lines = visionText.textBlocks.flatMap { block ->
                block.lines.map { OcrTextLine(it.text, it.boundingBox?.let { rect -> RectF(rect) }) }
            }
            OcrResult(visionText.text, lines)
        } catch (e: Throwable) {
            Log.w(TAG, "OCR recognition failed: ${e.message}", e)
            OcrResult("", emptyList())
        }
    }
}
