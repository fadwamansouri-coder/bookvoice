# BookVoice — Deployment Guide

## Step 1: Set up Railway

1. Go to [railway.com](https://railway.app) and sign up (free tier: $5/month credit — enough for a light-traffic app)
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Connect your GitHub account and select your BookVoice repository
4. Railway detects the Dockerfile automatically — no config needed
5. Once deployed, go to **Settings → Networking → Generate Domain** to get your free `*.up.railway.app` URL

### Push BookVoice to GitHub first

If you haven't already:

```bash
cd ~/Desktop/bookvoice
git init
git add .
git commit -m "BookVoice initial"
```

Then create a repo on github.com and push:

```bash
git remote add origin https://github.com/YOUR_USERNAME/bookvoice.git
git branch -M main
git push -u origin main
```

## Step 2: Set up Mailchimp

1. Go to [mailchimp.com](https://mailchimp.com) and create a free account
2. Create an **Audience** (your email list)
3. Go to **Profile → Extras → API Keys** and create a new key
4. Note your **Audience/List ID**: go to **Audience → Settings → Audience name and defaults** — the List ID is shown there

## Step 3: Connect Mailchimp to Railway

In your Railway project dashboard:

1. Go to **Variables** tab
2. Add these environment variables:

| Variable | Value |
|---|---|
| `MAILCHIMP_API_KEY` | `your-api-key-us21` (the key you created) |
| `MAILCHIMP_LIST_ID` | `abc123def4` (your audience/list ID) |

Railway will automatically redeploy with the new variables.

## Step 4: Test

1. Visit your Railway URL (e.g. `bookvoice-production.up.railway.app`)
2. You should see the landing page
3. Sign up with a test email
4. Check your Mailchimp audience — the subscriber should appear
5. After signing up, click "Open BookVoice" to access the reader

## How it works

- Visitors land on the landing page at `/`
- They enter their name and email → stored locally + sent to Mailchimp
- After signup, they're redirected to `/app` (the full BookVoice reader)
- You can email your subscribers through Mailchimp's campaign tools

## Optional: Custom domain

If you want `bookvoice.com` or similar:
1. Buy a domain (Namecheap, Google Domains, etc.)
2. In Railway → Settings → Networking → Custom Domain
3. Add a CNAME record pointing to your Railway URL
4. Railway handles SSL automatically

## Cost

- **Railway free tier**: $5/month credit (covers ~500 hours of a small container)
- **Mailchimp free tier**: Up to 500 subscribers, 1,000 emails/month
- **Edge TTS**: Free (Microsoft's API, no key needed)
- **Total**: $0 until you exceed free tiers
