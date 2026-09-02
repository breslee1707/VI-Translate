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

    /** Shorthand for the geometry these tests care about. */
    private fun block(
        text: String,
        x: Float,
        y: Float,
        width: Float,
        fontSize: Float = 10f,
        rotation: Int = 0
    ) = PdfLayoutPreserver.TextBlock(
        text = text,
        x = x,
        y = y,
        fontSize = fontSize,
        width = width,
        ascent = fontSize * 0.8f,
        descent = fontSize * 0.2f,
        rotation = rotation
    )

    @Test
    fun testCollapseVerticalFractions_tableRowsAreNeverMergedIntoFractions() {
        // The symbol column of a terminology table: one short token per row, at
        // the normal line pitch, no bar anywhere. A bar-less "fraction" pass
        // used to turn these into "b/b0" and "alpha/beta", which halved the row
        // count and left every label pointing at the wrong symbol.
        val rows = listOf(
            block("b", x = 465f, y = 530f, width = 6f),
            block("b₀", x = 465f, y = 520f, width = 9f),
            block("α", x = 466f, y = 510f, width = 7f),
            block("β", x = 466f, y = 500f, width = 7f)
        )

        val collapsed = PdfLayoutPreserver.collapseVerticalFractions(rows)

        assertEquals(4, collapsed.size)
        assertTrue(collapsed.none { it.text.contains("/") })
    }

    @Test
    fun testCollapseVerticalFractions_aDashNarrowerThanItsNeighboursIsNotABar() {
        // The unit column of a terminology table: "-" means dimensionless, and
        // it is one glyph wide. Read as a fraction bar it swallowed the unit
        // above and below into "mm/N" and cost the table a row.
        val above = block("mm", x = 514f, y = 210f, width = 15f)
        val dash = block("–", x = 519f, y = 200f, width = 4f)
        val below = block("N", x = 518f, y = 190f, width = 6f)

        val collapsed = PdfLayoutPreserver.collapseVerticalFractions(listOf(above, dash, below))

        assertEquals(3, collapsed.size)
        assertTrue(collapsed.none { it.text.contains("/") })
    }

    @Test
    fun testCollapseVerticalFractions_rotatedBlocksAreLeftAlone() {
        val header = block("Designation", x = 220f, y = 150f, width = 50f, rotation = 90)
        val body = block("Drum width", x = 220f, y = 130f, width = 60f)

        val collapsed = PdfLayoutPreserver.collapseVerticalFractions(listOf(header, body))

        assertEquals(2, collapsed.size)
        assertTrue(collapsed.any { it.rotation == 90 && it.text == "Designation" })
    }

    @Test
    fun testIsPureMathOrFormula_symbolsCarryingGreekOrSubscripts() {
        // Every one of these reached the translator in a shipped build.
        // "εmax" came back as "tối đa" and "m'₀" as "tôi'₀".
        for (symbol in listOf(
            "εmax", "m'₀", "m'u", "ρS", "µST", "µR",
            "b₀", "F₁", "F₂", "k₁%", "ΔL", "η", "V∙"
        )) {
            assertTrue("expected $symbol to read as a symbol", PdfLayoutPreserver.isPureMathOrFormula(symbol))
        }
    }

    @Test
    fun testIsPureMathOrFormula_proseIsStillProse() {
        // The rule that rescues the symbols above must not swallow the labels
        // sitting next to them in the same table.
        for (prose in listOf(
            "Terminology",
            "Maximum belt elongation",
            "Belt width",
            "Key to the abbreviations",
            "Drive efficiency",
            "Bulk density of goods conveyed"
        )) {
            assertFalse("expected $prose to read as prose", PdfLayoutPreserver.isPureMathOrFormula(prose))
        }
    }

    @Test
    fun testIsPureMathOrFormula_functionWordStrippingSurvivesANonLatinNeighbour() {
        // "maximum" must stay a word even though it starts with "max".
        assertFalse(PdfLayoutPreserver.isPureMathOrFormula("maximum"))
        assertFalse(PdfLayoutPreserver.isPureMathOrFormula("Minimum belt pull"))
    }

    @Test
    fun testComputeRightLimits_neighbourBoundsTheColumnNotThePage() {
        // Two-column body text. The left column may not grow past the right one.
        val left = block("left column line", x = 48f, y = 700f, width = 200f)
        val right = block("right column line", x = 300f, y = 700f, width = 200f)

        val limits = PdfLayoutPreserver.computeRightLimits(listOf(left, right), pageRightEdge = 595f)

        assertEquals(297f, limits.getValue(left), 0.5f)
        // Nothing to the right of the right column, so it gets the page margin.
        assertEquals(552f, limits.getValue(right), 0.5f)
    }

    @Test
    fun testComputeRightLimits_aColumnSharesTheTightestLimitItFound() {
        // The second line of a table cell has no symbol beside it, so on its own
        // it would be allowed to run the full width of the page.
        val firstLine = block("Mass of goods conveyed", x = 218f, y = 500f, width = 180f)
        val secondLine = block("on the return side", x = 218f, y = 490f, width = 120f)
        val symbol = block("m'u", x = 463f, y = 500f, width = 14f)

        val limits = PdfLayoutPreserver.computeRightLimits(
            listOf(firstLine, secondLine, symbol),
            pageRightEdge = 595f
        )

        assertEquals(460f, limits.getValue(firstLine), 0.5f)
        assertEquals(460f, limits.getValue(secondLine), 0.5f)
    }

    @Test
    fun testComputeRightLimits_neverNarrowerThanTheSourceItself() {
        // The long first line of a cell already reaches past where its column's
        // limit falls. Shrinking it to the column would clip text the source
        // managed to fit, so it keeps the extent it was drawn at.
        val longLine = block("a first line that already reaches the symbol", x = 218f, y = 500f, width = 250f)
        val shortLine = block("and its second line", x = 218f, y = 490f, width = 90f)
        val symbol = block("m'u", x = 463f, y = 490f, width = 14f)

        val limits = PdfLayoutPreserver.computeRightLimits(
            listOf(longLine, shortLine, symbol),
            pageRightEdge = 595f
        )

        assertEquals(460f, limits.getValue(shortLine), 0.5f)
        assertEquals(468f, limits.getValue(longLine), 0.5f)
    }

    @Test
    fun testComputeRightLimits_rotatedRunsBoundOthersButGetNoLimitOfTheirOwn() {
        val header = block("Unit", x = 515f, y = 170f, width = 30f, rotation = 90)
        val label = block("Designation of the quantity", x = 220f, y = 175f, width = 120f)

        val limits = PdfLayoutPreserver.computeRightLimits(listOf(header, label), pageRightEdge = 595f)

        assertNull(limits[header])
        // Bounded by the rotated header's left edge (x - descent), not the page.
        assertEquals(510f, limits.getValue(label), 1.0f)
    }
}
