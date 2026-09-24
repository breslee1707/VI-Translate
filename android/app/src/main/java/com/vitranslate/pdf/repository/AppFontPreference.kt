package com.vitranslate.pdf.repository

import android.content.Context
import androidx.compose.ui.text.font.FontFamily

object AppFontPreference {
    private const val PREFS_NAME = "app_font_prefs"
    private const val KEY_USE_DEVICE_FONT = "use_device_font"

    fun isUseDeviceFont(context: Context): Boolean {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return prefs.getBoolean(KEY_USE_DEVICE_FONT, false)
    }

    fun setUseDeviceFont(context: Context, useDeviceFont: Boolean) {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().putBoolean(KEY_USE_DEVICE_FONT, useDeviceFont).apply()
    }

    fun getFontFamily(context: Context): FontFamily {
        return if (isUseDeviceFont(context)) FontFamily.Default else FontFamily.Default
    }
}
