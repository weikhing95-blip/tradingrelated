"""
Distill an AJ research memo into a structured, auditable BeliefModel.

    export ANTHROPIC_API_KEY=sk-ant-...
    python extract_belief.py --file article.txt --out belief_model.json

The output JSON is the durable artifact the trading agent runs against, so you
don't re-read the PDF (or pay for the extraction) on every decision. It is also
plain text you can open and sanity-check against the memo.

To get `article.txt` from the PDF:
    python -c "from pypdf import PdfReader; \
        print('\\n'.join((p.extract_text() or '') for p in \
        PdfReader('When_To_Trim_NASDAQ_In_2026.pdf').pages))" > article.txt
"""

from __future__ import annotations

import argparse
import sys

import anthropic

from schemas import BeliefModel

MODEL = "claude-opus-4-8"

SYSTEM = """You distill a single investment-research memo into a structured \
decision framework — a 'belief model' — that another program can act on.

You are capturing the AUTHOR'S framework, not your own market view. Rules:
- Every trigger and condition must come from the memo. Do not invent thresholds \
the author did not state; use 'qualitative' when there is no numeric line.
- Quote a short verbatim phrase as the source for each trigger/condition so the \
distillation is auditable against the text.
- `base_case_target_exposure_pct`: translate the author's prose into a number. \
A 'trim that keeps a core' is a partial reduction (~60-75% of a full position), \
not a full exit (0) and not untouched (100).
- Preserve the author's nuance: if he says 'reduce only on identifiable triggers \
rather than the date alone', that belongs in seasonality_view and failure_modes."""


def extract(text: str) -> BeliefModel:
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    "Distill this memo into a BeliefModel.\n\n<memo>\n"
                    + text.strip()
                    + "\n</memo>"
                ),
            }
        ],
        output_format=BeliefModel,
    )
    belief = response.parsed_output
    if belief is None:
        raise RuntimeError(
            f"Model returned no parseable BeliefModel (stop_reason={response.stop_reason})."
        )
    return belief


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", "-f", help="Article text file. Omit to read stdin.")
    parser.add_argument("--out", "-o", help="Write the BeliefModel JSON here.")
    args = parser.parse_args()

    text = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
    if not text.strip():
        sys.exit("No input text provided.")

    belief = extract(text)
    payload = belief.model_dump_json(indent=2)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        print(f"Wrote belief model to {args.out}", file=sys.stderr)
    else:
        print(payload)


if __name__ == "__main__":
    main()
