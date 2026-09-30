import { useTranslation } from 'react-i18next'

/**
 * Curated knowledge cards (HLD module 9). These are the same cards that go into
 * the assistant's cached prompt, so what the dashboard shows and what the
 * assistant knows cannot drift apart.
 */
const CARDS = [
  {
    slug: 'bypoll-context',
    titleEn: 'Why there is a by-election',
    titleHi: 'उपचुनाव की पृष्ठभूमि',
    bodyEn:
      'Sudivya Kumar "Sonu" (JMM), the sitting MLA elected in 2019 and 2024, died on ' +
      '6 September 2026. The ECI must poll within six months, so by roughly early March 2027. ' +
      'A sympathy effect after a sitting member dies is real but cannot be measured in advance — ' +
      'it is a scenario input here, never a prediction.',
    bodyHi:
      'सुदिव्य कुमार "सोनू" (झामुमो), 2019 और 2024 में निर्वाचित विधायक, का 6 सितंबर 2026 को निधन हो गया। ' +
      'चुनाव आयोग को छह माह के भीतर चुनाव कराना है, अर्थात लगभग मार्च 2027 तक। सहानुभूति का प्रभाव ' +
      'वास्तविक है पर पहले से मापा नहीं जा सकता — यहाँ यह एक परिदृश्य-इनपुट है, पूर्वानुमान नहीं।',
    sources: 'HLD §1',
  },
  {
    slug: 'ls-vs-split',
    titleEn: 'The 2024 Lok Sabha / Vidhan Sabha split',
    titleHi: '2024 लोकसभा बनाम विधानसभा का अंतर',
    bodyEn:
      'In LS 2024 across PC-11 Giridih: AJSU 4,51,139 (35.7%), JMM 3,70,259 (29.3%), ' +
      'JLKM 3,47,322 (27.5%). AJSU led in the Giridih assembly segment, yet JMM held the seat ' +
      'six months later. This is the single most important dynamic for the by-election: see the ' +
      'LS vs VS view and the floating-vote column.',
    bodyHi:
      'लोकसभा 2024 (गिरिडीह संसदीय क्षेत्र): आजसू 4,51,139 (35.7%), झामुमो 3,70,259 (29.3%), ' +
      'जेएलकेएम 3,47,322 (27.5%)। गिरिडीह विधानसभा खंड में आजसू आगे रही, फिर भी छह माह बाद ' +
      'विधानसभा सीट झामुमो ने जीती। उपचुनाव के लिए यही सबसे महत्वपूर्ण प्रवृत्ति है।',
    sources: 'HLD §1.1, module 6',
  },
  {
    slug: 'jlkm-factor',
    titleEn: 'The JLKM factor',
    titleHi: 'जेएलकेएम का प्रभाव',
    bodyEn:
      'Jairam Mahato\'s JLKM polled about 27% across the parliamentary seat in 2024 but only ' +
      '5.2% (10,787 votes) in the assembly seat. Against a margin of 3,838, where that vote ' +
      'goes decides the by-election. The scenario page models the split directly.',
    bodyHi:
      'जयराम महतो की जेएलकेएम ने 2024 में संसदीय क्षेत्र में लगभग 27% मत पाए, पर विधानसभा में ' +
      'केवल 5.2% (10,787 मत)। 3,838 के अंतर के सामने यह मत निर्णायक है। परिदृश्य पृष्ठ पर इसका ' +
      'सीधा मॉडल है।',
    sources: 'HLD §1.1, §1.2',
  },
  {
    slug: 'geography',
    titleEn: 'How the seat is built up',
    titleHi: 'क्षेत्र की संरचना',
    bodyEn:
      'Giridih Municipal Corporation (36 wards), Giridih Block (15 panchayats) and Pirtand Block ' +
      '(17 panchayats, including the Parasnath / Madhuban belt). The polling station is the atomic ' +
      'unit; everything rolls up from there. Polling stations are renumbered at every revision, so ' +
      'no multi-year comparison is valid unless it goes through the booth crosswalk.',
    bodyHi:
      'गिरिडीह नगर निगम (36 वार्ड), गिरिडीह प्रखंड (15 पंचायत) और पीरटांड़ प्रखंड (17 पंचायत, ' +
      'पारसनाथ/मधुबन क्षेत्र सहित)। मतदान केंद्र मूल इकाई है। हर पुनरीक्षण में केंद्र संख्या बदलती है, ' +
      'इसलिए बहु-वर्षीय तुलना बूथ-क्रॉसवॉक के बिना मान्य नहीं।',
    sources: 'HLD §3',
  },
  {
    slug: 'data-limits',
    titleEn: 'What this system cannot tell you',
    titleHi: 'यह प्रणाली क्या नहीं बता सकती',
    bodyEn:
      'How any individual voted, or any individual\'s community — no individual voter records are ' +
      'held. Booth results for elections whose Form 20 is not loaded. The true party affiliation ' +
      'of panchayat candidates, since those polls are party-less. And anything about a future ' +
      'result: scenarios are arithmetic on stated assumptions, not forecasts.',
    bodyHi:
      'किसी व्यक्ति ने कैसे मतदान किया या किसी व्यक्ति की जाति — व्यक्तिगत रिकॉर्ड रखे ही नहीं जाते। ' +
      'जिन चुनावों का फॉर्म 20 लोड नहीं है उनके बूथवार परिणाम। पंचायत प्रत्याशियों की वास्तविक ' +
      'दलीय संबद्धता। और भविष्य का कोई परिणाम — परिदृश्य केवल मान्यताओं पर गणित हैं।',
    sources: 'HLD §5, §12; LLD §12',
  },
]

export default function Factors() {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold">{t('nav.factors')}</h1>
      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        These cards are curated by the analyst team and are also what the assistant reads, so the
        dashboard and the chat answer from the same notes.
      </p>

      <div className="grid gap-3 md:grid-cols-2">
        {CARDS.map((card) => (
          <article key={card.slug} className="card px-4 py-3">
            <h2 className="text-sm font-semibold">{hi ? card.titleHi : card.titleEn}</h2>
            <p className="mt-1.5 text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              {hi ? card.bodyHi : card.bodyEn}
            </p>
            <p className="mt-2 text-2xs" style={{ color: 'var(--text-muted)' }}>
              {t('common.source')}: {card.sources}
            </p>
          </article>
        ))}
      </div>
    </div>
  )
}
