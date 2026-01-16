#!/usr/bin/env python3
from src import create_app

app = create_app()

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("QUANTITATIVE CRYPTO VOLUME ANALYSIS TOOLKIT - v4.1.5")
    print(f"{'='*60}")
    
    app.run(host="0.0.0.0", port=7860, debug=False)