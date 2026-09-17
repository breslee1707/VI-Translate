package com.vitranslate.advancedengine

import android.app.Activity
import android.os.Bundle
import android.widget.TextView

class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val tv = TextView(this).apply {
            text = "VI-Translate Advanced Translation Engine Addon\nStatus: Ready for IPC calls"
            textSize = 18f
            setPadding(32, 32, 32, 32)
        }
        setContentView(tv)
    }
}
