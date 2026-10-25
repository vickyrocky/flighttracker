# Flight tracker

Checks Google Flights every hour for one-way fares on 3, 4 and 5 November 2026 and reports to Telegram.

| Route | Limit |
|---|---|
| Bangalore to Ranchi (IXR) | ₹8,000 |
| Chennai to Ranchi (IXR) | ₹8,000 |
| Bangalore to Bhubaneswar (BBI), Kolkata (CCU), Jharsuguda (JRG), Varanasi (VNS) | ₹7,000 |

Routes, dates and limits are at the top of `tracker.py`.

## One-time setup

1. In Telegram, open **@BotFather**, send `/newbot`, and follow the prompts. Copy the token it gives you.
2. Open your new bot's chat and press **Start**. A bot cannot message you until you do this.
3. In this repo go to **Settings > Secrets and variables > Actions > New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN`: the token from BotFather
   - `TELEGRAM_CHAT_ID`: your numeric Telegram ID
4. Go to **Actions > Track flight prices > Run workflow**, tick **Only send a test Telegram message**, and run it. You should get a test message.

After that an hourly job on cron-job.org starts the workflow through the GitHub API. GitHub's own schedule never fired for this repo, so it is not used.

## How alerts behave

- Every hour you get a summary with the lowest fare on each route. Set `SEND_HOURLY_SUMMARY = False` in `tracker.py` to turn it off.
- Nothing is checked or sent between 12 AM and 6 AM IST. A deal that appears overnight is alerted at the first morning run if it is still there.
- A separate deal alert is sent when a fare is at or under its limit. You are told about a flight once, and again only if its price drops further.
- If a fare goes back above the limit and later falls again, you are told again.
- Google's Cheapest tab sometimes shows a lower "Cheapest from" fare than any listed flight, because that fare only loads when the tab is opened. The tracker reads that figure too and alerts on it, but it cannot see that flight's details. Such alerts say to open the Cheapest tab.
- Searches that fail are marked in the hourly summary.
- Prices are Google Flights fares for one adult in economy. The final price on the airline site can differ slightly.

## Run on your own PC

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python tracker.py --dry-run
```

For real alerts from your PC, put the token in `.env` and drop `--dry-run`.
