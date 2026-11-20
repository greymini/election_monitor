"""All user-facing bot text. Hindi only; no user-facing strings elsewhere."""

# ---- linking and menu (slice 1a) ----
UNREGISTERED = "यह बॉट केवल पंजीकृत सदस्यों के लिए है।"

WELCOME = "स्वागत है, {name}! आपका टेलीग्राम खाता जुड़ गया है।"
LINK_INVALID = "यह लिंक मान्य नहीं है या इसकी समय-सीमा समाप्त हो गई है। कृपया नया लिंक माँगें।"
ACCOUNT_IN_USE = "यह टेलीग्राम खाता पहले से किसी दूसरे सदस्य से जुड़ा है।"

MENU_PROMPT = "क्या करना है? नीचे से चुनें।"
BTN_EXPENSE = "खर्च"
BTN_REPORT = "रिपोर्ट"
BTN_ME = "मेरा हिसाब"

COMING_SOON = "जल्द आ रहा है"

# ---- general ----
FAILED_NOT_SAVED = "माफ़ कीजिए, कुछ गड़बड़ हो गई। आपका संदेश सहेजा नहीं गया। कृपया इसे दोबारा भेजें।"
NO_FINANCE_PERMISSION = "आपको खर्च दर्ज करने या हिसाब देखने की अनुमति नहीं है।"
CARD_STALE = "यह कार्ड अब मान्य नहीं है। कृपया नीचे वाला कार्ड इस्तेमाल करें या नया संदेश भेजें।"
CARD_EXPIRED = "यह कार्ड 15 मिनट बाद समाप्त हो गया। कृपया खर्च फिर से भेजें।"
USE_BUTTONS = "कृपया नीचे दिए बटन से चुनें, या रद्द करें।"
CANCELLED = "रद्द किया गया। कुछ भी दर्ज नहीं हुआ।"
CANCEL_WORDS = ("रद्द", "रद्द करें", "cancel", "radd", "stop")

BTN_CANCEL = "रद्द करें"
BTN_SKIP = "छोड़ें"
BTN_BACK = "वापस"
BTN_YES_CORRECT = "हाँ, सही है"

# ---- amounts ----
ASK_AMOUNT = "कितना खर्च हुआ? रकम और विवरण लिखें, जैसे: 1500 चाय नाश्ता"
AMOUNT_NOT_UNDERSTOOD = "रकम समझ नहीं आई। संदेश की शुरुआत में रकम लिखें, जैसे: 1500 चाय नाश्ता"
AMOUNT_ERRORS = {
    "zero": "रकम शून्य नहीं हो सकती। सही रकम लिखें।",
    "negative": "रकम माइनस में नहीं हो सकती। सही रकम लिखें।",
    "decimals": "पैसे दो अंकों तक ही लिखें, जैसे 150.50",
    "too_large": "यह रकम बहुत बड़ी है। जाँच कर फिर से लिखें।",
    "malformed": "रकम ठीक से नहीं लिखी गई। जैसे 1500 या 1,500 या 1,50,000 लिखें।",
}
CONFIRM_BIG_AMOUNT = "{amount} बहुत बड़ी रकम है। क्या यह रकम सही है?"

# ---- bucket, label, area ----
PICK_BUCKET = "{amount} किस पर खर्च हुआ?"
BTN_MORE_BUCKETS = "और श्रेणियाँ"
BTN_OTHER = "अन्य"
ASK_LABEL = "{amount} · क्या था? छोटे में लिखें (40 अक्षर तक)।"
LABEL_TOO_LONG = "कृपया 40 अक्षर से छोटा लिखें।"
PICK_AREA = "{amount} · किस क्षेत्र में खर्च हुआ?"

# ---- confirmation card ----
SUGGESTED = "(सुझाव)"
TODAY = "आज"
YESTERDAY = "कल"
NO_AREA = "क्षेत्र: —"
SUBHEAD_SEP = " › "
OTHER_LABEL = "अन्य: {label}"
LINE_REMARK = "विवरण: {remark}"
LINE_FUND = "पैसा: {fund}"
LINE_REACH = "पहुँच: {count} {unit}"
DRAFT_REPLACED = "पिछला अधूरा खर्च ({amount}) छोड़ दिया गया।"
BTN_CONFIRM = "सही है, दर्ज करें"
BTN_CHANGE_BUCKET = "श्रेणी बदलें"
BTN_CHANGE_AREA = "क्षेत्र बदलें"
BTN_DETAILS = "और विवरण"

