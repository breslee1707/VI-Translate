package com.vitranslate.pdf.repository

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.documentfile.provider.DocumentFile
import com.vitranslate.pdf.model.QueueItem
import com.vitranslate.pdf.model.TargetLanguage
import com.vitranslate.pdf.model.TranslationStatus
import com.vitranslate.pdf.model.UpdateInfo
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

/**
 * Owns the queue, the settings and the translation run.
 *
 * This used to live in the ViewModel, where the work was tied to the Activity:
 * leaving the app or letting the screen turn off killed a translation halfway
 * through. The state is process-scoped now so `TranslationService` can drive the
 * same run in the foreground while the ViewModel only observes it.
 */
object TranslationController {

    private const val PREFS_NAME = "pdf_translate_prefs"
    private const val KEY_OVERWRITE = "overwrite_existing"
    private const val KEY_OUTPUT_DIR = "custom_output_dir"

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private var appContext: Context? = null
    private var preserver: PdfLayoutPreserver? = null
    private var initialised = false

    @Volatile
    private var cancelRequested = false

    private val _queueItems = MutableStateFlow<List<QueueItem>>(emptyList())
    val queueItems: StateFlow<List<QueueItem>> = _queueItems.asStateFlow()

    private val _selectedLanguage =
        MutableStateFlow(TargetLanguage.getByCode(TargetLanguage.DEFAULT_CODE))
    val selectedLanguage: StateFlow<TargetLanguage> = _selectedLanguage.asStateFlow()

    private val _overwrite = MutableStateFlow(false)
    val overwrite: StateFlow<Boolean> = _overwrite.asStateFlow()

    private val _customOutputDirectory = MutableStateFlow<String?>(null)
    val customOutputDirectory: StateFlow<String?> = _customOutputDirectory.asStateFlow()

    private val _isTranslating = MutableStateFlow(false)
    val isTranslating: StateFlow<Boolean> = _isTranslating.asStateFlow()

    private val _progress = MutableStateFlow(0f)
    val progress: StateFlow<Float> = _progress.asStateFlow()

    private val _isIndeterminate = MutableStateFlow(false)
    val isIndeterminate: StateFlow<Boolean> = _isIndeterminate.asStateFlow()

    private val _statusText = MutableStateFlow("Chưa có file nào")
    val statusText: StateFlow<String> = _statusText.asStateFlow()

    private val _lastOutputDirectory = MutableStateFlow<String?>(null)
    val lastOutputDirectory: StateFlow<String?> = _lastOutputDirectory.asStateFlow()

    private val _updateInfo = MutableStateFlow<UpdateInfo?>(null)
    val updateInfo: StateFlow<UpdateInfo?> = _updateInfo.asStateFlow()

    /** File being worked on right now, for the foreground notification. */
    private val _activeFileName = MutableStateFlow<String?>(null)
    val activeFileName: StateFlow<String?> = _activeFileName.asStateFlow()

    fun initialise(context: Context) {
        if (initialised) return
        initialised = true
        val application = context.applicationContext
        appContext = application
        preserver = PdfLayoutPreserver(application)
        val prefs = application.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        _overwrite.value = prefs.getBoolean(KEY_OVERWRITE, false)
        _customOutputDirectory.value = prefs.getString(KEY_OUTPUT_DIR, null)
        scope.launch {
            val info = UpdateChecker().checkForUpdate()
            if (info != null && info.isNewerAvailable) {
                _updateInfo.value = info
            }
        }
    }

    private fun requireContext(): Context =
        appContext ?: error("TranslationController.initialise was never called")

    private fun prefs() =
        requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    // ---------------------------------------------------------------- settings

    fun setSelectedLanguage(language: TargetLanguage) {
        _selectedLanguage.value = language
        resetSkippedItems()
    }

    fun setOverwrite(value: Boolean) {
        _overwrite.value = value
        prefs().edit().putBoolean(KEY_OVERWRITE, value).apply()
        resetSkippedItems()
    }

    fun setCustomOutputDirectory(path: String?) {
        _customOutputDirectory.value = path
        prefs().edit().putString(KEY_OUTPUT_DIR, path).apply()
        resetSkippedItems()
    }

    // ------------------------------------------------------------------- queue

