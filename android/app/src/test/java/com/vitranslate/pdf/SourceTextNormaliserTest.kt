package com.vitranslate.pdf

import com.vitranslate.pdf.repository.SourceTextNormaliser
import org.junit.Assert.assertEquals
import org.junit.Test

class SourceTextNormaliserTest {

    @Test
    fun theNumberAbbreviationIsCapitalisedBeforeANumber() {
        assertEquals(
            "can be found in our brochure, ref. No. 305",
            SourceTextNormaliser.normalise("can be found in our brochure, ref. no. 305")
        )
    }

    @Test
    fun everyOccurrenceInASegmentIsCapitalised() {
        assertEquals(
            "Part No. 12 and No. 13",
            SourceTextNormaliser.normalise("Part no. 12 and no. 13")
        )
    }

    @Test
    fun aSpaceBetweenTheAbbreviationAndTheNumberIsAllowed() {
        assertEquals("No.  7", SourceTextNormaliser.normalise("no.  7"))
    }

    @Test
    fun theNegationIsLeftAlone() {
        // "no." that is not followed by a number is the ordinary word, or the
        // end of a sentence. Capitalising it would be wrong.
        assertEquals("There is no. Then we stop.", SourceTextNormaliser.normalise("There is no. Then we stop."))
        assertEquals("say no.", SourceTextNormaliser.normalise("say no."))
    }

    @Test
    fun theTailOfALongerWordIsNotMistakenForTheAbbreviation() {
        assertEquals("casino. 5 tables", SourceTextNormaliser.normalise("casino. 5 tables"))
    }

    @Test
    fun alreadyCapitalisedTextIsUnchanged() {
        assertEquals("ref. No. 305", SourceTextNormaliser.normalise("ref. No. 305"))
    }
}