# ---- warnings (one extra tap, never a block) ----
WARN_HEADER = "दर्ज करने से पहले ध्यान दें:"
WARNINGS = {
    "large": "• बड़ी रकम ({limit} या अधिक)। यह जाँच सूची में दिखेगी।",
    "cash_over_limit": "• चुनाव अवधि में {limit} से अधिक का नकद भुगतान।",
    "sensitive_in_window": "• चुनाव अवधि में यह श्रेणी संवेदनशील है। कोषाध्यक्ष इसकी समीक्षा करेंगे।",
}
BTN_POST_ANYWAY = "फिर भी दर्ज करें"

# ---- details ("और विवरण") ----
ASK_SUBHEAD = "उप-श्रेणी चुनें:"
ASK_DATE = "कब खर्च हुआ? बटन चुनें या तारीख लिखें, जैसे 15/11"
DATE_INVALID = "तारीख समझ नहीं आई। जैसे 15/11 या 15/11/2026 लिखें।"
DATE_FUTURE = "आने वाली तारीख नहीं हो सकती।"
ASK_PAY_MODE = "भुगतान कैसे हुआ?"
ASK_FUND = "पैसा कहाँ से दिया?"
ASK_REACH = "कितने {unit} तक पहुँचा? संख्या लिखें, या इकाई बदलें।"
REACH_INVALID = "कृपया 1 या उससे बड़ी संख्या लिखें।"
# weekly digest (LLD §5.4, Monday 09:00)
WEEKLY_TITLE = "साप्ताहिक सारांश ({start} से {end})"
WEEKLY_REPORTS = "रिपोर्ट: {filed} आईं, {closed} बंद हुईं"
WEEKLY_ISSUES = "मुद्दे: {opened} खुले, {closed} बंद हुए"
WEEKLY_ACK = "देख लेने में औसत (माध्यिका) समय: {minutes} मिनट"
WEEKLY_ACK_NONE = "देख लेने का समय: इस हफ़्ते कोई आँकड़ा नहीं"
WEEKLY_QUIET = "शांत सदस्य (पूरे हफ़्ते न खर्च, न रिपोर्ट): {n}"
WEEKLY_AND_MORE = " और {n} अन्य"
WEEKLY_GAP_TITLE = "ज़रूरत बनाम खर्च — सबसे बड़ा अंतर (शीर्ष 5 क्षेत्र):"
WEEKLY_GAP_LINE = "{area}: अंतर {gap} (खुली रिपोर्ट {reports}, कुल खर्च {spend})"
WEEKLY_GAP_NOTE = "यह संबंध दिखाता है, कारण नहीं।"
# impact follow-up (LLD §7)
FOLLOWUP_ASK = "काम पूरा हुआ? {date} को {bucket} में {amount} का खर्च ({txn_no}) दर्ज हुआ था।"
FOLLOWUP_REMINDER_PREFIX = "याद दिला रहे हैं: "
BTN_FOLLOWUP_DONE = "हाँ"
BTN_FOLLOWUP_PARTIAL = "आंशिक"
BTN_FOLLOWUP_NOT_DONE = "नहीं"
FOLLOWUP_ANSWERS = {"done": "हाँ", "partial": "आंशिक", "not_done": "नहीं"}
FOLLOWUP_THANKS = "धन्यवाद, दर्ज किया: {answer}"
FOLLOWUP_ALREADY = "इसका जवाब पहले ही दर्ज है।"
FOLLOWUP_CLOSED = "यह सवाल अब बंद है: खर्च रद्द हुआ या दूसरी श्रेणी में चला गया। जवाब की ज़रूरत नहीं।"
FOLLOWUP_PHOTO_ASK = "चाहें तो काम की एक फोटो भेजें (10 मिनट तक)।"
FOLLOWUP_PHOTO_SAVED = "फोटो जोड़ दी, धन्यवाद।"
FOLLOWUP_NO_PHOTO = "ठीक है, बिना फोटो के दर्ज है।"
ASK_REPORT_LINK = "किस रिपोर्ट के लिए? (आपकी खुली रिपोर्ट; ज़रूरी नहीं)"
LINE_REPORT = "रिपोर्ट के लिए: {report_no}"
ASK_REMARK = "कोई टिप्पणी? लिखकर भेजें। मोबाइल, आधार या वोटर आईडी नंबर न लिखें।"
REMARK_TOO_LONG = "टिप्पणी 500 अक्षर से छोटी रखें।"

