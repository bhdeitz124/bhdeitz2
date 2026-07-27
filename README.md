# bhdeitz2
Prediction Market

## Weekly sports consensus positions

Run:

```bash
python /home/runner/work/bhdeitz2/bhdeitz2/polymarket_weekly_consensus.py --pretty
```

The script:
- pulls the top weekly sports traders (default: top 20),
- fetches each trader's open positions,
- returns positions held by at least 3 traders,
- keeps only holders whose average paid price is within $0.10 of that position's group average price.
