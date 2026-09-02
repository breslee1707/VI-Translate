package com.vitranslate.pdf

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Does the app open?
 *
 * Unit tests and lint both passed on a build that crashed the moment it was
 * launched: the header asked Compose's painterResource for R.mipmap.ic_launcher,
 * which on API 26+ resolves to the <adaptive-icon> XML that painterResource
 * cannot read. Nothing that runs on the JVM or reads source can catch that —
 * only starting the activity can.
 *
 * This test composes the real screen, so a resource that fails to load, a theme
 * that fails to resolve or a crash in the first composition all fail here.
 */
@RunWith(AndroidJUnit4::class)
class LaunchSmokeTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun theMainScreenComposes() {
        composeRule.onNodeWithText("PDF Translate").assertIsDisplayed()
    }

    @Test
    fun theQueueAndActionAreOnScreen() {
        composeRule.onNodeWithText("Hàng đợi").assertIsDisplayed()
        composeRule.onNodeWithText("Bắt đầu dịch").assertIsDisplayed()
    }
}