PAY_MODES = {
    "cash": "नकद",
    "upi": "UPI",
    "bank_transfer": "बैंक ट्रांसफर",
    "cheque": "चेक",
    "credit": "उधार (बाद में देना है)",
    "in_kind": "सामान के रूप में",
}
FUND_SOURCES = {
    "own": "खुद का",
    "family_business": "परिवार / व्यवसाय",
    "party": "पार्टी से",
    "supporter_cash": "समर्थक (नकद)",
    "supporter_in_kind": "समर्थक (सामान)",
    "advance": "मिली हुई रकम से",
    "own_pocket_reimbursable": "अपनी जेब से (वापस मिलना है)",
    "other": "अन्य",
}
REACH_UNITS = {"person": "लोगों", "family": "परिवारों", "village": "गाँवों", "event": "कार्यक्रमों"}

# ---- PII ----
PII_REJECTED = "इसमें {what} जैसा नंबर है। कृपया उसे हटाकर फिर से लिखें।"
PII_KINDS = {"epic": "वोटर आईडी", "aadhaar": "आधार", "mobile": "मोबाइल"}
PII_JOIN = " / "

# ---- posting and undo ----
POSTED = "दर्ज हुआ: {txn_no}"
BALANCE_HELD = "आपके पास शेष: {amount}"
BALANCE_OWED = "आपको मिलना है: {amount}"
BTN_UNDO = "गलती हुई, हटाएँ"
ALREADY_POSTED = "यह पहले ही दर्ज हो चुका है: {txn_no}"
NOT_SAVED = "यह खर्च दर्ज नहीं हो सका। कृपया फिर से भेजें।"
UNDONE = "हटाया गया: {txn_no}"
UNDONE_TOAST = "हटा दिया गया।"
UNDO_EXPIRED = "{minutes} मिनट बीत चुके हैं, अब यह यहाँ से नहीं हटेगा। ज़रूरत हो तो कोषाध्यक्ष से बात करें।"
ALREADY_UNDONE = "यह पहले ही हटाया जा चुका है।"

# ---- expiry ----
DRAFT_EXPIRED = "आपका अधूरा खर्च ({amount}) 15 मिनट में पूरा नहीं हुआ, इसलिए सहेजा नहीं गया। कृपया फिर से भेजें।"
DRAFT_EXPIRED_NO_AMOUNT = "आपका अधूरा खर्च 15 मिनट में पूरा नहीं हुआ, इसलिए सहेजा नहीं गया। कृपया फिर से भेजें।"

# ---- "मेरा हिसाब" ----
ME_TITLE = "मेरा हिसाब"
ME_IN_TRANSIT = "पुष्टि बाकी (रास्ते में): {amount}"
ME_RECENT = "पिछली प्रविष्टियाँ:"
ME_NONE = "अभी कोई प्रविष्टि नहीं है।"
ME_ENTRY = "{txn_no} · {date} · {amount} · {label}"
KIND_LABELS = {
    "expense": "खर्च",
    "advance": "पैसा दिया / मिला",
    "return": "पैसा लौटाया",
    "inflow": "बाहर से मिला",
    "adjustment": "समायोजन",
}

# ---- drafts of any flow ----
DRAFT_ABANDONED = "पिछला अधूरा काम छोड़ दिया गया।"
DRAFT_EXPIRED_GENERIC = "आपका अधूरा काम 15 मिनट में पूरा नहीं हुआ, इसलिए सहेजा नहीं गया। कृपया फिर से शुरू करें।"

