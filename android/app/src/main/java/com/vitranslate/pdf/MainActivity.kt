package com.vitranslate.pdf

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import com.vitranslate.pdf.ui.components.*
import com.vitranslate.pdf.ui.theme.PDFTranslateTheme
import com.vitranslate.pdf.viewmodel.MainViewModel

class MainActivity : ComponentActivity() {

    private val viewModel: MainViewModel by viewModels()

    private val pickFilesLauncher = registerForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments()
    ) { uris ->
        if (uris.isNotEmpty()) {
            uris.forEach { uri ->
                try {
                    contentResolver.takePersistableUriPermission(
                        uri,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION
                    )
                } catch (_: Exception) {}
            }
            viewModel.addFiles(uris)
        }
    }

    private val pickDirectoryLauncher = registerForActivityResult(
        ActivityResultContracts.OpenDocumentTree()
    ) { treeUri ->
        if (treeUri != null) {
            try {
                contentResolver.takePersistableUriPermission(
                    treeUri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
            } catch (_: Exception) {}
            viewModel.addDirectory(treeUri)
        }
    }

    private val pickSaveDirectoryLauncher = registerForActivityResult(
        ActivityResultContracts.OpenDocumentTree()
    ) { treeUri ->
        if (treeUri != null) {
            try {
                contentResolver.takePersistableUriPermission(
                    treeUri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                )
            } catch (_: Exception) {}
            val path = treeUri.toString()
            viewModel.setCustomOutputDirectory(path)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Handle incoming PDF shared intent
        intent?.let { handleIntent(it) }

        setContent {
            PDFTranslateTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    MainScreen(
                        viewModel = viewModel,
                        onPickFiles = { pickFilesLauncher.launch(arrayOf("application/pdf")) },
                        onPickDirectory = { pickDirectoryLauncher.launch(null) },
                        onPickSaveDirectory = { pickSaveDirectoryLauncher.launch(null) }
                    )
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent) {
        val action = intent.action
        val type = intent.type
        if (type == "application/pdf") {
            if (Intent.ACTION_VIEW == action) {
                intent.data?.let { uri -> viewModel.addFiles(listOf(uri)) }
            } else if (Intent.ACTION_SEND == action) {
                (intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM))?.let { uri ->
                    viewModel.addFiles(listOf(uri))
                }
            }
        }
    }
}

@Composable
fun MainScreen(
    viewModel: MainViewModel,
    onPickFiles: () -> Unit,
    onPickDirectory: () -> Unit,
    onPickSaveDirectory: () -> Unit
) {
    val queueItems by viewModel.queueItems.collectAsState()
    val selectedLanguage by viewModel.selectedLanguage.collectAsState()
    val overwrite by viewModel.overwrite.collectAsState()
    val customSaveDirectory by viewModel.customOutputDirectory.collectAsState()
    val isTranslating by viewModel.isTranslating.collectAsState()
    val progress by viewModel.progress.collectAsState()
    val isIndeterminate by viewModel.isIndeterminate.collectAsState()
    val statusText by viewModel.statusText.collectAsState()
    val lastOutputDirectory by viewModel.lastOutputDirectory.collectAsState()
    val updateInfo by viewModel.updateInfo.collectAsState()

    var showLogDialog by remember { mutableStateOf(false) }
    var showAboutDialog by remember { mutableStateOf(false) }
    var currentLogText by remember { mutableStateOf("") }

    if (showLogDialog) {
        LogViewerDialog(
            logText = currentLogText,
            onDismiss = { showLogDialog = false },
            onClearLog = {
                viewModel.clearLog()
                currentLogText = ""
            }
        )
    }

    if (showAboutDialog) {
        AboutDialog(
            appVersion = "1.9.11",
            onDismiss = { showAboutDialog = false }
        )
    }

    Column(
        modifier = Modifier.fillMaxSize()
    ) {
        HeaderView(
            appVersion = "1.9.11",
            updateInfo = updateInfo,
            onShowLog = {
                currentLogText = viewModel.getLogContent()
                showLogDialog = true
            },
            onShowAbout = {
                showAboutDialog = true
            }
        )

        DropZoneView(
            hasFiles = queueItems.isNotEmpty(),
            onPickFiles = onPickFiles,
            onPickDirectory = onPickDirectory
        )

        ControlsView(
            selectedLanguage = selectedLanguage,
            onLanguageSelected = { viewModel.setSelectedLanguage(it) },
            overwrite = overwrite,
            onOverwriteChange = { viewModel.setOverwrite(it) },
            customSaveDirectory = customSaveDirectory,
            onPickSaveDirectory = onPickSaveDirectory,
            isTranslating = isTranslating,
            onStartTranslation = { viewModel.startTranslation() }
        )

        QueueView(
            items = queueItems,
            isTranslating = isTranslating,
            onRemoveItem = { viewModel.removeItem(it) },
            onClearQueue = { viewModel.clearQueue() },
            modifier = Modifier.weight(1f)
        )

        FooterView(
            isTranslating = isTranslating,
            progress = progress,
            isIndeterminate = isIndeterminate,
            statusText = statusText,
            lastOutputDirectory = lastOutputDirectory,
            onShowLog = {
                currentLogText = viewModel.getLogContent()
                showLogDialog = true
            }
        )
    }
}
