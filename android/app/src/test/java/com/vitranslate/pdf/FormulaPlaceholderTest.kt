package com.vitranslate.pdf

import com.vitranslate.pdf.repository.FormulaPlaceholder
import com.vitranslate.pdf.repository.FormulaPlaceholderException
import org.junit.Assert.*
import org.junit.Test

class FormulaPlaceholderTest {

    @Test
    fun testEncodeFormulaPlaceholders() {
        val source = "Equation {v0} and inline {v1} text"
        val encoded = FormulaPlaceholder.encodeFormulaPlaceholders(source)
        assertEquals("Equation <b0></b0> and inline <b1></b1> text", encoded)
    }

    @Test
    fun testRestoreFormulaPlaceholdersSuccess() {
        val source = "Equation {v0} and inline {v1} text"
        val translated = "Phương trình <b0></b0> và nội dung <b1></b1>"
        val restored = FormulaPlaceholder.restoreFormulaPlaceholders(source, translated)
        assertEquals("Phương trình {v0} và nội dung {v1}", restored)
    }

    @Test(expected = FormulaPlaceholderException::class)
    fun testRestoreFormulaPlaceholdersFailsWhenTagsAltered() {
        val source = "Equation {v0} and inline {v1} text"
        val translated = "Phương trình <b0></b0> bị mất tag thứ hai"
        FormulaPlaceholder.restoreFormulaPlaceholders(source, translated)
    }

    @Test
    fun testRemoveControlCharacters() {
        val input = "Clean\u0000Text\u0007With\u001BControl"
        val cleaned = FormulaPlaceholder.removeControlCharacters(input)
        assertEquals("CleanTextWithControl", cleaned)
    }

    @Test
    fun testValidateStyleTagsSuccess() {
        val source = "<s1>Bold text</s1>"
        val translated = "<s1>Văn bản in đậm</s1>"
        FormulaPlaceholder.validateStyleTags(source, translated)
    }

    @Test(expected = FormulaPlaceholderException::class)
    fun testValidateStyleTagsMismatch() {
        val source = "<s1>Bold text</s1>"
        val translated = "Văn bản in đậm"
        FormulaPlaceholder.validateStyleTags(source, translated)
    }
}
