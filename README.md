
---
title: Crypto Volume Analsis Toolkit v4.0
emoji: 📈
colorFrom: gray
colorTo: gray
sdk: docker
pinned: true
license: mit
short_description: Perform crypto institutional-grade cross-market analysis
thumbnail: >-
  https://cdn-uploads.huggingface.co/production/uploads/6947519e94e1623904a5b3a4/SdHOG8_LhjpMm8-BBiUL7.jpeg
---


# **Crypto Volume Analysis Toolkit**

Is a powerful web-based suite designed for crypto analysts and traders. It tracks high-volume tokens across the last 24 hours, performing cross-market analysis using integrated Spot and Futures data. Generate professional, data-driven PDF reports directly in your browser with zero setup required.

# **Demo**

![One Page Screenshot - Excludes Remaining Spot Tokens](https://github.com/heisbuba/crypto-volume-analysis-toolkit/demo/demo.jpg?raw=true)

# **Features**

- Easy to use
- Modern Web-UI 
- Fast, lightweight, and reliable
- Executes the task in less than a minute
- Auto-save and retrieve API keys and VTMR details
- Works on any device
- No complex setup
- Automatic Spot Volume Tracking
- Advanced Futures + Spot Analysis
- Automatic clean HTML report generation
- Automatic PDF export
- Cleans up after finishing
- Multi-source verification reduces errors
- Useful for daily analysis routines
- Explainer for Open Interest Signal Score (OISS)

# **Universal Setup (Required for All Versions)**

**Everyone must do this step first.** To use the tool efficiently, you need specific futures data from CoinAlyze.

1. Go to https://CoinAlyze.net and sign up.

2. Navigate to **Custom Metrics** and tap on **Create Custom Metrics**.

3. Enter **VTMR** in the Name and Short Name fields, then paste the **VTMR code** below in the Expression field and save & close.

```code
((vol_1d[0] / mc_1d[0]) * 10) / 10 * (vol_1d[0] / mc_1d[0] >= 0.5)
```

- Go to **Columns**, deselect all, and select **Market Capitalization**, **Volume 24H**, **Open Interest Change % 24H**, **Predicted Funding Rate Average, OI Weighted**, and **VTMR**, then click **Apply**.

- Bookmark the page and save it as VTMR.

- Go to Chrome menu → Share → Print, and save it as it is without changing the file name in the Download folder.

### Why Do This?

Because before you run the toolkit, you need fresh futures data from CoinAlyze. 

So basically, you only need to launch Chrome  → type VTMR and open it  → print the page into the Download folder. 

**For the web-based version**, you'll upload it and save at **Get Futures** page before running advanced analysis.

If you are using **Standalone local environment v3.0 or v4.0**, then after you run the toolkit, it will take care of cleaning your download folder and leaving only the most valuable reports there. 


# 1. Web-Based Setup (Cloud Edition)

The easiest way to use the toolkit. No installation needed.

- Go to the [Live App Here](https://huggingface.co/spaces/heisbuba/cryptovat).
- Create an account and log in.
- Enter your API keys once in the Setup Wizard.
- Start scanning immediately using the dashboard.

# 2. Local Installation & Setup

If you prefer to run it locally on your own machine.

## Installation

1. Install these 5 libraries in your python or Pydroid3 environment:
```code
pip install requests pandas pypdf lxml flask
```
3. API Keys Setup
Set these in your environment variables (for v2.0) or in the Setup Wizard (for v3.0+):
  - HTML2PDF_API_KEY
  - CMC_API_KEY
  - LIVECOINWATCH_API_KEY
  - COINRANKINGS_API_KEY

# Disclaimer

This toolkit is for research and educational purposes only. It does not, in any way, provide financial advice, trading signals, or investment recommendations.

# Contribute

MIT licensed — use, modify, or build freely.
Contribute via issues, PRs, or feature suggestions.  All contributions stay MIT.

# Changelog

- v1.0: full version created and uploaded on Nov. 30, 2025.
- v2.0: Integrated OISS and explainer added on Dec. 02, 2025.
- v3.0: Local Web-UI added.
- v4.0: Cloud Edition (Hugging Face) with Firebase integration added.
