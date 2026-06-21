# STORM template: investing

Same 4-prompt flow, reweighted for an investment decision. The economist and skeptic carry more
weight here, and grounded mode is strongly recommended because numbers drive the decision.

## Perspective mapping for investing

- PRACTITIONER: an operator or customer in the business. What does the product or sector look
  like from inside? What breaks in practice?
- ACADEMIC: the evidence base. Unit economics, sector studies, base rates, what the data says
  about this kind of bet.
- SKEPTIC: the bear case. The strongest reason this loses money. What bulls ignore.
- ECONOMIST: the incentive and money map. Who profits from the current narrative, who is selling,
  insider and promoter activity, where the financial pressure sits. Weight this voice heavily.
- HISTORIAN: the historical parallel. Prior cycles, prior manias or busts of this shape, how they
  resolved.

## Flow

1. Run Prompt 1 with the 5 perspectives above on the specific ticker, asset, or thesis.
2. Run Prompt 2 (contradiction map). The biggest contradiction usually marks where the real risk
   lives. The thing every perspective agrees on is the part of the thesis you can lean on.
3. Grounded step: send the bull case, bear case, valuation numbers, and any insider or promoter
   activity claims to the deep-research skill or web tools, and pin each to a source.
4. Run Prompt 3 with `[YOUR ROLE]` as the investor. The actionable insight should name a position,
   a size, and the conditions that would invalidate the thesis.
5. Run Prompt 4. Treat any number under a 7 of 10 confidence as unverified. Cross-check prices,
   margins, and growth rates against primary filings.

## Save

Write the run to `docs/research/storm/YYYY-MM-DD-<ticker>.md` so the thesis and its invalidation
conditions are on record for the next review.
