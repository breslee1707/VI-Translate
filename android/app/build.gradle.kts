import java.io.FileInputStream
import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

// Release signing comes from the environment on CI and from an untracked
// keystore.properties locally. A missing keystore leaves the release build
// unsigned on purpose: falling back to the debug key would ship builds whose
// signature changes every run, and users could not update over them.
val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties().apply {
    if (keystorePropertiesFile.exists()) {
        FileInputStream(keystorePropertiesFile).use { load(it) }
    }
}

fun signingValue(environmentName: String, propertyName: String): String? =
    (System.getenv(environmentName) ?: keystoreProperties.getProperty(propertyName))
        ?.takeIf { it.isNotBlank() }

val releaseStorePath = signingValue("ANDROID_KEYSTORE_PATH", "storeFile")
val releaseStorePassword = signingValue("ANDROID_KEYSTORE_PASSWORD", "storePassword")
val releaseKeyAlias = signingValue("ANDROID_KEY_ALIAS", "keyAlias")
val releaseKeyPassword = signingValue("ANDROID_KEY_PASSWORD", "keyPassword")
val hasReleaseSigning = releaseStorePath != null &&
    releaseStorePassword != null &&
    releaseKeyAlias != null &&
    releaseKeyPassword != null &&
    file(releaseStorePath).exists()

// The one number to change for a release. Everything else follows from it: the
// tag the workflow accepts, the name of the published APK, and versionCode.
val appVersionName = "0.1.0"

/**
 * Android refuses to install over a build whose versionCode is not lower, and
 * the number means nothing to anyone by itself. Deriving it from the version
 * name removes the step that is easy to forget and whose only symptom is
 * "app not installed" on a user's phone.
 *
 * major * 10000 + minor * 100 + patch, so 0.1.0 is 100 and 1.2.3 is 10203.
 * Each component has to stay under 100, which is the same discipline the
 * desktop APP_VERSION already follows.
 */
fun versionCodeFrom(name: String): Int {
    val parts = name.split(".").map { part ->
        part.takeWhile { it.isDigit() }.toIntOrNull()
            ?: throw GradleException("appVersionName '$name' is not dotted numbers")
    }
    require(parts.size == 3) { "appVersionName '$name' must be major.minor.patch" }
    require(parts.drop(1).all { it < 100 }) {
        "minor and patch in '$name' must each stay below 100"
    }
    return parts[0] * 10000 + parts[1] * 100 + parts[2]
}

android {
    namespace = "com.vitranslate.pdf"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.vitranslate.pdf"
        minSdk = 26
        targetSdk = 35
        versionCode = versionCodeFrom(appVersionName)
        versionName = appVersionName

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = file(releaseStorePath!!)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (hasReleaseSigning) signingConfigs.getByName("release") else null
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        @Suppress("DEPRECATION")
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    lint {
        checkReleaseBuilds = false
    }
}

tasks.register("printVersionName") {
    val versionName = android.defaultConfig.versionName
    doLast { println(versionName) }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.documentfile)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.material.icons.extended)

    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.okhttp)
    implementation(libs.pdfbox.android)

    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.ui.test.junit4)
    debugImplementation(libs.androidx.ui.tooling)
    debugImplementation(libs.androidx.ui.test.manifest)
}
