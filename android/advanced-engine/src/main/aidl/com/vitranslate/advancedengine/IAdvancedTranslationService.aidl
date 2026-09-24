package com.vitranslate.advancedengine;

import com.vitranslate.advancedengine.ITranslationCallback;
import android.os.ParcelFileDescriptor;

interface IAdvancedTranslationService {
    boolean isReady();
    void translatePdf(
        in ParcelFileDescriptor inputPdf,
        in ParcelFileDescriptor outputPdf,
        in String targetLang,
        in String engineType,
        in ITranslationCallback callback
    );
    void cancel();
}
