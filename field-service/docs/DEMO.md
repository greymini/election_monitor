# Local demo: five-minute walkthrough

The field screens of the Election Monitor run at `http://127.0.0.1:8787/` on the sample data: spend
overview, ledger, team balances, add entry, election register, reports, map, issues, team and settings.
Sign in as any demo user; the dashed DEV strip under the header switches who you are ("view as") so
scoping can be seen. Screenshots of every screen are in `docs/screens/`.

The rest of this page walks through the **chat simulator** at `/demo`, a developer tool (not linked
from the app). It is a browser demo of both modules with no Telegram. **Dev-only: nothing in it ships.** Every name,
amount and report is invented.

## Start it

```
.venv\Scripts\activate                 # Windows; on macOS/Linux: source .venv/bin/activate
pip install -c constraints.txt -e ".[dev]"   # once
python -m scripts.demo                 # add --port N to change port 8787
```

No `.env` is needed. The first run also needs Node.js (18 or later; built with 24): the script runs `npm ci` and `npm run build`
in `web/` (and rebuilds whenever a source file is newer than the build). The script then:

1. Starts its own embedded PostgreSQL in `.demo-pgdata/` (your `.pgdata/` is not touched).
2. Rebuilds the `pulse_demo` database from scratch.
3. Seeds the dev team, links each member to an invented Telegram account, and loads the sample data.
4. Prints two URLs: the app at `http://127.0.0.1:8787/` and the chat simulator at `/demo`.

Loading takes about a minute. Ctrl+C stops the server and the database. Every run starts fresh.

The sample data covers:

- 43 expenses across buckets and areas (three under Other with the same label, ready to promote).
- An election window with a ceiling and a cash limit, and four buckets mapped to statutory heads.
- Advances in every state: acknowledged, waiting, overdue and disputed.
- Two returns.
- 10 reports in different categories and statuses, with placeholder photos, transcribed voice notes
  and, for seven of them, a map location.
- Two issues.
- One answered follow-up.
- Two core users who are not on the team yet, `usr-worker-c1` and `usr-worker-c2`, for trying
  **Import CSV** on the Team screen (the drawer offers a template).

## The page

- **Two phones.** Each one is a Telegram chat with the bot. Choose who is holding it from the list at
  the top of the phone. By default the left phone is the candidate (**डेमो प्रत्याशी**, principal) and
  the right one is **डेमो कार्यकर्ता क1** (worker A1).
- **Using a phone.** Type in the box and press Send, or tap the bot's buttons. 📎 attaches a photo,
  a voice note or a PDF. Text already in the box goes with the attachment as its caption.
- **What you see.** The bot's text is exactly what it would send. Cards it edits are updated in
  place and marked *edited*. Messages it deletes show as *message deleted*.
- **What happens behind it.** Every action is a Telegram update handed to the real dispatcher. The
  worker runs in the same process.
- **Panels.** On the right: **Ledger**, **Balances**, **Spend** (with need versus spend) and
  **Reports** (photo, transcript, timeline, linked issue and spend). They show the candidate's view
  and refresh after every action.
- **Skip.** The buttons at the top move the clock forward: 10 minutes, 1 hour, 1 day or 14 days.
  The worker then runs at once, so auto-filing, escalation, reminders, digests and follow-ups happen
  straight away. Time only moves forward. Restart the script to go back to the present.

The phones start empty. The sample history is in the panels.

## Walkthrough

### 1. An expense (worker A1, right phone), about 1 minute

1. Type `250 चाय नाश्ता`. The amount comes first; `250 chai` works too.
2. The bot replies with a card showing **₹250 · भोजन और नाश्ता (सुझाव) · डेमो पंचायत क1 · आज · नकद**.
   *(सुझाव)* means the bucket is the machine's suggestion. Tap **सही है, दर्ज करें**.
3. The card is edited to **दर्ज हुआ: 32-E-…** and shows the remaining balance.
4. **Ledger** now lists the entry at the top.

### 2. An advance, acknowledged on the other phone, about 1 minute

1. On the candidate's phone (left), type `दिया 5000 कार्यकर्ता क1` and tap **दर्ज करें**. The card
   says **पुष्टि का इंतज़ार है।**
