package com.vitranslate.pdf.model

data class TargetLanguage(
    val code: String,
    val name: String
) {
    companion object {
        val DEFAULT_CODE = "vi"

        val SUPPORTED_LANGUAGES = listOf(
            TargetLanguage("af", "Afrikaans"),
            TargetLanguage("ca", "Català"),
            TargetLanguage("cs", "Čeština"),
            TargetLanguage("cy", "Cymraeg"),
            TargetLanguage("da", "Dansk"),
            TargetLanguage("de", "Deutsch"),
            TargetLanguage("en", "English"),
            TargetLanguage("es", "Español"),
            TargetLanguage("et", "Eesti"),
            TargetLanguage("eu", "Euskara"),
            TargetLanguage("fi", "Suomi"),
            TargetLanguage("fr", "Français"),
            TargetLanguage("ga", "Gaeilge"),
            TargetLanguage("gl", "Galego"),
            TargetLanguage("hr", "Hrvatski"),
            TargetLanguage("hu", "Magyar"),
            TargetLanguage("id", "Bahasa Indonesia"),
            TargetLanguage("is", "Íslenska"),
            TargetLanguage("it", "Italiano"),
            TargetLanguage("lt", "Lietuvių"),
            TargetLanguage("lv", "Latviešu"),
            TargetLanguage("ms", "Bahasa Melayu"),
            TargetLanguage("mt", "Malti"),
            TargetLanguage("nl", "Nederlands"),
            TargetLanguage("no", "Norsk"),
            TargetLanguage("pl", "Polski"),
            TargetLanguage("pt", "Português"),
            TargetLanguage("ro", "Română"),
            TargetLanguage("sk", "Slovenčina"),
            TargetLanguage("sl", "Slovenščina"),
            TargetLanguage("sq", "Shqip"),
            TargetLanguage("sv", "Svenska"),
            TargetLanguage("sw", "Kiswahili"),
            TargetLanguage("tl", "Tagalog"),
            TargetLanguage("tr", "Türkçe"),
            TargetLanguage("vi", "Tiếng Việt")
        ).sortedBy { it.name }

        fun getByCode(code: String): TargetLanguage {
            return SUPPORTED_LANGUAGES.find { it.code.lowercase() == code.lowercase() }
                ?: SUPPORTED_LANGUAGES.first { it.code == DEFAULT_CODE }
        }

        fun getByName(name: String): TargetLanguage {
            return SUPPORTED_LANGUAGES.find { it.name == name }
                ?: SUPPORTED_LANGUAGES.first { it.code == DEFAULT_CODE }
        }
    }
}
