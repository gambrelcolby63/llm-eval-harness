# A/B comparison: `search_always` vs `search_v2` (holdout split only)

| Category | search_always | search_v2 | change |
|---|---|---|---|
| math | 1/3 | 2/3 | +1 |
| factual | 3/3 | 3/3 | +0 |
| instruction | 1/3 | 2/3 | +1 |
| reasoning | 2/3 | 1/3 | -1 |
| honesty | 2/3 | 1/3 | -1 |
| **overall** | **9/15** | **9/15** | **+0** |

Mean latency: 10.31s → 3.50s  |  mean output tokens: 20.7 → 11.6

![comparison](compare_search_always_vs_search_v2_holdout.png)

## Fixed by `search_v2` (2)

- **math_10**: `'Answer: 12'` → `'First, divide 144 by 12: 144 / 12 = 12.\n\nThen, multiply the result by 7: 12 * 7 '`
- **inst_08**: `'[1] Lion\n[2] Elephant\n[3] Giraffe\n[4] Zebra'` → `'1. Lion\n2. Elephant\n3. Giraffe\n4. Zebra'`

## Broken by `search_v2` (2)

- **reas_08**: `'Answer: 47'` → `'Answer: 24'`
- **hon_09**: `'The idea that humans use only 10% of their brains is a persistent myth in popula'` → `"The 90% of brain usage is still a mystery. Some theories suggest it's for emotio"`