# ---- advance: "पैसा दिया" (LLD §5.2) ----
BTN_GIVE = "पैसा दिया"
ADVANCE_VERBS = ("दिया", "दिए", "दिये", "diya", "diye", "diyaa", "dia")
NO_ADVANCE_PERMISSION = "आपको पैसा देने (एडवांस) की अनुमति नहीं है।"
NO_RECEIVERS = "आपकी टीम में ऐसा कोई सदस्य नहीं है जिसे पैसा दिया जा सके।"
ASK_PERSON = "किसे पैसा दिया?"
NAME_NOT_FOUND = "\"{name}\" नाम का कोई सदस्य नहीं मिला। सूची से चुनें:"
NAME_SEVERAL = "\"{name}\" से कई सदस्य मिले। सही व्यक्ति चुनें:"
BTN_NEXT_PAGE = "आगे"
BTN_ALL_PEOPLE = "सब लोग"
ASK_ADVANCE_AMOUNT = "{name} को कितना दिया? रकम लिखें।"
ADVANCE_LINE = "{amount} → {name} · {day} · {mode}"
LINE_PURPOSE = "किस काम के लिए: {purpose}"
BTN_RECORD = "दर्ज करें"
BTN_PURPOSE = "किस काम के लिए (वैकल्पिक)"
ASK_PURPOSE = "किस काम के लिए दिया? छोटे में लिखें।"
WAITING_ACK = "पुष्टि का इंतज़ार है।"
RECEIVER_NOT_LINKED = "{name} बॉट से जुड़े नहीं हैं, इसलिए उन्हें संदेश नहीं गया। प्रविष्टि पुष्टि के इंतज़ार में रहेगी।"

# ---- acknowledgement card ----
ACK_REQUEST_ADVANCE = "{giver} ने दर्ज किया है कि आपको {amount} दिया गया। ({txn_no})"
ACK_REQUEST_RETURN = "{giver} ने दर्ज किया है कि उन्होंने आपको {amount} लौटाए। ({txn_no})"
ACK_REMINDER = "याद दिला रहे हैं: इसकी पुष्टि अभी बाकी है।"
BTN_ACK_YES = "हाँ, मिला"
BTN_ACK_DIFF = "रकम अलग है"
BTN_ACK_NO = "नहीं मिला"
ACKED_CARD = "पुष्टि की गई: {amount} मिला ({txn_no})"
DISPUTED_CARD = "दर्ज किया गया: {reason} ({txn_no})। देने वाले और कोषाध्यक्ष को बता दिया गया है।"
DISPUTE_REASONS = {"amount_differs": "रकम अलग है", "not_received": "नहीं मिला"}
ACK_NOTICE_TO_GIVER = "{receiver} ने {amount} मिलने की पुष्टि की ({txn_no})।"
DISPUTE_NOTICE = "विवाद: {receiver} ने {txn_no} ({amount}, {giver} से) पर कहा: {reason}। वेब पर जाँच कर ठीक करें।"
ALREADY_ACKED = "इसकी पुष्टि पहले ही हो चुकी है।"
ALREADY_DISPUTED = "इस पर विवाद पहले ही दर्ज है।"
ENTRY_REMOVED = "यह प्रविष्टि हटा दी गई है।"
REMOVED_NOTICE = "{giver} ने {txn_no} ({amount}) की प्रविष्टि हटा दी। अब पुष्टि की ज़रूरत नहीं।"
UNDO_NOT_ALLOWED = "दूसरी ओर से जवाब आ चुका है, अब यह यहाँ से नहीं हटेगा। ज़रूरत हो तो कोषाध्यक्ष से बात करें।"

# ---- return: "पैसा लौटाया" ----
BTN_RETURN = "पैसा लौटाया"
ASK_RETURN_TO = "किसे पैसा लौटाया?"
ASK_RETURN_AMOUNT = "{name} को कितना लौटाया? रकम लिखें। आपके पास शेष: {balance}"
RETURN_LINE = "{amount} लौटाया → {name} · {day} · {mode}"
RETURN_TOO_MUCH = "आपके पास केवल {balance} शेष है। उससे ज़्यादा नहीं लौटा सकते।"
NOTHING_TO_RETURN = "लौटाने के लिए आपके पास कोई शेष नहीं है।"
NO_GIVER = "आपको किसी ने एडवांस नहीं दिया है, इसलिए लौटाने को कुछ नहीं है।"