    fun addFiles(uris: List<Uri>) {
        val currentList = _queueItems.value.toMutableList()
        for (uri in uris) {
            val name = fileName(uri) ?: continue
            if (!name.endsWith(".pdf", ignoreCase = true)) continue
            if (currentList.any { it.uri == uri }) continue
            currentList.add(QueueItem(id = UUID.randomUUID().toString(), uri = uri, name = name))
        }
        _queueItems.value = currentList
        refreshStatusText()
    }

    fun addDirectory(treeUri: Uri) {
        val docTree = DocumentFile.fromTreeUri(requireContext(), treeUri) ?: return
        val pdfFiles = mutableListOf<Uri>()
        collectPdfsRecursively(docTree, pdfFiles)
        addFiles(pdfFiles)
    }

    private fun collectPdfsRecursively(dir: DocumentFile, result: MutableList<Uri>) {
        for (file in dir.listFiles()) {
            if (file.isDirectory) {
                collectPdfsRecursively(file, result)
            } else if (file.name?.endsWith(".pdf", ignoreCase = true) == true) {
                result.add(file.uri)
            }
        }
    }

    fun removeItem(id: String) {
        if (_isTranslating.value) return
        _queueItems.value = _queueItems.value.filter { it.id != id }
        refreshStatusText()
    }

    fun clearQueue() {
        if (_isTranslating.value) return
        _queueItems.value = emptyList()
        _lastOutputDirectory.value = null
        refreshStatusText()
    }

    private fun resetSkippedItems() {
        if (_isTranslating.value) return
        _queueItems.value = _queueItems.value.map { item ->
            if (item.status == TranslationStatus.SKIPPED) {
                item.copy(status = TranslationStatus.QUEUED, detail = "")
            } else {
                item
            }
        }
        refreshStatusText()
    }

    private fun refreshStatusText() {
        val count = _queueItems.value.size
        _statusText.value = if (count > 0) "$count file trong hàng đợi" else "Chưa có file nào"
    }

    fun hasPendingWork(): Boolean = _queueItems.value.any { it.isPending }

    /** Says so in the status line rather than starting a service with no work. */
    fun reportNothingToDo() {
        _statusText.value = "Không còn file nào cần dịch"
    }

    // --------------------------------------------------------------------- run

    /**
     * Works through the queue. Driven by the foreground service rather than the
     * UI, so a translation keeps going once the app is in the background.
     */
    suspend fun runQueue() {
        if (_isTranslating.value) return
        val pending = _queueItems.value.filter { it.isPending }
        if (pending.isEmpty()) {
            _statusText.value = "Không còn file nào cần dịch"
            return
        }

        cancelRequested = false
        _isTranslating.value = true
        _isIndeterminate.value = true
        _progress.value = 0f
        _statusText.value = "Đang chuẩn bị…"

        val engine = preserver ?: PdfLayoutPreserver(requireContext()).also { preserver = it }
        val targetOutputDir = _customOutputDirectory.value
        val totalFiles = pending.size
        var completedFiles = 0
        var cancelled = false

        try {
            for (item in pending) {
                if (cancelRequested) {
                    cancelled = true
                    break
                }
                _activeFileName.value = item.name
                updateItemStatus(item.id, TranslationStatus.RUNNING, "Đang dịch…")

                try {
                    val result = engine.translatePdf(
                        inputUri = item.uri,
                        outputDirUriOrPath = targetOutputDir,
                        targetLang = _selectedLanguage.value.code,
                        overwrite = _overwrite.value,
                        onProgress = { donePages, totalPages ->
                            _isIndeterminate.value = false
                            val fileFraction =
                                if (totalPages > 0) donePages.toFloat() / totalPages else 0f
                            _progress.value = (completedFiles + fileFraction) / totalFiles
                            _statusText.value =
                                "Đang dịch ${item.name}   trang $donePages/$totalPages"
                            updateItemStatus(
                                item.id,
                                TranslationStatus.RUNNING,
                                "trang $donePages/$totalPages"
                            )
                        },
                        onLog = { appendLog(it) },
                        isCancelled = { cancelRequested }
                    )

                    val partial = result.untranslatedCount > 0
                    updateItemStatus(
                        item.id,
                        if (partial) TranslationStatus.PARTIAL else TranslationStatus.DONE,
                        if (partial) "${result.untranslatedCount} đoạn chưa dịch được" else "",
                        result.untranslatedCount,
                        result.outputPath
                    )
                    _lastOutputDirectory.value = result.outputPath
                } catch (_: TranslationCancelledException) {
                    // The half-written PDF has already been deleted. Put the file
                    // back in the queue so pressing Dịch again just restarts it.
                    updateItemStatus(item.id, TranslationStatus.QUEUED, "")
                    cancelled = true
                    break
                } catch (e: Exception) {
                    val errorMsg = e.message ?: "Translation error"
                    if (errorMsg.contains("already exists", ignoreCase = true)) {
                        appendLog("Bỏ qua file ${item.name}: $errorMsg")
                        updateItemStatus(
                            item.id,
                            TranslationStatus.SKIPPED,
                            "File đã tồn tại (Đã bỏ qua)"
                        )
                    } else {
                        logFailure(item.name, e)
                        updateItemStatus(item.id, TranslationStatus.FAILED, errorMsg)
                    }
                }

                completedFiles++
                _progress.value = completedFiles.toFloat() / totalFiles
            }
        } finally {
            _activeFileName.value = null
            _isTranslating.value = false
            _isIndeterminate.value = false
            cancelRequested = false
            _statusText.value = summarise(cancelled)
        }
    }

