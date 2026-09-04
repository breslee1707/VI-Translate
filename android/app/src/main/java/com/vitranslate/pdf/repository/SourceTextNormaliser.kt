package com.vitranslate.pdf.repository

/**
 * Small, evidence-driven repairs to source text that make a translation engine
 * read it the way a person would.
 *
 * Mirrors `normalise_number_abbreviation` in `pdf2zh/translator.py`; the two
 * builds share the defect, so they share the fix.
 */
object SourceTextNormaliser {

    private val NUMBER_ABBREVIATION = Regex("(?<![A-Za-z])no\\.(?=\\s*\\d)")

    /**
     * Capitalise the "no." that means "number" so it is not read as "not".
     *
     * "ref. no. 305" came back as "ref. KHÔNG. 305" — lowercase "no."
     * mid-sentence reads as the negation. The same string capitalised is
     * unambiguous ("No. 305" translates to "Số 305"), and capitalising an
     * abbreviation that already stands for a proper noun changes nothing else.
     *
     * Only a "no." directly in front of a number is touched, so ordinary prose
     * is left alone.
     */
    fun normaliseNumberAbbreviation(text: String): String =
        NUMBER_ABBREVIATION.replace(text, "No.")

    /** Every normalisation, in the order the engine should see them. */
    fun normalise(text: String): String = normaliseNumberAbbreviation(text)
}