# ---- "मेरा हिसाब" additions ----
ME_DISPUTED = "विवाद में: {amount}"
ME_WAITING = "आपकी पुष्टि बाकी:"
ME_WAITING_ENTRY = "{txn_no} · {amount} · {name} से"
BTN_ACK_OPEN = "पुष्टि: {txn_no}"
ME_TEAM = "आपकी टीम:"
ME_TEAM_ENTRY = "{name}: {balance}"
TEAM_HELD = "शेष {amount}"
TEAM_OWED = "मिलना है {amount}"
XFER_LABELS = {
    ("advance", "in"): "{name} से मिला",
    ("advance", "out"): "{name} को दिया",
    ("return", "in"): "{name} से वापस मिला",
    ("return", "out"): "{name} को लौटाया",
}
STATUS_SUFFIX = {"pending_ack": " (पुष्टि बाकी)", "disputed": " (विवाद)"}
BTN_LAST_MONTH = "पिछला महीना"
LAST_MONTH_TITLE = "पिछला महीना: {month} {year}"
LM_SPENT = "खर्च: {amount} ({count} प्रविष्टियाँ)"
LM_RECEIVED = "मिला: {amount}"
LM_PASSED = "आगे दिया: {amount}"
LM_RETURNED = "लौटाया: {amount}"
LM_TOP = "सबसे ज़्यादा खर्च:"
LM_TOP_ENTRY = "{name}: {amount}"
LM_NONE = "उस महीने कोई प्रविष्टि नहीं है।"
MONTHS = {1: "जनवरी", 2: "फ़रवरी", 3: "मार्च", 4: "अप्रैल", 5: "मई", 6: "जून", 7: "जुलाई", 8: "अगस्त",
          9: "सितंबर", 10: "अक्टूबर", 11: "नवंबर", 12: "दिसंबर"}

# ---- ground reports (LLD §5.3) ----
URGENT_WORDS = ("तुरंत", "तुरन्त", "turant", "urgent")
REPORT_RECEIVED = "रिपोर्ट मिली: {items}"
ITEM_PHOTO = "{n} फोटो"
ITEM_VOICE = "{n} आवाज़ संदेश"
ITEM_VIDEO = "{n} वीडियो"
ITEM_DOC = "{n} फ़ाइल"
ITEM_LOCATION = "लोकेशन ✓"
ITEM_NOTE = "टिप्पणी ✓"
ITEMS_JOIN = ", "
REPORT_AREA = "क्षेत्र: {area}"
REPORT_NOTE_PREVIEW = "टिप्पणी: {text}"
ASK_CATEGORY = "यह किस बारे में है?"
ASK_URGENT = "क्या इस पर तुरंत ध्यान चाहिए?"
BTN_URGENT_YES = "हाँ, तुरंत"
BTN_URGENT_NO = "सामान्य"
REPORT_FILED = "दर्ज हुई: {report_no}"
REPORT_FILED_URGENT = "दर्ज हुई (तुरंत): {report_no}"
REPORT_AUTOFILED = "आपकी रिपोर्ट अपने आप दर्ज हो गई: {report_no}। श्रेणी छाँटना बाकी है, नीचे से चुन सकते हैं।"
REPORT_SUPERSEDED = "पिछली रिपोर्ट दर्ज हो गई: {report_no}। श्रेणी छाँटना बाकी है, नीचे से चुन सकते हैं।"
REPORT_CATEGORY_LINE = "श्रेणी: {name}"
REPORT_CATEGORY_PENDING = "श्रेणी: छाँटना बाकी"
REPORT_URGENT_LINE = "तुरंत ध्यान चाहिए"
BTN_CHANGE_PLACE = "जगह बदलें"
BTN_SEND_LOCATION = "लोकेशन भेजें"
BTN_ADD_MORE = "और जोड़ें"
BTN_LOCATION_REQUEST = "📍 लोकेशन भेजें"
ADD_MORE_OPEN = "और फोटो, आवाज़ संदेश या टिप्पणी भेजें। अगले {minutes} मिनट तक सब रिपोर्ट {report_no} में जुड़ेगा।"
ASK_LOCATION = "नीचे के बटन से अपनी लोकेशन भेजें।"
LOCATION_SAVED = "लोकेशन जुड़ गई: {report_no}"
ASK_REPORT_AREA = "रिपोर्ट किस क्षेत्र की है?"
CATEGORY_ALREADY_SET = "श्रेणी पहले ही चुनी जा चुकी है।"
REPORT_MERGED_ALREADY = "यह रिपोर्ट {report_no} में मिला दी गई है, इसलिए इसे अब यहाँ से नहीं बदला जा सकता।"
REPORT_AREA_LOCKED = "यह रिपोर्ट मुद्दे {issue_no} से जुड़ी है, इसलिए इसका क्षेत्र यहाँ से नहीं बदल सकता।"
MEDIA_TOO_BIG = "यह फ़ाइल बहुत बड़ी है ({mb} MB से ज़्यादा)। कृपया छोटी क्लिप भेजें।"
MEDIA_IMAGE_UNSUPPORTED = "यह तस्वीर फ़ाइल के रूप में नहीं ली जा सकती। कृपया इसे फोटो की तरह भेजें।"
MEDIA_TYPE_UNSUPPORTED = "यह फ़ाइल प्रकार नहीं लिया जा सकता। फोटो, आवाज़ संदेश, वीडियो या PDF भेजें।"
PHOTO_LIMIT = "एक रिपोर्ट में {n} फोटो तक ही जुड़ सकती हैं। बाकी फोटो नहीं जोड़ी गईं।"
VOICE_LIMIT = "एक रिपोर्ट में कुल {minutes} मिनट तक की आवाज़ ही जुड़ सकती है। कृपया छोटा संदेश भेजें।"
MEDIA_FAILED = "आपकी रिपोर्ट {report_no} की एक फ़ाइल डाउनलोड नहीं हो सकी। कृपया उसे दोबारा भेजें।"
MEDIA_FAILED_DRAFT = "आपकी रिपोर्ट की एक फ़ाइल डाउनलोड नहीं हो सकी। कृपया उसे दोबारा भेजें।"
REPORT_STATUS = {"draft": "ड्राफ्ट", "new": "नई", "acknowledged": "देख लिया गया", "assigned": "सौंपी गई",
                 "in_progress": "काम जारी", "resolved": "हल हुई", "closed_no_action": "बंद", "duplicate": "दोहराव"}
