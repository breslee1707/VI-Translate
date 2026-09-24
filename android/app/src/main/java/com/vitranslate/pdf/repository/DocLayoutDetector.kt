package com.vitranslate.pdf.repository

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.pdmodel.PDPage
import com.tom_roush.pdfbox.rendering.PDFRenderer
import java.io.File
import java.nio.FloatBuffer
import kotlin.math.max

data class DetectedLayoutRegion(
    val category: String,
    val confidence: Float,
    val x0: Float,
    val y0: Float,
    val x1: Float,
    val y1: Float
)

class DocLayoutDetector(private val modelFile: File) : AutoCloseable {

    private val env: OrtEnvironment = OrtEnvironment.getEnvironment()
    private val session: OrtSession = env.createSession(modelFile.absolutePath)

    /**
     * Preprocesses a PDF page and runs doclayout.onnx YOLOv8 layout inference.
     */
    fun detectLayout(
        document: PDDocument,
        pageIndex: Int,
        pageWidth: Float,
        pageHeight: Float
    ): List<DetectedLayoutRegion> {
        val renderer = PDFRenderer(document)
        val bitmap = try {
            renderer.renderImage(pageIndex, 1.5f)
        } catch (_: Exception) {
            return emptyList()
        }

        val resizedBitmap = Bitmap.createScaledBitmap(bitmap, 1024, 1024, true)
        val floatBuffer = FloatBuffer.allocate(1 * 3 * 1024 * 1024)

        // CHW format normalization
        val pixels = IntArray(1024 * 1024)
        resizedBitmap.getPixels(pixels, 0, 1024, 0, 0, 1024, 1024)

        // Red channel
        for (pixel in pixels) {
            floatBuffer.put(Color.red(pixel) / 255.0f)
        }
        // Green channel
        for (pixel in pixels) {
            floatBuffer.put(Color.green(pixel) / 255.0f)
        }
        // Blue channel
        for (pixel in pixels) {
            floatBuffer.put(Color.blue(pixel) / 255.0f)
        }

        floatBuffer.rewind()
        val shape = longArrayOf(1, 3, 1024, 1024)

        return try {
            val inputTensor = OnnxTensor.createTensor(env, floatBuffer, shape)
            inputTensor.use { tensor ->
                val results = session.run(mapOf(session.inputNames.iterator().next() to tensor))
                results.use {
                    parseYoloOutputs(it, pageWidth, pageHeight)
                }
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun parseYoloOutputs(
        outputResults: OrtSession.Result,
        pageWidth: Float,
        pageHeight: Float
    ): List<DetectedLayoutRegion> {
        val detected = mutableListOf<DetectedLayoutRegion>()
        if (outputResults.count() == 0) return detected

        val outputTensor = outputResults.get(0).value as? Array<Array<FloatArray>> ?: return detected
        // YOLO output tensor format [1, 84, 8400] or similar bounding box predictions
        val scaleX = pageWidth / 1024f
        val scaleY = pageHeight / 1024f

        for (pred in outputTensor[0]) {
            if (pred.size < 5) continue
            val confidence = pred[4]
            if (confidence < 0.45f) continue

            val cx = pred[0]
            val cy = pred[1]
            val w = pred[2]
            val h = pred[3]

            val x0 = max(0f, (cx - w / 2f) * scaleX)
            val y0 = max(0f, (cy - h / 2f) * scaleY)
            val x1 = (cx + w / 2f) * scaleX
            val y1 = (cy + h / 2f) * scaleY

            val category = when {
                pred.size > 5 && pred[5] > 0.5f -> "table"
                pred.size > 6 && pred[6] > 0.5f -> "figure"
                else -> "text"
            }

            detected.add(
                DetectedLayoutRegion(
                    category = category,
                    confidence = confidence,
                    x0 = x0,
                    y0 = y0,
                    x1 = x1,
                    y1 = y1
                )
            )
        }
        return detected
    }

    override fun close() {
        try {
            session.close()
            env.close()
        } catch (_: Exception) {}
    }

    companion object {
        fun createIfAvailable(context: Context): DocLayoutDetector? {
            val file = DocLayoutModelDownloader.getModelFile(context)
            if (!DocLayoutModelDownloader.isModelDownloaded(context)) return null
            return try {
                DocLayoutDetector(file)
            } catch (_: Exception) {
                null
            }
        }
    }
}
