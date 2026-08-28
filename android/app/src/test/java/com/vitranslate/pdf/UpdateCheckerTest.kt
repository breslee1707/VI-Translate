package com.vitranslate.pdf

import com.vitranslate.pdf.repository.UpdateChecker
import org.junit.Assert.*
import org.junit.Test

class UpdateCheckerTest {

    @Test
    fun testIsNewerVersion() {
        assertTrue(UpdateChecker.isNewer("1.9.12", "1.9.11"))
        assertTrue(UpdateChecker.isNewer("2.0.0", "1.9.11"))
        assertTrue(UpdateChecker.isNewer("v1.10.0", "1.9.11"))

        assertFalse(UpdateChecker.isNewer("1.9.11", "1.9.11"))
        assertFalse(UpdateChecker.isNewer("1.9.10", "1.9.11"))
        assertFalse(UpdateChecker.isNewer("1.8.99", "1.9.11"))
    }

    @Test
    fun testVersionParts() {
        assertEquals(listOf(1, 9, 11), UpdateChecker.versionParts("1.9.11"))
        assertEquals(listOf(2, 0, 0), UpdateChecker.versionParts("v2.0.0"))
    }
}