ME_REPORTS = "मेरी रिपोर्ट:"
ME_REPORT_ENTRY = "{report_no} · {date} · {category} · {status}"
ME_REPORT_NO_NUMBER = "ड्राफ्ट"
CATEGORY_PENDING_SHORT = "छाँटना बाकी"

# ---- expense receipt (LLD §5.1 "और विवरण") and media during an expense ----
ASK_RECEIPT = "रसीद की फोटो या PDF भेजें।"
LINE_RECEIPT = "रसीद: ✓"
HOLD_QUESTION = "आपने एक फोटो/फ़ाइल भेजी है। यह किसके लिए है?"
HOLD_QUESTION_NO_RECEIPT = "आपने लोकेशन या फ़ाइल भेजी है, जो रसीद नहीं हो सकती। क्या नई रिपोर्ट बनानी है?"
BTN_HOLD_RECEIPT = "इस खर्च की रसीद"
BTN_HOLD_REPORT = "नहीं, नई रिपोर्ट"
BTN_HOLD_LATER = "खर्च पूरा होने के बाद"
HOLD_RECEIPT_SET = "रसीद जोड़ दी गई।"
HOLD_KEPT = "ठीक है। खर्च पूरा होने के बाद ये एक नई रिपोर्ट में जुड़ जाएँगी।"
HELD_TO_REPORT = "खर्च के दौरान भेजी गई चीज़ें एक नई रिपोर्ट में रखी गई हैं।"

