package com.vitranslate.pdf

import com.vitranslate.pdf.repository.FormulaPlaceholder
import com.vitranslate.pdf.repository.PdfLayoutPreserver
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

    @Test
    fun testCollapseVerticalFractions() {
        val top = PdfLayoutPreserver.TextBlock(
            text = "ax² + bx + c",
            x = 150f,
            y = 120f,
            fontSize = 10f,
            width = 70f,
            ascent = 8f,
            descent = 2f
        )
        val bot = PdfLayoutPreserver.TextBlock(
            text = "x - d",
            x = 150f,
            y = 110f,
            fontSize = 10f,
            width = 70f,
            ascent = 8f,
            descent = 2f
        )

        val collapsed = PdfLayoutPreserver.collapseVerticalFractions(listOf(top, bot))
        assertEquals(1, collapsed.size)
        assertEquals("(ax² + bx + c) / (x - d)", collapsed[0].text)
        assertEquals(115f, collapsed[0].y, 0.1f)
    }

    @Test
    fun testIsPureMathOrFormula() {
        assertTrue(PdfLayoutPreserver.isPureMathOrFormula("(ax² + bx + c) / (x - d)"))
        assertTrue(PdfLayoutPreserver.isPureMathOrFormula("fnc / fnt = 2^(n/12)"))
        assertTrue(PdfLayoutPreserver.isPureMathOrFormula("32 / 3"))
        assertFalse(PdfLayoutPreserver.isPureMathOrFormula("Cho hàm số y = (ax² + bx + c) / (x - d) có đồ thị như hình vẽ"))
    }
}
