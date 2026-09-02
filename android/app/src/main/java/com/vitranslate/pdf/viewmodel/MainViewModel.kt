package com.vitranslate.pdf.viewmodel

import android.app.Application
import android.net.Uri
import android.provider.OpenableColumns
import androidx.documentfile.provider.DocumentFile
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.vitranslate.pdf.model.QueueItem
import com.vitranslate.pdf.model.TargetLanguage
import com.vitranslate.pdf.model.TranslationStatus
import com.vitranslate.pdf.model.UpdateInfo
import com.vitranslate.pdf.repository.PdfLayoutPreserver
import com.vitranslate.pdf.repository.UpdateChecker
import kotlinx.coroutines.Dispatchers
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

class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val preserver = PdfLayoutPreserver(application)
    private val updateChecker = UpdateChecker()

    private val _queueItems = MutableStateFlow<List<QueueItem>>(emptyList())
    val queueItems: StateFlow<List<QueueItem>> = _queueItems.asStateFlow()

    private val _selectedLanguage = MutableStateFlow(TargetLanguage.getByCode(TargetLanguage.DEFAULT_CODE))
    val selectedLanguage: StateFlow<TargetLanguage> = _selectedLanguage.asStateFlow()

    private val prefs = application.getSharedPreferences("pdf_translate_prefs", android.content.Context.MODE_PRIVATE)

    private val _overwrite = MutableStateFlow(prefs.getBoolean("overwrite_existing", false))
    val overwrite: StateFlow<Boolean> = _overwrite.asStateFlow()

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

    private val _customOutputDirectory = MutableStateFlow<String?>(prefs.getString("custom_output_dir", null))
    val customOutputDirectory: StateFlow<String?> = _customOutputDirectory.asStateFlow()

    private val _updateInfo = MutableStateFlow<UpdateInfo?>(null)
    val updateInfo: StateFlow<UpdateInfo?> = _updateInfo.asStateFlow()

    init {
        checkForUpdates()
    }

    fun setCustomOutputDirectory(path: String?) {
        _customOutputDirectory.value = path
        prefs.edit().putString("custom_output_dir", path).apply()
        resetSkippedItems()
    }

    private fun checkForUpdates() {
        viewModelScope.launch {
            val info = updateChecker.checkForUpdate()
            if (info != null && info.isNewerAvailable) {
                _updateInfo.value = info
            }
        }
    }

    fun setSelectedLanguage(language: TargetLanguage) {
        _selectedLanguage.value = language
        resetSkippedItems()
    }

    fun setOverwrite(value: Boolean) {
        _overwrite.value = value
        prefs.edit().putBoolean("overwrite_existing", value).apply()
        resetSkippedItems()
    }

    private fun resetSkippedItems() {
        if (!_isTranslating.value) {
            _queueItems.value = _queueItems.value.map { item ->
                if (item.status == TranslationStatus.SKIPPED) {
                    item.copy(status = TranslationStatus.QUEUED, detail = "")
                } else {
                    item
                }
            }
            refreshStatusText()
        }
    }

    fun addFiles(uris: List<Uri>) {
        val currentList = _queueItems.value.toMutableList()
        for (uri in uris) {
            val name = getFileName(uri) ?: continue
            if (!name.endsWith(".pdf", ignoreCase = true)) continue
            if (currentList.any { it.uri == uri }) continue

            currentList.add(
                QueueItem(
                    id = UUID.randomUUID().toString(),
                    uri = uri,
                    name = name
                )
            )
        }
        _queueItems.value = currentList
        refreshStatusText()
    }

    fun addDirectory(treeUri: Uri) {
        val docTree = DocumentFile.fromTreeUri(getApplication(), treeUri) ?: return
        val pdfFiles = mutableListOf<Uri>()
        collectPdfsRecursively(docTree, pdfFiles)
        addFiles(pdfFiles)
    }

    private fun collectPdfsRecursively(dir: DocumentFile, result: MutableList<Uri>) {
        val files = dir.listFiles()
        for (file in files) {
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

    private fun refreshStatusText() {
        val count = _queueItems.value.size
        _statusText.value = if (count > 0) "$count file trong hàng đợi" else "Chưa có file nào"
    }

    fun startTranslation() {
        if (_isTranslating.value) return
        val pending = _queueItems.value.filter { 
            it.status == TranslationStatus.QUEUED || 
            it.status == TranslationStatus.FAILED || 
            it.status == TranslationStatus.SKIPPED 
        }
        if (pending.isEmpty()) {
            _statusText.value = "Không còn file nào cần dịch"
            return
        }

        _isTranslating.value = true
        _isIndeterminate.value = true
        _statusText.value = "Đang chuẩn bị…"

        val targetOutputDir = _customOutputDirectory.value

        viewModelScope.launch(Dispatchers.IO) {
            val totalFiles = pending.size
            var completedFiles = 0

            for ((index, item) in pending.withIndex()) {
                updateItemStatus(item.id, TranslationStatus.RUNNING, "Đang dịch…")

                try {
                    val result = preserver.translatePdf(
                        inputUri = item.uri,
                        outputDirUriOrPath = targetOutputDir,
                        targetLang = _selectedLanguage.value.code,
                        overwrite = _overwrite.value,
                        onProgress = { donePages, totalPages ->
                            _isIndeterminate.value = false
                            val fileFraction = if (totalPages > 0) donePages.toFloat() / totalPages else 0f
                            val totalFraction = (completedFiles + fileFraction) / totalFiles
                            _progress.value = totalFraction
                            _statusText.value = "Đang dịch ${item.name}   trang $donePages/$totalPages"
                            updateItemStatus(item.id, TranslationStatus.RUNNING, "trang $donePages/$totalPages")
                        },
                        onLog = { appendLog(it) }
                    )

                    val finalStatus = if (result.untranslatedCount > 0) TranslationStatus.PARTIAL else TranslationStatus.DONE
                    val detail = if (result.untranslatedCount > 0) "${result.untranslatedCount} đoạn chưa dịch được" else ""

                    updateItemStatus(item.id, finalStatus, detail, result.untranslatedCount, result.outputPath)
                    _lastOutputDirectory.value = result.outputPath
                } catch (e: Exception) {
                    val errorMsg = e.message ?: "Translation error"
                    val isSkipped = errorMsg.contains("already exists", ignoreCase = true)
                    val finalStatus = if (isSkipped) TranslationStatus.SKIPPED else TranslationStatus.FAILED

                    val displayDetail = if (isSkipped) "File đã tồn tại (Đã bỏ qua)" else errorMsg

                    val logDir = File(getApplication<Application>().getExternalFilesDir(null), "translated")
                    if (finalStatus == TranslationStatus.FAILED) {
                        logFailure(logDir, item.name, e)
                    } else {
                        appendLog("Bỏ qua file ${item.name}: $displayDetail")
                    }
                    updateItemStatus(item.id, finalStatus, displayDetail)
                }

                completedFiles++
                _progress.value = completedFiles.toFloat() / totalFiles
            }

            _isTranslating.value = false
            _isIndeterminate.value = false

            val counts = _queueItems.value.groupingBy { it.status }.eachCount()
            val doneCount = counts[TranslationStatus.DONE] ?: 0
            val partialCount = counts[TranslationStatus.PARTIAL] ?: 0
            val failedCount = counts[TranslationStatus.FAILED] ?: 0

            var summary = "Xong $doneCount/${_queueItems.value.size} file"
            if (partialCount > 0) summary += ", $partialCount file dịch thiếu"
            if (failedCount > 0) summary += ", $failedCount file lỗi"

            _statusText.value = summary
        }
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

    fun appendLog(message: String) {
        try {
            val logDir = File(getApplication<Application>().getExternalFilesDir(null), "translated")
            logDir.mkdirs()
            val logFile = File(logDir, "pdf-translate.log")
            val timeStr = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date())
            FileWriter(logFile, true).use { writer ->
                writer.write("[$timeStr] $message\n")
            }
        } catch (_: Exception) {}
    }

    fun getLogContent(): String {
        return try {
            val defaultLogDir = File(getApplication<Application>().getExternalFilesDir(null), "translated")
            val logFile = File(defaultLogDir, "pdf-translate.log")
            if (logFile.exists()) logFile.readText() else "Chưa có log nhật ký nào."
        } catch (e: Exception) {
            "Lỗi khi đọc file log: ${e.message}"
        }
    }

    fun clearLog() {
        try {
            val defaultLogDir = File(getApplication<Application>().getExternalFilesDir(null), "translated")
            val logFile = File(defaultLogDir, "pdf-translate.log")
            if (logFile.exists()) logFile.delete()
        } catch (_: Exception) {}
    }

    private fun logFailure(outputDir: File, sourceName: String, error: Throwable) {
        try {
            outputDir.mkdirs()
            val logFile = File(outputDir, "pdf-translate.log")
            FileWriter(logFile, true).use { writer ->
                val timeStr = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date())
                writer.write("\n======================================================================\n")
                writer.write("[$timeStr] ERROR: $sourceName\n")
                writer.write(error.stackTraceToString())
                writer.write("\n======================================================================\n")
            }
        } catch (_: Exception) {}
    }

    private fun getFileName(uri: Uri): String? {
        var name: String? = null
        getApplication<Application>().contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (nameIndex != -1 && cursor.moveToFirst()) {
                name = cursor.getString(nameIndex)
            }
        }
        return name ?: uri.lastPathSegment
    }
}
