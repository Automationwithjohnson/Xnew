# Setup & Deployment Guide: NewsMadeByAnti

This guide outlines the two ways to run the poster every 5 minutes: **Local PC (Recommended)** or **Railway Cloud Deployment**.

---

## Option 1: Run Locally on your PC (Recommended)

Running the script locally is highly recommended because **X (Twitter) heavily blocks/flags accounts** that connect via datacenter IP addresses (like Railway/AWS). Running locally uses your residential IP address, which is much safer.

### Method A: Python Background Loop
Keep a terminal window open running the loop scheduler:
1. Open PowerShell or Command Prompt.
2. Run:
   ```powershell
   cd C:\Users\PRECISION\Desktop\ANTIGRAVITY\Newsmadebyanti
   python run_loop.py
   ```
3. This will trigger the poster immediately, sleep for 5 minutes, and repeat. You can stop it anytime with `Ctrl+C`.

### Method B: Windows Task Scheduler (Runs silently in the background)
To run it without keeping any terminal windows open:
1. Open the Windows Start Menu, type **Task Scheduler**, and open it.
2. Click **Create Basic Task...** on the right side.
3. Name it `NewsMadeByAnti` and click Next.
4. Select **Daily** and click Next.
5. Set the start time and click Next.
6. Select **Start a Program** and click Next.
7. Fill in the fields:
   - **Program/script:** `python` (or full path to your python executable, e.g. `C:\Python314\python.exe`)
   - **Add arguments:** `twikit_news_poster.py`
   - **Start in:** `C:\Users\PRECISION\Desktop\ANTIGRAVITY\Newsmadebyanti`
8. Click Next and click **Finish**.
9. Double-click the task you just created, go to the **Triggers** tab, click **Edit**, check the box for **Repeat task every:** and set it to **5 minutes** (for a duration of indefinitely).
10. Click OK. It will now run silently in the background every 5 minutes.

---

## Option 2: Deploy to Railway (Cloud 24/7)

If you prefer to run it 24/7 without keeping your PC turned on, you can deploy to Railway. 

> [!WARNING]
> Because Twikit logs in via automated web queries, X's firewall may block or flag your account if it detects logins from Railway datacenter IPs. You may need to use proxies.

### Steps to Deploy:
1. **Push to GitHub**:
   - Create a private repository on GitHub (keep it private to protect your cookies and database).
   - Initialize git in `C:\Users\PRECISION\Desktop\ANTIGRAVITY\Newsmadebyanti` and push the files to your GitHub repository.
2. **Add a Procfile**:
   - Create a file named `Procfile` in the root of the folder containing:
     ```text
     worker: python run_loop.py
     ```
3. **Configure Railway**:
   - Go to [Railway.app](https://railway.app) and create a new project.
   - Select **Deploy from GitHub repo** and connect your private repository.
4. **Set Variables**:
   - Go to the **Variables** tab in Railway and add the environment variables from your `.env` file:
     - `OPENROUTER_API_KEY`
     - `OPENROUTER_MODEL`
     - `X_PREMIUM`
     - `MAX_TWEET_LENGTH`
     - `WINDOW_MINUTES`
    - Since cookies and database need to be saved, you can add a **Railway Volume** mount to persist `Xaccountdata.json` and `posted_links.db`, or keep them inside the Git repo.

---

## Option 3: Run on Android (Termux) - Very Clean Residential IP

Running via **Termux** on an Android phone is an excellent option. It uses your mobile network/residential IP (safest for X) and allows you to run it 24/7 with minimal power draw.

### Setup Steps:
1. **Install Termux**:
   - Download and install Termux from **F-Droid** (do not use the Play Store version as it is outdated).
2. **Update Packages & Install dependencies**:
   Open Termux and run:
   ```bash
   pkg update && pkg upgrade -y
   pkg install python git sqlite -y
   ```
3. **Get your Project Files**:
   - Push your `Newsmadebyanti` folder to a private GitHub repo, then clone it in Termux:
     ```bash
     git clone https://github.com/your-username/your-private-repo.git
     cd your-private-repo
     ```
   - *Alternative:* Transfer the folder to your phone storage and copy it to the Termux home folder using a file manager (like MiXplorer or Solid Explorer).
4. **Install Python Packages**:
   ```bash
   pip install requests beautifulsoup4 feedparser twikit python-dotenv
   ```
5. **Run the runner loop 24/7**:
   - Prevent Android from sleeping/suspending the Termux background process:
     ```bash
     termux-wake-lock
     ```
   - Start the loop runner:
     ```bash
     python run_loop.py
     ```
   - To stop the runner loop, press `Ctrl+C`. To release the CPU lock when not running, run `termux-wake-unlock`.
