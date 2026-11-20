/** Hindi by default, English by toggle (as in the core app). Keys are identical in both files; a
 * test fails on any key used in code that either language lacks (src/i18n/keys.test.ts). */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";
import hi from "./hi.json";

export const LANGS = ["hi", "en"] as const;
export type Lang = (typeof LANGS)[number];
const STORE = "em-lang";

function initial(): Lang {
  try {
    const v = localStorage.getItem(STORE);
    return v === "en" ? "en" : "hi";
  } catch {
    return "hi";
  }
}

void i18n.use(initReactI18next).init({
  resources: { hi: { translation: hi }, en: { translation: en } },
  lng: initial(),
  fallbackLng: false,          // a missing key must show up, not quietly fall back
  interpolation: { escapeValue: false },
  returnNull: false,
});

export function setLang(lang: Lang) {
  void i18n.changeLanguage(lang);
  document.documentElement.lang = lang;
  try {
    localStorage.setItem(STORE, lang);
  } catch {
    /* private mode */
  }
}

document.documentElement.lang = i18n.language;
export default i18n;
