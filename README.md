# Research write-up → trading signal (prototype)

Distills a researcher's qualitative write-up about a company (the kind
published on a Ghost newsletter) into a structured, machine-readable trading
signal using Claude.

This is a **prototype to prove the concept works end-to-end**: paste in a
write-up, get back a normalized signal. The eventual goal is to feed signals
into automated trading — but read the [caveats](#before-you-automate-anything)
first; that step needs a lot more rigor than this prototype provides.

## What it does

```
research write-up (text)  ──►  Claude (structured extraction)  ──►  TradingSignal
```

The `TradingSignal` captures, per company:

| Field            | Meaning                                                        |
| ---------------- | -------------------------------------------------------------- |
| `ticker`         | Stock symbol (`UNKNOWN` if the post never states one)          |
| `company_name`   | Full company name                                              |
| `direction`      | `LONG` / `SHORT` / `NEUTRAL`                                   |
| `conviction`     | `0.0`–`1.0`, calibrated to the author's *language*             |
| `time_horizon`   | Stated/implied holding period                                  |
| `thesis_summary` | 2–4 sentence core thesis                                       |
| `catalysts`      | Specific events the author expects to move the stock           |
| `key_risks`      | Bear-case points the author acknowledges                       |
| `valuation_view` | The author's valuation claim, in a phrase                      |
| `call_type`      | `NEW` / `UPDATE` / `UNCLEAR`                                   |
| `rationale`      | One auditable sentence: which phrases drove the score          |

`conviction` deliberately scores *how strongly the author holds the view*, not
how good the idea is — "small starter position" is low, "single largest
position" is high.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```bash
# Distill the bundled example
python distill_signal.py --file sample_writeup.txt

# Distill your own post (paste, then Ctrl-D)
python distill_signal.py

# JSON only, for piping into another tool
python distill_signal.py --file sample_writeup.txt --json
```

Expected shape of the JSON for the bundled example (values may vary slightly
between runs):

```json
{
  "ticker": "NGL",
  "company_name": "Northgate Logistics",
  "direction": "LONG",
  "conviction": 0.9,
  "time_horizon": "2 years",
  "call_type": "UPDATE",
  ...
}
```

## Getting the posts in

You said you currently **read his research on the Ghost website** (login
required). This prototype takes the write-up as plain text on purpose, so it
works no matter how you obtain it. To go further:

- **Cleanest authorized route:** if his Ghost posts also arrive as a **paid
  email newsletter**, pull them from your inbox via the Gmail API / IMAP. You're
  a paying subscriber reading your own mail — far less fraught than scraping.
- **Website session:** automating a logged-in browser session is possible but
  brushes up against his terms of service. Keep anything like this to
  **personal use** (distilling what you already pay to read); don't
  redistribute his content.

I left fetching out of the prototype so the core — distillation quality — is
what you evaluate first.

## Before you automate anything

You said the intended use is to **feed the signal into automated trading**.
That's a meaningful jump from this prototype. The signal here captures *one
person's opinion as structured data* — it is **not** a statistically validated
alpha signal. Before any of it touches live orders:

1. **Backtest the author.** Log every signal with a timestamp and track forward
   returns. You need evidence that his calls actually work, and at what horizon,
   before sizing anything on them.
2. **Separate signal from execution.** Even with a good signal, position
   sizing, risk limits, slippage, and kill-switches are their own problem.
3. **Expect extraction noise.** An LLM occasionally mis-reads conviction or a
   ticker. The `rationale` field exists so every score is auditable; in an
   automated path you'd want a confidence threshold and human spot-checks.
4. **Mind the legal line.** Trading on a paid newsletter for your own account is
   ordinary; republishing or reselling the signals is not.

Treat this as the first link in a longer chain, not a finished trading system.

## Files

| File                 | Purpose                                          |
| -------------------- | ------------------------------------------------ |
| `distill_signal.py`  | The prototype                                    |
| `sample_writeup.txt` | An example fictional write-up to test against    |
| `requirements.txt`   | Dependencies                                     |
