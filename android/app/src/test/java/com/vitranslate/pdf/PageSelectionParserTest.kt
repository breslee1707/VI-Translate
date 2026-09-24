package com.vitranslate.pdf

import com.vitranslate.pdf.repository.PageSelectionParser
import org.junit.Assert.assertEquals
import org.junit.Test

class PageSelectionParserTest {

    @Test
    fun testAllPagesDefault() {
        val total = 10
        assertEquals((1..10).toList(), PageSelectionParser.parsePageSelection("all", total))
        assertEquals((1..10).toList(), PageSelectionParser.parsePageSelection("", total))
        assertEquals((1..10).toList(), PageSelectionParser.parsePageSelection("   ", total))
    }

    @Test
    fun testSinglePage() {
        val total = 10
        assertEquals(listOf(1), PageSelectionParser.parsePageSelection("1", total))
        assertEquals(listOf(5), PageSelectionParser.parsePageSelection("5", total))
    }

    @Test
    fun testCommaSeparatedPages() {
        val total = 10
        assertEquals(listOf(1, 3, 5), PageSelectionParser.parsePageSelection("1,3,5", total))
        assertEquals(listOf(1, 3, 5), PageSelectionParser.parsePageSelection("1, 3 , 5 ", total))
    }

    @Test
    fun testSimpleRange() {
        val total = 10
        assertEquals(listOf(1, 2, 3, 4, 5), PageSelectionParser.parsePageSelection("1-5", total))
    }

    @Test
    fun testComplexMixedSyntax() {
        val total = 24
        val expected = listOf(1, 2, 3, 7, 10, 11, 12)
        assertEquals(expected, PageSelectionParser.parsePageSelection("1-3,7,10-12", total))
        assertEquals(expected, PageSelectionParser.parsePageSelection(" 1-3 , 7 , 10-12 ", total))
    }

    @Test
    fun testDuplicatesAndSorting() {
        val total = 10
        assertEquals(listOf(1, 2, 3, 5), PageSelectionParser.parsePageSelection("5, 1, 3, 1-3", total))
    }

    @Test
    fun testOutofBoundsAndInvalidInput() {
        val total = 5
        // Page 0, negative pages, page > total
        assertEquals(listOf(1, 2), PageSelectionParser.parsePageSelection("-1, 0, 1, 2, 99", total))
        assertEquals((1..5).toList(), PageSelectionParser.parsePageSelection("invalid, text", total))
        assertEquals((1..5).toList(), PageSelectionParser.parsePageSelection("0", total))
    }
}
