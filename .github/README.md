# Crypto Volume Analysis Toolkit (CryptoVAT)

**A powerful web-based suite designed for crypto analysts and traders.** It tracks high-volume tokens across the last 24 hours, performing cross-market analysis using integrated Spot and Futures data. Generate professional, data-driven PDF reports directly in your browser with zero setup required.


# **Key Features**

- Experience a high-end, simple, and modern analysis app that works across all your devices.
- No complex local setup or deep technical knowledge required—just log in and start analyzing.
- We use a multi-source verification engine that cross-references 4 major data sources to filter out "fake" volume and data errors.
- We fuse Spot market trends with Futures data for that essential institutional "edge".
- Our multi-threaded engine does the heavy lifting, delivering deep analysis in under 60 seconds.
- Secure Firebase integration means your API keys and custom settings are saved and ready every time you log in.
- Generate and download clean, professional data-driven PDF reports directly in your browser with one click.
- We included built-in logic for the Open Interest Signal Score (OISS) and Funding Rate analysis.
- The system automatically wipes temporary analysis files after every session to keep your workspace private.
- Developed VTMR (Volume-to-MarketCap-Ratio) metric specifically for you to spot opportunities easily.

# **Setup Guide**

- Launch the [Live App Here](https://huggingface.co/spaces/heisbuba/cryptovat).
- Create an account and log in.
- Obtain and enter your API keys in the **Setup Wizard**.

- Visit [CoinAlyze.net](https://coinalyze.net) and sign up.

- Navigate to **Custom Metrics** and click on **Create Custom Metrics**.

- Enter **VTMR** in the Name and Short Name fields, paste the **VTMR code** below in the Expression field, then **Save & Close**.

```code
((vol_1d[0] / mc_1d[0]) * 10) / 10 * (vol_1d[0] / mc_1d[0] >= 0.5)
```

- Go to **Columns**, deselect all, and select **Market Capitalization**, **Volume 24H**, **Open Interest Change % 24H**, **Predicted Funding Rate Average, OI Weighted**, and **VTMR**, then click **Apply**.

- Sort the data by **VTMR**, copy the URL and paste it in the VTMR box in App's Setup Wizard and proceed to dashboard.

- Tap on **Spot Scan** to generate spot market data.

- Click on **Get Futures** > **Open CoinAlyze** > Go to Chrome Menu (⋮) >  → Share → Print, and save it as PDF. **Note:** Do not change the file name; but if you must then ensure it is saved as **Futures.pdf**.

- Use the upload button in **Get Futures** to upload the file and complete your cross-market analysis.

# ⚖️ **Disclaimer**

CryptoVAT is for research and educational purposes only. It does not provide financial advice, trading signals, or investment recommendations. All data analysis should be verified independently.

# **Contribute**

This project is MIT Licensed — you are free to use, modify, and build upon it.

 - **Issues**: Report bugs or suggest data metrics.

 - **Pull Requests**: Open a PR to add new analysis logic or UI improvements.

 - **Feedback**: All suggestions are welcome to help make this the best free toolkit for traders.

# **Changelog**

- **v4.1**: Improved Spot Volume Tracker Data Accuracy with mandatoy CoinGecko Demo API configuration coming up - *Jan 11, 2026*
- **v4.0**: Cloud Edition (Hugging Face) with Firebase integration added and major logic and UI overhaul - *Dec 25, 2025*.
- **v3.0**: Local Web-UI added.
- **v2.0**: Integrated OISS and explainer added - *Dec. 02, 2025*.
- **v1.0**: full version created and uploaded - *Nov. 30, 2025*.


