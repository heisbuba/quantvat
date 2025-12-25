# 📈 Crypto Volume Analysis Toolkit (CryptoVAT)

**A powerful web-based suite designed for crypto analysts and traders.** It tracks high-volume tokens across the last 24 hours, performing cross-market analysis using integrated Spot and Futures data. Generate professional, data-driven PDF reports directly in your browser with zero setup required.

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

# **Setup Guide**

- Launch the [Live App Here](https://huggingface.co/spaces/heisbuba/cryptovat).
- Create an account and log in.
- Obtan and enter your API keys in the Setup Wizard.

1. Visit [CoinAlyze.net](https://coinalyze.net) and sign up.

2. Navigate to **Custom Metrics** and tap on **Create Custom Metrics**.

3. Enter **VTMR** in the Name and Short Name fields, paste the **VTMR code** below in the Expression field, then **Save & Close**.

```code
((vol_1d[0] / mc_1d[0]) * 10) / 10 * (vol_1d[0] / mc_1d[0] >= 0.5)
```

- Go to **Columns**, deselect all, and select **Market Capitalization**, **Volume 24H**, **Open Interest Change % 24H**, **Predicted Funding Rate Average, OI Weighted**, and **VTMR**, then click **Apply**.

- Sort the whole data by VTMR, copy the URL and paste it in the VTMR box in App's setup wizard login to dashboard.

- Tap on **Spot Scan** to generate spit data > Tap kn Get Futures > Visit your alread

- Go to Chrome menu → Share → Print, and save it as it is without changing the file name in the Download folder.

### Why Do This?

Because before you run the toolkit, you need fresh futures data from CoinAlyze. 

So basically, you only need to launch Chrome  → type VTMR and open it  → print the page into the Download folder. 

**For the web-based version**, you'll upload it and save at **Get Futures** page before running advanced analysis.

If you are using **Standalone local environment v3.0 or v4.0**, then after you run the toolkit, it will take care of cleaning your download folder and leaving only the most valuable reports there. 


# 1. Setup Guide

The easiest way to use the toolkit. No installation needed.


- Start scanning immediately using the dashboard.



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
