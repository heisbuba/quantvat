# **Crypto Volume Analysis Toolkit**

Lightweight and simple toolkit that helps you track high-volume crypto tokens in the last 24 hours and run cross-market analysis using spot + futures data and generate reports in a pdf file. This toolkit works on all devices.

# **Demo**

# **Features**

- Spot Volume Tracker v2.0
- Multi-API verification (CG, CMC, LCW, CR)
- Advanced Futures + Spot Analysis v1.0
- Automatic clean HTML report generator
- Automatic PDF export
- Cleanup system that removes source files after analysis
- Lightweight and fast

# **Installation & Setup**

Before running the toolkit, you need to install these four libraries in your python or Pydroid3 environment:


`pip install requests pandas beautifulsoup4 pypdf`

## API keys Setup

Set these in your environment:

- HTML2PDF_API_KEY

- CMC_API_KEY

- LIVECOINWATCH_API_KEY

- COINRANKINGS_API_KEY

## CoinAlyze VRMR Setup

To use the tool efficiently, you need specific futures data from CoinAlyze:

- Go to CoinAlyze.net and sign up.

- Navigate to **Custom Metrics** and tap on **Create Custom Metrics**.

- Enter **VTMR** in the Name and Short Name fields, then paste the **VTMR code** in the Expression field 'n save & close.

`((vol_1d[0] / mc_1d[0]) * 10) / 10 * (vol_1d[0] / mc_1d[0] >= 0.5)`

- Go to **Columns**, deselect all, and select **Market Capitalization**, **Volume 24H**, and **VTMR**, then click **Apply**.

- Bookmark the page and save it as VTMR.

- Go to Chrome menu → Share → Print, and save it as it is without changing the file name in the Download folder.

### Why do This?

Because before you run the toolkit, you need fresh futures data from CoinAlyze. So basically, you only need to launch Chrome, type VTMR and opn it, print the page into the Download folder. 

After you run the toolkit, it will take care of cleaning your download folder and leaving only the most valuable reports there. 

# Strategy Behind the Toolkit


# Contribute

This project is released under the MIT License, which means anyone is free to use it, modify it, and build on it.
If you’d like to contribute—whether it’s improving the code, fixing a bug, adding new analysis features, or suggesting better data sources—you’re welcome to take part.

You can open an issue, submit a pull request, or share ideas for future updates.

Contributions of any size are appreciated, and all submitted work will remain under the same MIT License to keep the project open and accessible for everyone.

# Disclaimer

This toolkit is for research and educational purposes only. It does not, in any way, provide financial advice, trading signals, or investment recommendations. Crypto markets are volatile—always do your own research and make your own decisions.




