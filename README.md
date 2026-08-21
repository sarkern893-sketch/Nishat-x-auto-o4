# Telegram Auto-Post Bot

This bot runs two parts together:

- `main.py` — private Telegram admin panel
- `userbot.py` — watches source channels and publishes cleaned text

The Docker image starts both processes through `run_all.py`.

The admin panel supports multiple source and destination channels. Each matching
source post is sent to every configured destination, and the same source message
is not published twice.

## Updated workflow

1. A source post is cleaned without rewriting its original structure.
2. Configured usernames, phone numbers, emails, and Telegram links are replaced.
3. Spacing is normalized and only minimal, relevant emoji decoration is added.
4. The edited post is published to the configured destination channel.
5. Option B forwarding forwards that edited channel message to configured groups.
6. The same forwarded message can repeat a configurable number of times with a
   configurable interval.
7. Optional Groq AI can format text posts without changing their meaning, with
   configurable style, length, emoji, and custom prompt.
8. Users can opt in with `/optin` (or opt out with `/optout`) before receiving
   an admin campaign message. Campaigns keep per-user delivery status.
9. Group AI can be enabled per group with reply modes: `always`, `question`,
   `mention`, `reply`, or `ask`.
10. Channel → Group forwarding supports multiple groups, per-group count and
    delay, pause/resume, schedule window, optional AI editing, duplicate
    protection, status, and JSONL history.

Forwarding uses Telegram's native forward-message operation; it does not create
a new copy of the message. The forwarding bot/account needs permission to send
messages in each target group. It does not need to be an administrator unless
the group's own permission settings require that.

## Railway setup

1. Deploy this folder using the included `Dockerfile`.
2. Add all variables from `.env.example` in Railway Variables.
3. Add a Railway Volume mounted at `/data`.
4. Use an always-on worker/service, not a scheduled job.
5. Add the bot as an administrator in the destination channel with permission to post.
6. Make sure the Telegram user account can read every source channel.
7. Open the bot privately and use `📡 চ্যানেল সেটিংস` to add sources and destinations.
8. Use `⚙️ সেটিংস` to configure replacement contact details.
9. Use `⚙️ সেটিংস` → `🔁 Forward সেটিংস` to add groups, enable forwarding,
   and set repeat count/interval.
10. Use `🤖 AI সেটিংস` to enable Groq editing and configure its prompt.
11. Users must send `/optin` to the bot before any campaign message can be
    delivered. `/optout` stops future campaign messages.
12. Add the bot to a group, then configure that group's ID and mode from
    `🤖 AI সেটিংস` → `👥 Group AI সেটিংস`.
13. Admin can manage individual campaign users with `/useron`, `/useroff`,
    `/userremove`, `/userstatus`, and `/retry`, followed by the user ID or
    username.
14. Use `📢 Channel → Group` to add groups and configure the selected group.
    Schedule format is `on 09:00 23:00`; delay is in seconds and count is
    between 1 and 20.

Source and destination channels are configured from the admin panel, so
`SOURCE_CHANNELS` and `DEST_CHANNEL_ID` are not required in `.env`.

## Telegram session

The first Telethon login may require an OTP and two-step verification password.
Railway cannot reliably collect an interactive login code. Create and authorize
the Telethon session before running the production service, then keep the
resulting session file in the mounted `/data` directory.

Never commit `.env`, Telegram bot tokens, API hashes, session files, or
`GROQ_API_KEY`. The key is read only from the environment and never saved in
settings. The updated distribution intentionally excludes the local Telegram
session file;
authorize your own session in the mounted data directory before production use.

## নতুন Feature সমূহ (Update)

- **👑 Multi Admin System** — মূল মেনু থেকে `👑 Admin Management` (শুধু Super
  Owner / `ADMIN_IDS`-এ থাকা আইডি দেখতে পাবে)। এখান থেকে নতুন Admin যোগ করা
  যায়, প্রতিটির জন্য আলাদা Permission (Channel Manage, Schedule Manage,
  AI Settings, All Data Manage, Bot Settings, User Account Control) ON/OFF
  করা যায়, এবং Admin সরানো যায়। `ADMIN_IDS` (env) সবসময় Super Owner থাকে,
  বট থেকে কখনো সরানো যায় না।
- **🎨 Custom AI Post Format Style** — `🤖 AI সেটিংস → 🎨 Post Format Style`।
  চালু থাকলে AI post edit করার সময় admin-নির্ধারিত Border/Header/Footer/
  Contact line/Bullet/Emoji heading অনুযায়ী পোস্ট সাজায়; বন্ধ থাকলে আগের
  মতো plain edit হয়।
- **📚 Private Knowledge fix** — এখন Post AI দিয়ে edit করার সময়ও owner-এর
  Private Knowledge (price/contact/rules ইত্যাদি) ব্যবহার হয়, আগে শুধু
  Live Chat-এই ব্যবহার হতো।
- **🧭 Multi-Channel Watch + Smart Scheduling** — মূল মেনু থেকে
  `🧭 Multi-Channel Watch`। এখানে একাধিক Channel যোগ করলে বট সেগুলোর নতুন
  Post সংগ্রহ করে, Duplicate/একই ধরনের Content বাদ দিয়ে, নির্ধারিত সময়ে
  (ডিফল্ট 09:00, 14:00, 19:00) সেরা Post Destination-এ পাবলিশ করে। এই
  Feature বন্ধ থাকলে আগের সাধারণ Auto Forward/Auto Post System স্বাভাবিকভাবে
  কাজ করে — কোনো প্রভাব পড়ে না।