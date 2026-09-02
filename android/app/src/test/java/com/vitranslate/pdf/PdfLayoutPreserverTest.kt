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
    fun testCollapseVerticalFractions_withBar() {
        val top = PdfLayoutPreserver.TextBlock(
            text = "ax² + bx + c",
            x = 150f,
            y = 120f,
            fontSize = 10f,
            width = 70f,
            ascent = 8f,
            descent = 2f
        )
        val bar = PdfLayoutPreserver.TextBlock(
            text = "────────",
            x = 150f,
            y = 115f,
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

        val collapsed = PdfLayoutPreserver.collapseVerticalFractions(listOf(top, bar, bot))
        assertEquals(1, collapsed.size)
        assertEquals("(ax² + bx + c)/(x - d)", collapsed[0].text)
        assertEquals(115f, collapsed[0].y, 0.1f)
    }

    @Test
    fun testCollapseVerticalFractions_withoutBar_noMerge() {
        // Without a fraction bar, blocks should NOT be merged
        val top = PdfLayoutPreserver.TextBlock(
            text = "Question 1",
            x = 50f,
            y = 500f,
            fontSize = 10f,
            width = 100f,
            ascent = 8f,
            descent = 2f
        )
        val bot = PdfLayoutPreserver.TextBlock(
            text = "A. 0.",
            x = 50f,
            y = 490f,
            fontSize = 10f,
            width = 30f,
            ascent = 8f,
            descent = 2f
        )
        val extra = PdfLayoutPreserver.TextBlock(
            text = "B. 2.",
            x = 150f,
            y = 490f,
            fontSize = 10f,
            width = 30f,
            ascent = 8f,
            descent = 2f
        )

        val collapsed = PdfLayoutPreserver.collapseVerticalFractions(listOf(top, bot, extra))
        assertEquals(3, collapsed.size)  // all blocks preserved, nothing merged
    }

    @Test
    fun testIsPureMathOrFormula() {
        // Pure math — should be classified as formula
        assertTrue(PdfLayoutPreserver.isPureMathOrFormula("(ax² + bx + c) / (x - d)"))
        assertTrue(PdfLayoutPreserver.isPureMathOrFormula("fnc / fnt = 2^(n/12)"))
        assertTrue(PdfLayoutPreserver.isPureMathOrFormula("32 / 3"))

        // Vietnamese prose containing math — should NOT be classified as formula
        assertFalse(PdfLayoutPreserver.isPureMathOrFormula("Cho hàm số y = (ax² + bx + c) / (x - d) có đồ thị như hình vẽ"))
        assertFalse(PdfLayoutPreserver.isPureMathOrFormula("Họ nguyên hàm của hàm số f(x) = 1/(1-x) + sin(2x) là"))
        assertFalse(PdfLayoutPreserver.isPureMathOrFormula("Giá trị lớn nhất của hàm số y = x + 4 - x² bằng"))

        // English prose containing math — should NOT be classified as formula
        assertFalse(PdfLayoutPreserver.isPureMathOrFormula("The sum of the solutions of the equation is"))
    }
}
