/**
 * i18n initialization — AI Team Studio
 *
 * - Supports: English (en), Simplified Chinese (zh-CN)
 * - Persistence: localStorage key "ats-language"
 * - Default: English
 * - No backend/HTTP loading — all resources bundled
 */

import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.ts";
import zhCN from "./locales/zh-CN.ts";

/** localStorage key for language persistence */
export const LANGUAGE_STORAGE_KEY = "ats-language";

/** Supported language codes */
export const SUPPORTED_LANGUAGES = [
  { code: "en", label: "English" },
  { code: "zh-CN", label: "简体中文" },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]["code"];

/** Read persisted language or fall back to English */
function getStoredLanguage(): LanguageCode {
  try {
    const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (stored === "en" || stored === "zh-CN") return stored;
  } catch {
    // localStorage may be unavailable in some contexts
  }
  return "en";
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    "zh-CN": { translation: zhCN },
  },
  lng: getStoredLanguage(),
  fallbackLng: "en",
  interpolation: {
    escapeValue: false, // React already escapes
  },
  react: {
    useSuspense: false,
  },
});

/** Change language and persist to localStorage */
export function setLanguage(code: LanguageCode): void {
  void i18n.changeLanguage(code);
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, code);
  } catch {
    // silent
  }
}

export default i18n;
