import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import zh from "./locales/zh.json";
import en from "./locales/en.json";

const SUPPORTED_LANGS = ["zh", "en"] as const;

const detectLanguage = (): string => {
    const savedLang = localStorage.getItem("xbot-lang");
    if (savedLang && (SUPPORTED_LANGS as readonly string[]).includes(savedLang)) {
        return savedLang;
    }

    const browserLang = navigator.language.toLowerCase();

    if (browserLang.startsWith("zh")) {
        return "zh";
    } else if (browserLang.startsWith("en")) {
        return "en";
    }

    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (timezone.includes("Asia/Shanghai") || timezone.includes("Asia/Hong_Kong") || timezone.includes("Asia/Taipei")) {
        return "zh";
    }

    return "en";
};

i18n
    .use(LanguageDetector)
    .use(initReactI18next)
    .init({
        resources: {
            zh: { translation: zh },
            en: { translation: en },
        },
        lng: detectLanguage(),
        fallbackLng: "en",
        supportedLngs: [...SUPPORTED_LANGS],
        detection: {
            order: ["localStorage", "navigator"],
            caches: ["localStorage"],
            lookupLocalStorage: "xbot-lang",
        },
        interpolation: {
            escapeValue: false,
        },
    });

export default i18n;
