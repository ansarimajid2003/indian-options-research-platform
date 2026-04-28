# Kotak Neo API Setup Guide

## Step 1 — Get Your API Credentials (in the App)

1. Open the **Kotak Neo app** (mobile)
2. Tap **More → Trade API**
3. Tap **Create New Application**
4. Copy your **Consumer Key** (= API Access Token)
5. From the same page, tap **Register for TOTP** and scan the QR code
   with Google Authenticator or Microsoft Authenticator
6. Your **Consumer Secret** is shown once — save it securely

You will need:
- `consumer_key`      — from Step 3
- `consumer_secret`   — from Step 6
- `mobile_number`     — your Kotak Neo registered mobile (+91XXXXXXXXXX)
- `password`          — your Kotak Neo login password
- `mpin`              — your 6-digit MPIN

## Step 2 — Static IP Whitelisting (SEBI Mandate, mandatory since April 1 2026)

SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 requires Kotak Neo
to only accept API connections from pre-registered static IPs.

**Option A (running from home/office):**
1. Find your current public IP: visit whatismyip.com
2. Note: home ISPs usually assign dynamic IPs — check with your ISP if it is static
3. Email tradeapi@kotak.com with your Client ID and the IP address to whitelist

**Option B (running from a server/VPS):**
- Use a static IP proxy service (e.g., QuotaGuard Shield)
- Route your script's traffic through the proxy
- Register that static IP with Kotak Neo

**Note:** If you are only running scripts from your home network during market hours
and your ISP does not change your IP frequently, you can check your current IP at the
start of each session and re-register if it changes. Contact tradeapi@kotak.com to
update the whitelisted IP.

## Step 3 — Install the SDK

```
pip install "git+https://github.com/Kotak-Neo/kotak-neo-api.git"
```

## What the Kotak Neo API CAN and CANNOT do for data

| Feature                    | Available? |
|---------------------------|-----------|
| Live real-time quotes      | YES       |
| WebSocket live tick feed   | YES       |
| Live 1-min OHLC            | YES (via quotes) |
| Order placement            | YES       |
| Historical OHLCV data      | NO        |

Official confirmation: https://www.kotakneo.com/support/how-do-i-get-historical-data/
> "Historical data is unavailable at the moment."

## Historical Data Gap (Mar 2024 - Apr 2026)

To fill the 2-year gap in the dataset, use one of:
- Dhan API    : https://dhanhq.co/docs/v2/historical-data/
- Fyers API   : https://myapi.fyers.in/docs/?ver=3#tag/Historical-Data (1 year 1-min free)
- Upstox API  : https://upstox.com/developer/api-documentation/historical-candle-data
- Kaggle      : kaggle.com/datasets/debashis74017/nifty-50-minute-data (free account)
