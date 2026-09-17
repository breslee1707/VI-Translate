package com.vitranslate.pdf.repository

import android.content.Context

enum class AdvancedEngineStatus {
    READY,
    NOT_INSTALLED,
    INCOMPATIBLE,
    UNAVAILABLE
}

object AdvancedEngineManager {

    fun getEngineStatus(context: Context): AdvancedEngineStatus {
        return AdvancedEngineStatus.READY
    }

    fun isAddonInstalled(context: Context): Boolean {
        return true
    }
}
