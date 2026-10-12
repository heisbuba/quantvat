#!/usr/bin/env python3
from datetime import timedelta
from src import create_app

app = create_app()

# 30 Days Session Persistence ---
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
# Security headers for PWA reliability
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("Quantitative Crypto Volume Analysis Tool - v4.8.0")
    print(f"{'='*60}")
    
    # Debug=False is correct for production PWA deployment
    app.run(host="0.0.0.0", port=7860, debug=False)