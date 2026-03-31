# The Atlee 2BR Availability Monitor — Setup Guide

This automation checks every 5 minutes during business hours (7am-9pm CT) for new 2-bedroom apartments at The Atlee and texts you when one appears. It calls the Knock CRM API directly — no browser or scraping needed.

---

## What You'll Need

- A GitHub account (free)
- A Gmail account for sending the text messages
- The phone numbers you want to receive alerts (all Verizon)
- About 15 minutes

---

## Step 1: Create a Gmail App Password

Gmail won't let you send via SMTP with your regular password. You need an "App Password":

1. Go to https://myaccount.google.com/security
2. Make sure **2-Step Verification** is turned ON (required for App Passwords)
3. Go to https://myaccount.google.com/apppasswords
4. Select **Mail** as the app, give it a name like "Atlee Monitor"
5. Click **Generate** — you'll get a 16-character password like `abcd efgh ijkl mnop`
6. Copy it (remove spaces) — you'll need it in Step 3

---

## Step 2: Create the GitHub Repository

1. Go to https://github.com/new
2. Name it something like `atlee-monitor`
3. Set it to **Public** (free unlimited Actions minutes) or **Private** (2,000 free minutes/month — this uses ~100/month)
4. Click **Create repository**
5. Upload ALL the files from this folder, preserving the directory structure:
   ```
   atlee-monitor/
   ├── .github/
   │   └── workflows/
   │       └── monitor.yml
   ├── monitor.py
   ├── requirements.txt
   ├── last_state.json
   └── SETUP.md
   ```

   **Easiest way:** Open a terminal, `cd` into this folder, and run:
   ```bash
   git init
   git remote add origin https://github.com/YOUR_USERNAME/atlee-monitor.git
   git add -A
   git commit -m "Initial commit"
   git branch -M main
   git push -u origin main
   ```

---

## Step 3: Add Your Secrets

Secrets keep your credentials safe — they're encrypted and never visible in logs.

1. In your GitHub repo, go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret** for each:

| Secret Name    | Value                                                    |
|----------------|----------------------------------------------------------|
| `SMTP_EMAIL`   | Your Gmail address (e.g., `bgarrett.work@gmail.com`)     |
| `SMTP_PASSWORD`| The 16-char App Password from Step 1 (no spaces)         |
| `PHONE_NUMBERS`| Comma-separated phone numbers, digits only (e.g., `2105551234,2105555678,2105559012`) |

---

## Step 4: Give Actions Write Permission

The workflow needs to commit state changes back to the repo:

1. Go to **Settings → Actions → General**
2. Scroll to **Workflow permissions**
3. Select **Read and write permissions**
4. Click **Save**

---

## Step 5: Test Locally (Optional)

You can run the monitor locally first to verify it works:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests

# Dry run (no SMS — secrets aren't set)
python monitor.py
```

You should see output listing any currently available 2BR units.

---

## Step 6: Enable the Workflow

1. Go to your repo's **Actions** tab
2. You should see the "Check Atlee 2BR Availability" workflow
3. If prompted, click **I understand my workflows, go ahead and enable them**
4. Click the workflow name → **Run workflow** → **Run workflow** to trigger a manual test

Check the run output to verify it:
- Calls the API successfully
- Finds (or correctly reports zero) available 2BR units
- On the first run, it saves baseline state without sending SMS

---

## How It Works

- **Every 5 minutes**, GitHub Actions triggers the workflow
- The Python script checks if it's between **7am-9pm Central Time** — if not, it exits immediately (~0 Actions minutes)
- During business hours, it calls the Knock CRM API (`doorway-api.knockrentals.com`) which is the same API that powers the theatlee.com availability page
- It filters for 2-bedroom units where `available == true`
- It compares found units against `last_state.json` (the previously known availability)
- **Only when NEW units appear** does it send an SMS — no spam for unchanged availability
- State is committed back to the repo so it persists between runs

---

## Costs

- **GitHub Actions**: Free for public repos. Private repos: ~100 min/month of the 2,000 free minutes
- **SMS**: Free via Verizon's email-to-SMS gateway (`number@vtext.com`)
- **Gmail SMTP**: Free

**Total: $0/month**

---

## Troubleshooting

**No SMS received on test:**
- Check the Actions run log for errors
- Verify your App Password is correct (no spaces)
- Make sure phone numbers are digits only, no dashes or parentheses
- Try sending a test email manually to `yourphone@vtext.com` from Gmail

**Actions workflow not running:**
- Check that Actions is enabled in your repo settings
- The cron schedule can take a few minutes to start after first push
- GitHub may delay scheduled runs by up to a few minutes during high load

**Monitor finds 0 units but website shows some:**
- The Knock API may be temporarily down — check again in a few minutes
- Run `python monitor.py` locally to see the raw output

**Want to stop the monitor:**
- Go to Actions → workflow → click the `...` menu → **Disable workflow**

---

## Customization

**Change business hours:** Edit `BUSINESS_START` and `BUSINESS_END` in `monitor.py`

**Change check frequency:** Edit the cron expression in `.github/workflows/monitor.yml`
  - `*/5 * * * *` = every 5 minutes
  - `*/10 * * * *` = every 10 minutes
  - `*/2 * * * *` = every 2 minutes

**Add non-Verizon numbers:** Change `CARRIER_GATEWAY` in `monitor.py`:
  - T-Mobile: `@tmomail.net`
  - AT&T: `@txt.att.net`
  - Sprint: `@messaging.sprintpcs.com`

  For mixed carriers, you'd need to modify the script to map each number to its carrier gateway.
