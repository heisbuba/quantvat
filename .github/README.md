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

- Visit [CoinAlyze.net](https://coinalyze.net) and sign up.

- Navigate to **Custom Metrics** and tap on **Create Custom Metrics**.

- Enter **VTMR** in the Name and Short Name fields, paste the **VTMR code** below in the Expression field, then **Save & Close**.

```code
((vol_1d[0] / mc_1d[0]) * 10) / 10 * (vol_1d[0] / mc_1d[0] >= 0.5)
```

- Go to **Columns**, deselect all, and select **Market Capitalization**, **Volume 24H**, **Open Interest Change % 24H**, **Predicted Funding Rate Average, OI Weighted**, and **VTMR**, then click **Apply**.

- Sort the whole data by **VTMR**, copy the URL and paste it in the VTMR box in App's setup wizard and proceed to dashboard.

- Tap on **Spot Scan** to generate spit data >  **Get Futures** > **Open CoinAlyze** > Chrome menu >>  → Share → Print, and save it as it is without changing the file name or by naming it **Futures.pdf**.

- Then use the upload button in **Get Futures** to upload it back to complete your cross-market analysis.

# Disclaimer

This toolkit is for research and educational purposes only. It does not, in any way, provide financial advice, trading signals, or investment recommendations.

# Contribute

MIT licensed — use, modify, or build freely. Contribute via issues, PRs, or feature suggestions. All contributions stay MIT.

# Changelog

- v1.0: full version created and uploaded on Nov. 30, 2025.
- v2.0: Integrated OISS and explainer added on Dec. 02, 2025.
- v3.0: Local Web-UI added.
- v4.0: Cloud Edition (Hugging Face) with Firebase integration added.
