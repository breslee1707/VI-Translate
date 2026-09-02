package com.vitranslate.pdf.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.vitranslate.pdf.MainActivity
import com.vitranslate.pdf.R
import com.vitranslate.pdf.repository.TranslationController
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

/**
 * Runs the queue in the foreground so Android does not kill a translation the
 * moment the user leaves the app or the screen turns off.
 *
 * The work itself lives in [TranslationController]; this class only keeps the
 * process alive, mirrors progress into a notification, and offers a Huỷ action.
 */
class TranslationService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var runJob: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        TranslationController.initialise(this)
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_CANCEL -> {
                TranslationController.requestCancel()
                return START_NOT_STICKY
            }
        }

        // startForeground has to happen within a few seconds of the start call,
        // before any of the slow work begins.
        startInForeground(buildNotification("Đang chuẩn bị…", null))

        if (runJob?.isActive == true) return START_NOT_STICKY

        observeProgress()
        runJob = scope.launch {
            try {
                TranslationController.runQueue()
            } finally {
                stopForegroundCompat()
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        // If the system tears us down mid-run, stop the work rather than let it
        // continue unsupervised with no notification and no way to cancel.
        TranslationController.requestCancel()
        scope.cancel()
        super.onDestroy()
    }

    private fun observeProgress() {
        scope.launch {
            combine(
                TranslationController.statusText,
                TranslationController.activeFileName,
                TranslationController.progress,
                TranslationController.isIndeterminate,
                TranslationController.isTranslating
            ) { status, file, progress, indeterminate, translating ->
                Progress(status, file, progress, indeterminate, translating)
            }.collect { snapshot ->
                if (!snapshot.translating) return@collect
                val percent = (snapshot.progress * 100).toInt().coerceIn(0, 100)
                notify(
                    buildNotification(
                        text = snapshot.file ?: snapshot.status,
                        progress = if (snapshot.indeterminate) null else percent
                    )
                )
            }
        }
    }

    private data class Progress(
        val status: String,
        val file: String?,
        val progress: Float,
        val indeterminate: Boolean,
        val translating: Boolean
    )

    private fun buildNotification(text: String, progress: Int?): Notification {
        val openApp = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val cancel = PendingIntent.getService(
            this,
            1,
            Intent(this, TranslationService::class.java).setAction(ACTION_CANCEL),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.notification_translating_title))
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentIntent(openApp)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .apply {
                if (progress == null) {
                    setProgress(0, 0, true)
                } else {
                    setProgress(100, progress, false)
                }
            }
            .addAction(0, getString(R.string.notification_cancel), cancel)
            .build()
    }

    private fun startInForeground(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun stopForegroundCompat() {
        stopForeground(STOP_FOREGROUND_REMOVE)
    }

    private fun notify(notification: Notification) {
        // Posting is a no-op without the runtime permission on API 33+; the work
        // must carry on regardless, so this is never treated as an error.
        try {
            NotificationManagerCompat.from(this).notify(NOTIFICATION_ID, notification)
        } catch (_: SecurityException) {
        }
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notification_channel_name),
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = getString(R.string.notification_channel_description)
                setShowBadge(false)
            }
        )
    }

    companion object {
        private const val CHANNEL_ID = "translation_progress"
        private const val NOTIFICATION_ID = 1001

        const val ACTION_START = "com.vitranslate.pdf.action.START_TRANSLATION"
        const val ACTION_CANCEL = "com.vitranslate.pdf.action.CANCEL_TRANSLATION"

        fun start(context: Context) {
            val intent = Intent(context, TranslationService::class.java)
                .setAction(ACTION_START)
            context.startForegroundService(intent)
        }

        /**
         * The controller is a process singleton, so the running service sees the
         * flag without an intent. Sending one would risk starting the service
         * again just to stop it if the run had already finished.
         */
        fun cancel() {
            TranslationController.requestCancel()
        }
    }
}
