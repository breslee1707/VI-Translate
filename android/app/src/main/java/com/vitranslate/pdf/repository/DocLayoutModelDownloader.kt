package com.vitranslate.pdf.repository

import android.content.Context
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

object DocLayoutModelDownloader {

    private const val MODEL_FILENAME = "doclayout.onnx"
    private val MODEL_URLS = listOf(
        "https://huggingface.co/breezedeus/pix2text-layout/resolve/main/doclayout.onnx",
        "https://huggingface.co/layoutlm/doclayout/resolve/main/doclayout.onnx",
        "https://github.com/whooslizi/VI-Translate/releases/download/android-v0.3.1/doclayout.onnx"
    )

    fun getModelFile(context: Context): File {
        return File(context.filesDir, MODEL_FILENAME)
    }

    fun isModelDownloaded(context: Context): Boolean {
        val file = getModelFile(context)
        return file.exists() && file.length() > 10000000L // > 10MB
    }

    suspend fun downloadModel(
        context: Context,
        modelUrl: String? = null,
        onProgress: (percent: Int) -> Unit
    ): File? {
        val targetFile = getModelFile(context)
        if (isModelDownloaded(context)) {
            onProgress(100)
            return targetFile
        }

        val urlsToTry = if (!modelUrl.isNullOrBlank()) listOf(modelUrl) + MODEL_URLS else MODEL_URLS
        val client = OkHttpClient.Builder()
            .followRedirects(true)
            .followSslRedirects(true)
            .build()

        for (url in urlsToTry) {
            val request = Request.Builder().url(url).build()
            try {
                val response = client.newCall(request).execute()
                if (!response.isSuccessful) continue

                val body = response.body ?: continue
                val contentLength = body.contentLength()
                val tempFile = File(context.filesDir, "$MODEL_FILENAME.tmp")

                body.byteStream().use { inputStream ->
                    FileOutputStream(tempFile).use { outputStream ->
                        val buffer = ByteArray(8192)
                        var bytesRead: Int
                        var totalRead = 0L

                        while (inputStream.read(buffer).also { bytesRead = it } != -1) {
                            outputStream.write(buffer, 0, bytesRead)
                            totalRead += bytesRead
                            if (contentLength > 0) {
                                val percent = ((totalRead * 100) / contentLength).toInt()
                                onProgress(percent)
                            }
                        }
                    }
                }

                if (tempFile.renameTo(targetFile)) {
                    return targetFile
                }
            } catch (_: IOException) {
                // Try next URL
            }
        }
        return null
    }
}
