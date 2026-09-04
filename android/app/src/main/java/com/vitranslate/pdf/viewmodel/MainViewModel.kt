package com.vitranslate.pdf.viewmodel

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import com.vitranslate.pdf.model.TargetLanguage
import com.vitranslate.pdf.repository.TranslationController
import com.vitranslate.pdf.service.TranslationService

/**
 * A thin view over [TranslationController].
 *
 * The queue and the translation loop used to live here, which tied a run to the
 * Activity's lifetime. They now sit in a process-scoped controller driven by
 * [TranslationService], and this class only forwards the UI's intent and
 * republishes the controller's state.
 */
class MainViewModel(application: Application) : AndroidViewModel(application) {

    init {
        TranslationController.initialise(application)
    }

    val queueItems = TranslationController.queueItems
    val selectedLanguage = TranslationController.selectedLanguage
    val overwrite = TranslationController.overwrite
    val customOutputDirectory = TranslationController.customOutputDirectory
    val isTranslating = TranslationController.isTranslating
    val progress = TranslationController.progress
    val isIndeterminate = TranslationController.isIndeterminate
    val statusText = TranslationController.statusText
    val lastOutputDirectory = TranslationController.lastOutputDirectory
    val updateInfo = TranslationController.updateInfo

    fun setSelectedLanguage(language: TargetLanguage) =
        TranslationController.setSelectedLanguage(language)

    fun setOverwrite(value: Boolean) = TranslationController.setOverwrite(value)

    fun setCustomOutputDirectory(path: String?) =
        TranslationController.setCustomOutputDirectory(path)

    fun addFiles(uris: List<Uri>) = TranslationController.addFiles(uris)

    fun addDirectory(treeUri: Uri) = TranslationController.addDirectory(treeUri)

    fun removeItem(id: String) = TranslationController.removeItem(id)

    fun clearQueue() = TranslationController.clearQueue()

    fun startTranslation() {
        if (isTranslating.value) return
        if (!TranslationController.hasPendingWork()) {
            TranslationController.reportNothingToDo()
            return
        }
        TranslationService.start(getApplication())
    }

    fun cancelTranslation() = TranslationService.cancel()

    fun getLogContent(): String = TranslationController.logContent()

    fun clearLog() = TranslationController.clearLog()
}
