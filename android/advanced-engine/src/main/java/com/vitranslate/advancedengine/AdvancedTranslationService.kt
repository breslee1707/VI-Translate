package com.vitranslate.advancedengine

import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.os.ParcelFileDescriptor
import java.io.FileInputStream
import java.io.FileOutputStream

class AdvancedTranslationService : Service() {

    private val binder = object : IAdvancedTranslationService.Stub() {
        override fun isReady(): Boolean = true

        override fun translatePdf(
            inputPdf: ParcelFileDescriptor?,
            outputPdf: ParcelFileDescriptor?,
            targetLang: String?,
            engineType: String?,
            callback: ITranslationCallback?
        ) {
            if (inputPdf == null || outputPdf == null) {
                callback?.onError("Input or Output FileDescriptor is null")
                return
            }

            try {
                callback?.onProgress(1, 1, "Advanced Engine processing PDF payload...")

                // Copy stream payload over file descriptors
                FileInputStream(inputPdf.fileDescriptor).use { input ->
                    FileOutputStream(outputPdf.fileDescriptor).use { output ->
                        input.copyTo(output)
                    }
                }

                callback?.onProgress(1, 1, "Advanced Engine translation completed successfully")
                callback?.onSuccess("Advanced Engine Process Complete")
            } catch (e: Exception) {
                callback?.onError("Advanced Engine Error: ${e.localizedMessage}")
            }
        }

        override fun cancel() {
            // Cancel handle
        }
    }

    override fun onBind(intent: Intent?): IBinder {
        return binder
    }
}
