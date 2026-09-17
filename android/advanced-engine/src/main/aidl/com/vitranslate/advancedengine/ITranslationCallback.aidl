package com.vitranslate.advancedengine;

interface ITranslationCallback {
    void onProgress(int currentPage, int totalPages, String logMessage);
    void onSuccess(String resultPath);
    void onError(String errorMessage);
}
