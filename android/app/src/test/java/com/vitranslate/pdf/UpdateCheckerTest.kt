package com.vitranslate.pdf

import com.vitranslate.pdf.repository.UpdateChecker
import org.junit.Assert.*
import org.junit.Test

class UpdateCheckerTest {

    @Test
    fun testIsNewerVersion() {
        assertTrue(UpdateChecker.isNewer("3.0.1", "3.0.0"))
        assertTrue(UpdateChecker.isNewer("4.0.0", "3.9.11"))
        assertTrue(UpdateChecker.isNewer("v3.10.0", "3.9.11"))

        assertFalse(UpdateChecker.isNewer("3.0.0", "3.0.0"))
        assertFalse(UpdateChecker.isNewer("3.0.0", "3.0.1"))
        assertFalse(UpdateChecker.isNewer("2.8.99", "3.0.0"))
    }

    /** Releases are tagged `android-v*` so the desktop `v*` namespace stays free. */
    @Test
    fun testAndroidTagPrefixIsCompared() {
        assertTrue(UpdateChecker.isNewer("android-v3.0.1", "3.0.0"))
        assertFalse(UpdateChecker.isNewer("android-v3.0.0", "3.0.0"))
        assertEquals(listOf(3, 0, 0), UpdateChecker.versionParts("android-v3.0.0"))
    }

    /**
     * A desktop tag must never read as an Android update. `v0.2.5` is a real
     * published tag and compares lower, so even if one leaked past the prefix
     * filter it cannot prompt anyone to install a Windows zip.
     */
    @Test
    fun testDesktopTagIsNotNewer() {
        assertFalse(UpdateChecker.isNewer("v0.2.5", "3.0.0"))
    }

    @Test
    fun testVersionParts() {
        assertEquals(listOf(3, 0, 0), UpdateChecker.versionParts("3.0.0"))
        assertEquals(listOf(2, 0, 0), UpdateChecker.versionParts("v2.0.0"))
    }
}
