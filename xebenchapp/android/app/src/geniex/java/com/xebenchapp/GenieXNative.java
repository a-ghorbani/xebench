package com.xebenchapp;

import com.geniex.sdk.bean.GenerationConfig;
import com.geniex.sdk.bean.LLMTokenCallback;
import com.geniex.sdk.bean.LlmCreateInput;
import com.geniex.sdk.bean.LlmGenerateResult;
import com.geniex.sdk.jni.Llm;

/**
 * geniex-android's JNI model calls, made directly and synchronously on the caller's thread.
 *
 * The SDK's public LlmWrapper runs these same calls on a coroutine dispatcher and forwards each token
 * and the final ProfilingData through a buffered callbackFlow (trySend), which hops threads before the
 * app sees a token and can drop the completion event when the buffer is full. The benchmark needs the
 * completion's ProfilingData every time and times the first token on its own worker thread, so it
 * calls com.geniex.sdk.jni.Llm (the class LlmWrapper wraps) itself. That class is Kotlin `internal`,
 * which Kotlin callers outside the SDK cannot name but Java can; its JNI signatures are those of
 * geniex-android 0.8.0, and GenieXBenchModule refuses any other packaged version (PINNED_VERSION).
 */
final class GenieXNative {
    /** The geniex-android version whose JNI calls this class was written for. */
    static final String PINNED_VERSION = "0.8.0";

    private final Llm llm = new Llm();

    static void requirePinned(String packagedVersion) {
        if (!PINNED_VERSION.equals(packagedVersion)) {
            throw new IllegalStateException("GenieXBench is pinned to geniex-android " + PINNED_VERSION
                + "; this APK packages " + packagedVersion);
        }
    }

    /** A model handle (non-zero); the SDK throws with its error message when creation fails. */
    long create(LlmCreateInput input) {
        return llm.create(input);
    }

    /** Empties the model's context: the next request starts a new conversation. */
    int reset(long handle) {
        return llm.reset(handle);
    }

    /**
     * One request on the model; tokens and the final result arrive on callback, on this thread,
     * before it returns. The SDK throws with its error message when generation fails.
     */
    void generate(long handle, String prompt, GenerationConfig config, LLMTokenCallback callback) {
        LlmGenerateResult ignored = llm.generate(handle, prompt, config, callback);
    }

    int destroy(long handle) {
        return llm.destroy(handle);
    }
}