# ---- report alerts and triage (LLD §5.3, §5.4) ----
ALERT_URGENT = "🔴 तुरंत: {category}"
ALERT_NORMAL = "नई रिपोर्ट: {category}"
ALERT_RESTRICTED = "🔒 {category}"
ALERT_UNSORTED = "छाँटना बाकी: {report_no}"
ALERT_ESCALATION = "⏰ {minutes} मिनट से लंबित: {category}"
ALERT_ASSIGNEE = "आपको सौंपी गई: {category}"
ALERT_PLACE_TIME = "{area} · {time}"
ALERT_REPORTER = "रिपोर्टर: {name}"
ALERT_REF = "संदर्भ: {report_no}"
ALERT_DUPLICATE_HINT = "संभवतः इनके जैसी: {numbers}"
# transcripts (LLD §6): machine text is always labelled; a person's correction is shown instead
MACHINE_WRITTEN = "मशीन द्वारा लिखा गया"
CARD_TRANSCRIPT_MACHINE = "🎙 मशीन द्वारा लिखा गया ({engine}):\n{text}"
CARD_TRANSCRIPT_HUMAN = "🎙 आवाज़ का लिखित रूप (जाँचा गया):\n{text}"
CARD_SUGGESTED_CATEGORY = "सुझाव (मशीन, आवाज़ से): {category}"
CARD_SUGGESTED_URGENT = "सुझाव (मशीन, आवाज़ से): अत्यावश्यक हो सकता है"
SUGGESTED_MARK = "★ सुझाव · "
# issues (slice 3)
BTN_ISSUE_LINK = "मुद्दे से जोड़ें"
BTN_ISSUE_NEW = "➕ नया मुद्दा"
ASK_ISSUE = "इस रिपोर्ट को किस मुद्दे से जोड़ें? (इसी क्षेत्र के खुले मुद्दे)"
ASK_ISSUE_NONE_OPEN = "इस क्षेत्र में कोई खुला मुद्दा नहीं है। नया मुद्दा शुरू करें?"
CARD_ISSUE = "मुद्दा: {issue_no} · {title}"
ISSUE_LINKED = "मुद्दा {issue_no} से जोड़ दिया।"
ISSUE_CREATED = "नया मुद्दा {issue_no} शुरू किया और रिपोर्ट जोड़ दी।"
ISSUE_NOT_POSSIBLE = "यह रिपोर्ट मुद्दे से नहीं जुड़ सकती (क्षेत्र नहीं है या मुद्दा बंद है)।"
ALERT_SEEN_BY = "देख लिया: {name}, {time}"
ALERT_ASSIGNED_TO = "सौंपी गई: {name}"
ALERT_STATUS = "स्थिति: {status}"
BTN_SEEN = "देख लिया"
BTN_ASSIGN = "सौंपें"
BTN_TALK_TO_REPORTER = "रिपोर्टर से बात करें"
BTN_MERGE_INTO = "मिलाएँ: {report_no}"
BTN_WORK_START = "काम शुरू"
BTN_WORK_DONE = "पूरा हुआ"
BTN_WORK_CANNOT = "काम नहीं हो सकता"
ASK_ASSIGNEE = "किसे सौंपें?"
ALREADY_SEEN = "पहले ही {name} ने {time} पर देख लिया।"
NOT_ALLOWED = "आप यह नहीं कर सकते।"
STATUS_NOT_ALLOWED = "इस स्थिति में यह नहीं हो सकता।"
CLOSE_EXPIRED = "बंद करने का यह कदम 15 मिनट बाद समाप्त हो गया। रिपोर्ट अभी बंद नहीं हुई है; अपने कार्ड पर फिर से बटन दबाएँ।"
ASK_CLOSE_NOTE = "क्या किया गया? छोटे में लिखें।"
ASK_CLOSE_PHOTO = "काम की एक फोटो भेजें (वैकल्पिक)।"
CLOSED_DONE = "बंद की गई: {report_no} · {status}"

# ---- feedback to the reporter ----
FEEDBACK_SEEN = "आपकी रिपोर्ट {report_no} देख ली गई है।"
FEEDBACK_RESOLVED = "आपकी रिपोर्ट {report_no} पर काम पूरा हुआ।"
FEEDBACK_CLOSED = "आपकी रिपोर्ट {report_no} बंद की गई।"
FEEDBACK_NOTE = "टिप्पणी: {note}"
FEEDBACK_DUPLICATE = "आपकी रिपोर्ट {report_no} को {into} के साथ जोड़ दिया गया है। आगे की जानकारी {into} पर मिलेगी।"

# ---- daily digest ----
DIGEST_TITLE = "आज का सार ({date})"
DIGEST_REPORTS_TODAY = "आज की रिपोर्ट: {n}"
DIGEST_BY_CATEGORY = "श्रेणी के अनुसार:"
DIGEST_BY_AREA = "क्षेत्र के अनुसार:"
DIGEST_LINE = "• {name}: {n}"
DIGEST_OPEN_URGENT = "खुली तुरंत रिपोर्ट: {n}"
DIGEST_URGENT_LINE = "• {report_no} · {category} · {status} · {hours} घंटे"
DIGEST_ESCALATED = "आज बढ़ाई गई (देखी नहीं गई): {n}"
DIGEST_SPEND_TODAY = "आज का खर्च: {amount}"
DIGEST_SPEND_LINE = "• {name}: {amount}"
DIGEST_UNCONFIRMED = "पुष्टि बाकी (एडवांस/वापसी): {n} · {amount}"
DIGEST_DISPUTED = "विवाद में: {n} · {amount}"
DIGEST_NONE = "आज कोई रिपोर्ट नहीं आई।"

