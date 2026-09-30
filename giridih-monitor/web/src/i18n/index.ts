import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import en from './en.json'
import hi from './hi.json'

/** Hindi first, English on a toggle (LLD 10). The <html lang> attribute is kept
 *  in step so the Devanagari font stack applies. */
const STORAGE_KEY = 'giridih.lang'

function initialLanguage(): 'hi' | 'en' {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'hi' || saved === 'en') return saved
  } catch {
    /* ignore */
  }
  return 'hi'
}

void i18n.use(initReactI18next).init({
  resources: { hi: { translation: hi }, en: { translation: en } },
  lng: initialLanguage(),
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

function applyLang(lang: string) {
  document.documentElement.lang = lang
}
applyLang(i18n.language)

i18n.on('languageChanged', (lang) => {
  applyLang(lang)
  try {
    localStorage.setItem(STORAGE_KEY, lang)
  } catch {
    /* ignore */
  }
})

export default i18n
