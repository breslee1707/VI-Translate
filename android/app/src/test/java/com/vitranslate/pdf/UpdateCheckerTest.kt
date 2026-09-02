package com.vitranslate.pdf

import com.vitranslate.pdf.repository.UpdateChecker
import org.junit.Assert.*
import org.junit.Test

class UpdateCheckerTest {

    @Test
    fun testIsNewerVersion() {
        assertTrue(UpdateChecker.isNewer("0.1.1", "0.1.0"))
        assertTrue(UpdateChecker.isNewer("1.0.0", "0.9.11"))
        assertTrue(UpdateChecker.isNewer("v0.10.0", "0.9.11"))

        assertFalse(UpdateChecker.isNewer("0.1.0", "0.1.0"))
        assertFalse(UpdateChecker.isNewer("0.1.0", "0.1.1"))
        assertFalse(UpdateChecker.isNewer("0.0.99", "0.1.0"))
    }

    /** Releases are tagged `android-v*` so the desktop `v*` namespace stays free. */
    @Test
    fun testAndroidTagPrefixIsCompared() {
        assertTrue(UpdateChecker.isNewer("android-v0.1.1", "0.1.0"))
        assertFalse(UpdateChecker.isNewer("android-v0.1.0", "0.1.0"))
        assertEquals(listOf(0, 1, 0), UpdateChecker.versionParts("android-v0.1.0"))
    }

    /**
     * The desktop line is at v0.2.5 while Android starts at 0.1.0, so a desktop
     * tag compares *higher*. Nothing in the version numbers protects us — only
     * the `android-v` prefix filter in checkForUpdate keeps a Windows release
     * from prompting Android users to install a zip they cannot use. This test
     * pins the danger so the filter is never dropped as redundant.
     */
    @Test
    fun testDesktopTagWouldCompareHigherAndMustBeFilteredByPrefix() {
        assertTrue(UpdateChecker.isNewer("v0.2.5", "0.1.0"))
        assertFalse("v0.2.5".startsWith(UpdateChecker.ANDROID_TAG_PREFIX))
    }

    @Test
    fun testVersionParts() {
        assertEquals(listOf(0, 1, 0), UpdateChecker.versionParts("0.1.0"))
        assertEquals(listOf(2, 0, 0), UpdateChecker.versionParts("v2.0.0"))
    }
}
