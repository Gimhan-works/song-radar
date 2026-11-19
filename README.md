# Trending Song Radar

අලුතින් release වුණ (පැය 5ට අඩු) international songs ස්වයංක්‍රීයව අල්ලලා, trending වීමේ score එකක් (0-100) දෙන tool එක.

## Setup (විනාඩි 5යි)

1. **YouTube API key** ගන්න (නොමිලේ):
   - https://console.cloud.google.com ගිහින් project එකක් හදන්න
   - "APIs & Services" > "Library" > **YouTube Data API v3** > Enable
   - "Credentials" > "Create credentials" > **API key**
2. Python 3.9+ තියෙන්න ඕනේ. වෙන packages ඕනේ නැහැ.
3. Run කරන්න:

```bash
# Mac/Linux
export YOUTUBE_API_KEY="ඔයාගේ key එක"
python tracker.py --serve

# Windows (PowerShell)
$env:YOUTUBE_API_KEY="ඔයාගේ key එක"
python tracker.py --serve
```

4. Browser එකේ http://localhost:8000/index.html open කරන්න. පැයකට සැරයක් ස්වයංක්‍රීයව update වෙනවා.

## Settings (environment variables)

| Variable | Default | කුමක්ද |
|---|---|---|
| `WINDOW_HOURS` | 5 | කොච්චර අලුත් songs ද අල්ලගන්නේ |
| `REGIONS` | US | `US,GB,AU` වගේ comma separated |

| `KARAOKE_TOP` | 15 | එක run එකකදී karaoke check කරන්නේ score එකෙන් ඉහළම songs කීයද (0 = off) |
| `KARAOKE_RECHECK_MIN` | 120 | එකම song එක ආයෙ check කරන්නේ විනාඩි කීයකට පස්සේද |
| `KARAOKE_DAILY_SEARCHES` | 60 | දවසකට karaoke searches උපරිමය (quota ආරක්ෂා කරන්න) |

`--interval 20` දැම්මම විනාඩි 20කට සැරයක් run වෙනවා.

## Karaoke signal 🎤

Voice එක ඉවත් කරලා karaoke / instrumental විතරක් දාපු videos ගණන song එකකට කීයක් තියෙනවද කියලා tracker එක බලනවා.
මිනිස්සු sing කරන්න ආස songs වලට තමයි karaoke versions හැදෙන්නේ, ඒ නිසා ඒක හොඳ trending signal එකක්.

- Song එකේ release වුණාට පස්සේ upload වුණ videos වලින්, title එකේ `karaoke`, `instrumental`, `no vocals`, `backing track` වගේ වචන තියෙන සහ song නම ගැලපෙන ඒවා විතරක් ගණන් ගන්නවා.
- Dashboard එකේ `🎤 N karaoke` chip එකක් පෙනෙනවා (mouse එක උඩින් තිබ්බම ඒ videos වල නම් පෙනෙනවා).
- Score එකට උපරිම **+10** bonus එකක් (karaoke videos 5ක් හෝ ඊට වැඩි නම්) එකතු වෙනවා.
- Check කරන්නේ score එකෙන් ඉහළම `KARAOKE_TOP` songs විතරයි. එක check එකකට quota 100ක් යනවා.
- අලුත් song එකක් release වෙලා පැය කිහිපයක් ඇතුළත karaoke versions තවම නැතිවෙන්න පුළුවන්. ඒ නිසා ඉක්මනින් 0 පෙන්නුවොත් ඒක නරක සලකුණක් නෙවෙයි. පැය 2කට පස්සේ ආයෙ check වෙනවා.

## Opportunity view 🎯

Dashboard එකේ උඩ තියෙන **Opportunity** button එක ඔබන්න. Trend score එක ඉහළ වගේම karaoke versions අඩු songs උඩට එනවා.
- `🎤 0 karaoke · gap!` කියන්නේ ඒ song එකට තවම karaoke version එකක් හම්බවෙලා නැහැ කියන එකයි.
- `🎤 not checked yet` කියන්නේ ඒ song එක `KARAOKE_TOP` ඇතුළත නැති නිසා (හෝ දවසේ search සීමාව ඉවර නිසා) තවම check කරලා නැහැ කියන එකයි.
- Opportunity = karaoke bonus නැති trend score එක, karaoke videos ගාන අනුව අඩු කරලා (5ක් හෝ ඊට වැඩි නම් 70%ක් අඩු වෙනවා).

## Quota ගැන

සෑම search එකක්ම quota 100ක් පාවිච්චි කරනවා, දවසකට නොමිලේ 10,000යි.
- 1 region, පැයකට සැරයක් = 2,400/day
- Karaoke searches (default උපරිමය 60/day) = 6,000/day
- දෙකම එකතුව ≈ 8,500/day ✅ (සීමාව ඇතුළත)
- 2 regions දැම්මොත් quota ඉක්මවනවා. එහෙම නම් `KARAOKE_DAILY_SEARCHES` අඩු කරන්න.
- Karaoke ගාන එක song එකකට පැය 2කට වරක් විතරයි අලුත් වෙන්නේ, එකම song එකට quota නාස්ති වෙන්නේ නැහැ.

## Score එක හදන්නේ කොහොමද

| Signal | Weight |
|---|---|
| Views per hour (වේගය) | 35% |
| Acceleration (අන්තිම snapshots වල වේගය vs සාමාන්‍ය වේගය) | 20% |
| Reach (views ÷ subscribers, fanbase එකෙන් එළියට යනවද) | 20% |
| Engagement (likes + comments ratio) | 15% |
| Artist channel size | 10% |
| 🎤 Karaoke bonus (karaoke videos 5+ = උපරිම) | +10 points (bonus) |

මේක **ඇස්තමේන්තුවක්**, සත්‍ය probability එකක් නෙවෙයි. Weights tune කරන්න tracker.py එකේ `compute_score` බලන්න.
කාලයත් එක්ක (දින කිහිපයක data එකතු වුණාම) කලින් trend වුණ songs vs නොවුණ ඒවා label කරලා ML model එකකට upgrade කරන්න පුළුවන්.

## 24/7 free hosting (GitHub Actions)

`.github/workflows/track.yml` එක repo එකට දාලා, Settings > Secrets > Actions වල `YOUTUBE_API_KEY` add කරන්න.
පැයකට සැරයක් run වෙලා data.json commit කරනවා. Settings > Pages enable කළොත් dashboard එක online බලන්න පුළුවන්.