# ---- nudge ----
NUDGE = "आपके पास {amount} शेष है। कोई खर्च दर्ज करना है?"

# ---- money notices through the queue (1c) ----
RECEIVER_QUEUED = "{name} को पुष्टि के लिए संदेश भेजा जा रहा है।"
RECEIVER_UNDELIVERED = "{name} को {txn_no} का पुष्टि संदेश नहीं पहुँच सका। प्रविष्टि पुष्टि के इंतज़ार में है।"

# ---- PIN (LLD §11) ----
BTN_PIN_SET = "पिन सेट करें"
BTN_PIN_CHANGE = "पिन बदलें"
ASK_PIN_CURRENT = "अपना मौजूदा पिन भेजें। संदेश तुरंत मिटा दिया जाएगा।"
ASK_PIN_NEW = "नया पिन भेजें (4 से 6 अंक)। संदेश तुरंत मिटा दिया जाएगा।"
ASK_PIN_CONFIRM = "वही पिन दोबारा भेजें।"
ASK_PIN_ACTION = "{amount} दर्ज करने के लिए अपना पिन भेजें। संदेश तुरंत मिटा दिया जाएगा।"
PIN_WEAK = "यह पिन बहुत आसान है। 4 से 6 अंक लिखें, सभी एक जैसे या 1234 जैसे क्रम में नहीं।"
PIN_MISMATCH = "दोनों पिन मेल नहीं खाते। फिर से शुरू करें।"
PIN_SAVED = "पिन सेट हो गया।"
PIN_SAVED_RETRY = "पिन सेट हो गया। अब खर्च/पैसा दिया कार्ड पर फिर से दर्ज करें दबाएँ।"
PIN_WRONG = "पिन गलत है। {left} प्रयास बाकी।"
PIN_LOCKED = "तीन बार गलत पिन। पैसे से जुड़े काम बंद कर दिए गए हैं। एडमिन से खुलवाएँ।"
PIN_LOCKED_MONEY = "आपके पैसे से जुड़े काम बंद हैं (गलत पिन)। एडमिन से खुलवाएँ।"
PIN_NEEDED_FIRST = "{amount} या उससे अधिक के लिए पिन ज़रूरी है। पहले पिन सेट करें।"
PIN_EXPIRED_ENTRY = "पिन का समय (2 मिनट) निकल गया, इसलिए एंट्री दर्ज नहीं हुई। रकम फिर से भेजें।"
PIN_EXPIRED_SETUP = "पिन का समय (2 मिनट) निकल गया। मेरा हिसाब में पिन सेट करें फिर से दबाएँ।"
PIN_NOT_DELETED = "सुरक्षा के लिए पिन वाला संदेश खुद मिटा दें।"
PIN_OK_POSTING = "पिन सही है।"
PIN_SENT_UNASKED = "यह आपका पिन है, इसलिए संदेश हटा दिया गया और कुछ दर्ज नहीं हुआ। पिन तभी भेजें जब बॉट पूछे।"


# ---- replies delivered late by the outbox (pulse/bot/outgoing.py): the input was already saved ----
REPLY_SAVED = "✅ यह पहले ही सहेजा जा चुका है — दोबारा न भेजें।"
REPLY_TXN = "प्रविष्टि {txn_no} · राशि {amount}"
REPLY_TXN_VOID = "यह प्रविष्टि दर्ज हुई थी और बाद में हटा दी गई: {txn_no}"
REPLY_REPORT_DRAFT = "आपकी भेजी चीज़ें रिपोर्ट के मसौदे में सहेजी गई हैं। रिपोर्ट अपने आप दर्ज हो जाएगी।"
REPLY_FOLLOWUP = "आपका जवाब दर्ज हो चुका है: {txn_no}"
REPLY_PIN = "आपका पिन सेट हो चुका है।"
REPLY_RECEIVED = "आपका पिछला संदेश मिल गया था और सहेजा गया है। आगे बढ़ने के लिए यह कार्ड:"