2. **Balances** shows the ₹5,000 under *In transit* on the candidate's row.
3. Worker A1's phone gets **डेमो प्रत्याशी ने दर्ज किया है कि आपको ₹5,000 दिया गया।** Tap
   **हाँ, मिला**.
4. The candidate is told **डेमो कार्यकर्ता क1 ने ₹5,000 मिलने की पुष्टि की**. In **Balances**, A1's
   balance goes up and *In transit* goes down.
5. To try a dispute instead, tap **रकम अलग है** or **नहीं मिला**.

### 3. An urgent report: acknowledged, assigned and closed, about 2 minutes

1. On worker A1's phone, type `तुरंत: पुल के पास बस पलटी, कई लोग घायल`. A message starting with
   *तुरंत* is filed at once as urgent: **दर्ज हुई (तुरंत): 32-R-…**.
2. Tap the category **दुर्घटना / आपदा**. To add a photo or voice note, tap **और जोड़ें** and attach
   one. A voice note gets a transcript, marked as machine-written in **Reports**.
3. The candidate's phone shows a 🔴 alert card with buttons **देख लिया**, **सौंपें** and
   **मुद्दे से जोड़ें**.
4. Tap **देख लिया**. The card is edited to show who acknowledged it and when, and A1 is told
   **आपकी रिपोर्ट 32-R-… देख ली गई है।**
5. Tap **सौंपें**, then choose **डेमो कार्यकर्ता क1** under **किसे सौंपें?**.
6. A1 gets **आपको सौंपी गई: …** with **काम शुरू**, **पूरा हुआ** and **काम नहीं हो सकता**. Tap
   **पूरा हुआ**.
7. The bot asks **क्या किया गया? छोटे में लिखें।** Type `घायलों को अस्पताल पहुँचाया`.
8. Next it asks **काम की एक फोटो भेजें (वैकल्पिक)।** Attach a photo or tap **छोड़ें**.
9. A1 is told **आपकी रिपोर्ट 32-R-… पर काम पूरा हुआ।** with the note. The candidate's card now
   ends **स्थिति: हल हुई**, and **Reports** shows the timeline: filed, acknowledged, assigned,
   resolved.

**Escalation (optional).** Send another `तुरंत …` report, pick its category and leave it
unacknowledged. Press **Skip 1 hour**. The candidate gets **⏰ 60 मिनट से लंबित: …**, which also
names a possible duplicate.

### 4. A follow-up after 14 days, about 1 minute

1. On worker A1's phone, type `1800 चापाकल मरम्मत`. The suggested bucket is **छोटे विकास कार्य**
   (small works). Tap **सही है, दर्ज करें**.
2. Press **Skip 14 days**. Follow-ups are only asked between 10:00 and 20:00 IST, so if the jump lands
   outside those hours, the clock moves on to 10:05 IST. The page says when it does this.
3. A1 is asked **काम पूरा हुआ? … छोटे विकास कार्य में ₹1,800 का खर्च (32-E-…) दर्ज हुआ था।**
   Tap **हाँ**. The bot replies **धन्यवाद, दर्ज किया: हाँ** and offers to take a photo of the work.
4. The candidate's phone receives the weekly summary (**साप्ताहिक सारांश**), which ends with the
   need-versus-spend gaps. It also gets a follow-up for a sample relief entry.

## Good to know

- **Telegram's 48-hour delete window.** Press **Skip 1 day** before **Skip 14 days** to see chat
  cleanup: messages older than 24 hours turn into *message deleted*. After a 14-day jump they stay,
  because Telegram only lets a bot delete messages for 48 hours.
- **Fake transcription.** `ASR_ENGINE=fake` gives every new voice note the same sample sentence.
  The sample reports have their own transcripts.
- **Photos.** The page converts any image to JPEG before sending, as Telegram apps do, so it
  arrives as a photo. PDFs arrive as documents.
- **Safety.** Demo mode refuses to start unless the database lives in `.demo-pgdata/`. The `/demo`
  routes don't exist when `DEMO_MODE` is off. Secrets are generated for each run and never written
  to disk.
- **Removing the demo.** See `docs/MERGE.md` §6 item 13 for what to delete at merge.
- **Browser tests** need a fresh demo (restart the script first): one test closes a sample report and
  another imports the two spare users. From `web/`: `npx playwright test`.
