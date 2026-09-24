# QuantVAT

**QuantVAT** is an ecosystem built for data-driven traders. It tracks volumetric activity, performing cross-market analysis by fusing Spot and Futures data. In addition, it features the Deep Diver Engine for spot analysis and a semi-automated Trading Journal with blunt AI auditing.

<p align="center">
  <a href="https://heisbuba-quantvat.hf.space" target="_blank"><img src="https://img.shields.io/badge/Live_Demo-HERE-10b981?style=for-the-badge&logo=huggingface" alt="Live Demo"/></a><a href="https://www.x.com/quantvat" target="_blank"><img src="https://img.shields.io/badge/quantvat-000000?style=for-the-badge&logo=x" alt="X"/></a><a href="https://www.youtube.com/@quantvat" target="_blank"><img src="https://img.shields.io/badge/@quantvat-FF0000?style=for-the-badge&logo=youtube" alt="YouTube"/></a><a href="https://www.quantvat.name.ng" target="_blank"><img src="https://img.shields.io/badge/quantvat.name.ng-ffffff?style=for-the-badge&logo=blogger" alt="Blog"/></a>
</p>

# Features

| Feature | Description |
|---------|-------------|
| **Cross-Market Fusion** | Combines Spot market trends with Futures data to get overall picture |
| **VTMR Logic Engine** | Custom Volume-to-MarketCap Ratio metric to identify divergence between trading activity and valuation |
| **OISS & Funding Analysis** | Open Interest Signal Score + funding rate analytics |
| **Deep Diver Engine** | Dedicated spot token browser with quantitative volumetric analysis (VTPC, VTMR, velocity metrics) |
| **AI-Powered Trading Journal** | Semi-automated journal with blunt AI auditing to detect patterns in execution and psychology |
| **Automated Reporting** | Generate professional, data-driven reports directly in your browser |
| **PWA Support** | Install as a native app for instant access (iOS/Android/Desktop) |

# Setup Guide

1. **Launch** the [Live App](https://heisbuba-quantvat.hf.space)
2. **Create an account** and log in
3. Enter your **Coingecko API Demo key** in the **Setup Wizard**
4. **Configure VTMR** via CoinAlyze:

### Step 1: VMTR Configuration
1. Visit [CoinAlyze.net](https://coinalyze.net) and sign up
2. Navigate to **Custom Metrics** → **Create Custom Metrics**
3. Enter **VTMR** in Name and Short Name fields
4. Paste this formula in the Expression field:
   ```code
   (vol_1d[0] / mc_1d[0]) * (vol_1d[0] / mc_1d[0] >= 0.5)
   ```
5. **Save & Close**

### Step 2: Customize Columns
1. Go to **Columns** → Deselect all
2. Select only:
   - Market Capitalization
   - Volume 24H
   - Open Interest Change % 24H
   - Predicted Funding Rate Average, OI Weighted
   - VTMR
3. Click **Apply**

### Step 3: Generate Your Feed URL
1. Sort the data by **VTMR** (highest first)
2. Copy the full URL
3. Paste it in the **VTMR box** inside QuantVAT's Setup Wizard
4. Proceed to **Dashboard**

### Step 4: Spot & Futures Analysis
- **Spot Scan** — Tap the button to generate spot market data
- **Get Futures** — Export PDF from CoinAlyze → Upload to QuantVAT → Complete your cross-market analysis


# Disclaimer

QuantVAT is for **research and educational purposes only**. It does **not** provide:
- Financial advice
- Trading signals
- Investment recommendations.

# Contributing

We welcome contributions! This project is **MIT Licensed** — free to use, modify, and build upon.

| Contribution Type | How to Help |
|-------------------|-------------|
| 🐛 **Issues** | Report bugs or suggest new data metrics |
| 🔧 **Pull Requests** | Add new analysis logic or UI improvements |
| 💬 **Feedback** | All suggestions welcome |

# Changelog

| Version | Date | Changes |
|---------|------|---------|
| **v4.7** | Sep 24, 2026 | Made many upgrades and optimizations, and removed PDF generation, keeping only HTML. 
| **v4.6** | Aug 4, 2026 | Enhanced futures PDF parser, cross‑platform data extraction, code restructure |
| **v4.5** | Feb 12, 2026 | Major UI/UX fix, AI Trading Journal, Deep Diver Engine, PWA support |
| **v4.1** | Jan 11, 2026 | Improved Spot Volume Tracker with mandatory CoinGecko Demo API config |
| **v4.0** | Dec 25, 2025 | Cloud Edition (Hugging Face) with Firebase integration, major overhaul |
| **v3.0** | — | Local Web-UI added |
| **v2.0** | Dec 2, 2025 | Integrated OISS and explainer |
| **v1.0** | Nov 30, 2025 | Initial full version |

# License

MIT License — See [LICENSE](https://github.com/heisbuba/quantvat/blob/main/LICENSE) for details.
