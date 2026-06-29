# Protiviti AI Scribe — Setup Guide

Two things are covered here:
1. [Azure AD App Registration](#1-azure-ad-app-registration) — required once per Microsoft account/tenant you want the bot to use for Teams chat posting + transcript retrieval
2. [Installing on a new machine](#2-installing-on-a-new-machine) — required once per computer

---

## 1. Azure AD App Registration

This gives the bot permission to post the MoM into Teams chat and fetch the official Teams transcript via Microsoft Graph API — no browser automation involved for either.

### Step 1 — Sign in
Go to **[portal.azure.com](https://portal.azure.com)** and sign in with the Microsoft account/tenant you want this app tied to.

### Step 2 — Create the app registration
1. Search **"Microsoft Entra ID"** in the top search bar
2. Left sidebar → **App registrations** → **+ New registration**
3. Fill in:
   - **Name:** `Protiviti AI Scribe`
   - **Supported account types:** Accounts in this organizational directory only (Single tenant)
   - **Redirect URI:** leave blank for now
4. Click **Register**

### Step 3 — Configure authentication
1. Left sidebar → **Authentication**
2. Click **Add a platform** → select **"Mobile and desktop applications"**
3. Check the box for `http://localhost`
4. Click **Configure** to save
5. Scroll down to **Advanced settings** → toggle **"Allow public client flows"** to **Yes**
6. Click **Save** at the top

### Step 4 — Add API permissions
1. Left sidebar → **API permissions**
2. Click **Add a permission** → **Microsoft Graph** → **Delegated permissions**
3. Search and check **exactly these 4**:
   - `Chat.ReadWrite`
   - `ChatMessage.Send`
   - `OnlineMeetings.Read`
   - `OnlineMeetingTranscript.Read.All`
4. Click **Add permissions**
5. Back on the API permissions page, click **Grant admin consent for [your org]** → confirm

> Don't add extra permissions like `Chat.ReadWrite.All` or `Chat.ManageDeletion.All` — those are higher-privilege, need separate admin approval, and aren't used by this app.

### Step 5 — Get your credentials
1. Left sidebar → **Overview**
2. Copy:
   - **Application (client) ID**
   - **Directory (tenant) ID**
3. Paste both into `.env`:
   ```
   GRAPH_CLIENT_ID=<application-client-id>
   GRAPH_TENANT_ID=<directory-tenant-id>
   ```

### Important notes
- These permissions are **delegated**, meaning the bot can only post/read as the signed-in user (you), not as a separate bot identity — this is a Microsoft restriction to prevent spam, not a limitation of this app.
- The official Teams transcript (`OnlineMeetingTranscript.Read.All`) is only available if someone clicks **"Start transcription"** during the meeting. If transcription isn't started, the bot automatically falls back to its own live audio transcription.
- This only works for meetings **you organize** — Graph API can't look up meetings by URL unless you're the organizer.
- **Don't reuse a personal Azure account's app registration for an employer's Teams data** — register the app in the same tenant as the Teams account that will use it.

---

## 2. Installing on a new machine

### Prerequisites
- Windows 10/11
- Python 3.10 or newer ([python.org/downloads](https://www.python.org/downloads/)) — check **"Add Python to PATH"** during install
- A Gemini API key (free) — [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
- An Azure AD App Registration (see [Part 1](#1-azure-ad-app-registration)) if you want Teams chat posting

### Step 1 — Clone the repo
```powershell
git clone https://github.com/shubu2908/Protiviti-AI-Scribe.git
cd Protiviti-AI-Scribe
```

### Step 2 — Run the installer
```powershell
install.bat
```
This installs all Python packages and the Chromium browser for Playwright.

### Step 3 — Configure `.env`
This file is never committed to git (it holds secrets), so you create it fresh on every machine:
```powershell
copy .env.example .env
```
Open `.env` and fill in:

| Variable | Required? | Where to get it |
|---|---|---|
| `GEMINI_API_KEY` | Yes | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) |
| `BOT_DISPLAY_NAME` | No (default provided) | Whatever you want the bot to show as in Teams |
| `GRAPH_CLIENT_ID` / `GRAPH_TENANT_ID` | Only for Teams chat posting | From [Part 1](#1-azure-ad-app-registration) above |
| `SMTP_*` / `EMAIL_TO` / `ORGANIZER_EMAIL` | Only for email delivery | See comments in `.env.example` |

### Step 4 — First run (one-time sign-in)
The first time the bot tries to post to Teams chat or fetch a transcript, a browser window opens for you to sign in with the Graph account and consent to the permissions. After that, it signs in silently using a cached token (`graph_token_cache.bin` — also gitignored, stays local to that machine).

### Step 5 — Launch
Double-click **`launch.bat`**, or run from a terminal:
```powershell
python tray_app.py
```
A microphone icon appears in the system tray. Right-click → **Start Scribe** → paste the Teams meeting URL.

Or run the bot directly without the tray app:
```powershell
python main.py --url "https://teams.microsoft.com/..." --title "My Meeting"
```

### Troubleshooting
See the **Troubleshooting** section in [README.md](README.md) for audio capture issues, lobby/rejoin detection problems, and rate limit handling.
