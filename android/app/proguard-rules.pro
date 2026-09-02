# Referenced by the release build type. Minification is off today, so nothing
# here runs yet; the rules exist so turning it on does not silently strip the
# reflection-driven parts of the PDF and HTTP stacks.

# PDFBox-Android loads font and encoding resources by name at runtime.
-keep class com.tom_roush.pdfbox.** { *; }
-keep class com.tom_roush.fontbox.** { *; }
-dontwarn com.tom_roush.**

# PDFBox is a port of a desktop library and references java.awt / javax.imageio
# classes that do not exist on Android but are never reached.
-dontwarn java.awt.**
-dontwarn javax.imageio.**
-dontwarn javax.xml.bind.**

# OkHttp ships optional platform integrations that are absent on Android.
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**

# Kotlin coroutines debug agent hooks.
-dontwarn kotlinx.coroutines.debug.**