    /** Asks the run to stop at the next page or paragraph boundary. */
    fun requestCancel() {
        if (!_isTranslating.value) return
        cancelRequested = true
        _statusText.value = "Đang huỷ…"
    }

    private fun summarise(cancelled: Boolean): String {
        val counts = _queueItems.value.groupingBy { it.status }.eachCount()
        val doneCount = counts[TranslationStatus.DONE] ?: 0
        val partialCount = counts[TranslationStatus.PARTIAL] ?: 0
        val failedCount = counts[TranslationStatus.FAILED] ?: 0
        val total = _queueItems.value.size

        var summary = if (cancelled) {
            "Đã huỷ — xong $doneCount/$total file"
        } else {
            "Xong $doneCount/$total file"
        }
        if (partialCount > 0) summary += ", $partialCount file dịch thiếu"
        if (failedCount > 0) summary += ", $failedCount file lỗi"
        return summary
    }

    private fun updateItemStatus(
        id: String,
        status: TranslationStatus,
        detail: String,
        untranslated: Int = 0,
        outputPath: String? = null
    ) {
        _queueItems.value = _queueItems.value.map { item ->
            if (item.id == id) {
                item.copy(
                    status = status,
                    detail = detail,
                    untranslated = untranslated,
                    outputPath = outputPath ?: item.outputPath
                )
            } else {
                item
            }
        }
    }

    // --------------------------------------------------------------------- log

    private fun logFile(): File {
        val logDir = File(requireContext().getExternalFilesDir(null), "translated")
        logDir.mkdirs()
        return File(logDir, "pdf-translate.log")
    }

    fun appendLog(message: String) {
        try {
            FileWriter(logFile(), true).use { writer ->
                writer.write("[${timestamp()}] $message\n")
            }
        } catch (_: Exception) {
        }
    }

    private fun logFailure(sourceName: String, error: Throwable) {
        try {
            FileWriter(logFile(), true).use { writer ->
                writer.write("\n======================================================================\n")
                writer.write("[${timestamp()}] ERROR: $sourceName\n")
                writer.write(error.stackTraceToString())
                writer.write("\n======================================================================\n")
            }
        } catch (_: Exception) {
        }
    }

    fun logContent(): String = try {
        val file = logFile()
        if (file.exists()) file.readText() else "Chưa có log nhật ký nào."
    } catch (e: Exception) {
        "Lỗi khi đọc file log: ${e.message}"
    }

    fun clearLog() {
        try {
            val file = logFile()
            if (file.exists()) file.delete()
        } catch (_: Exception) {
        }
    }

    private fun timestamp(): String =
        SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date())

    private fun fileName(uri: Uri): String? {
        var name: String? = null
        try {
            requireContext().contentResolver.query(uri, null, null, null, null)?.use { cursor ->
                val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (nameIndex != -1 && cursor.moveToFirst()) {
                    name = cursor.getString(nameIndex)
                }
            }
        } catch (_: Exception) {
        }
        return name ?: uri.lastPathSegment
    }

    private val QueueItem.isPending: Boolean
        get() = status == TranslationStatus.QUEUED ||
            status == TranslationStatus.FAILED ||
            status == TranslationStatus.SKIPPED
}
