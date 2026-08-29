package com.vitranslate.pdf

import com.vitranslate.pdf.repository.FormulaPlaceholder
import org.junit.Assert.*
import org.junit.Test

class PdfLayoutPreserverTest {

    @Test
    fun testFormulaPlaceholderEncodingAndRestoration() {
        val original = "Let {v0} be a function where x^2 + 1 = 0."
        val encoded = FormulaPlaceholder.encodeFormulaPlaceholders(original)
        assertEquals("Let <b0></b0> be a function where <b9000>x^2</b9000> + 1 = 0.", encoded)

        val restored = FormulaPlaceholder.restoreFormulaPlaceholders(original, encoded)
        assertEquals(original, restored)
    }

    @Test
    fun testFormulaPlaceholderControlCharRemoval() {
        val input = "Text with \u0000 control \u0007 chars"
        val cleaned = FormulaPlaceholder.removeControlCharacters(input)
        assertEquals("Text with  control  chars", cleaned)
    }
}
