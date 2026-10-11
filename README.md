# Flight tracker

Checks Google Flights every hour for one-way fares from Bangalore on 3, 4 and 5 November 2026 and sends a Telegram message when a fare is at or under the limit.

| Destination | Limit |
|---|---|
| Ranchi (IXR) | ₹8,000 |
| Bhubaneswar (BBI), Kolkata (CCU), Jharsuguda (JRG), Varanasi (VNS) | ₹7,000 |

Routes, dates and limits are at the top of `tracker.py`.

## One-time setup

1. In Telegram, open **@BotFather**, send `/newbot`, and follow the prompts. Copy the token it gives you.
2. Open your new bot's chat and press **Start**. A bot cannot message you until you do this.
3. In this repo go to **Settings > Secrets and variables > Actions > New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN`: the token from BotFather
   - `TELEGRAM_CHAT_ID`: your numeric Telegram ID
4. Go to **Actions > Track flight prices > Run workflow**, tick **Only send a test Telegram message**, and run it. You should get a test message.

After that it runs by itself every hour.

## How alerts behave

- You are told about a flight once, and again only if its price drops further.
- If a fare goes back above the limit and later falls again, you are told again.
- If prices cannot be read three runs in a row, you get a warning message.
- Prices are Google Flights fares for one adult in economy. The final price on the airline site can differ slightly.

## Run on your own PC

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python tracker.py --dry-run
```

For real alerts from your PC, put the token in `.env` and drop `--dry-run`.
