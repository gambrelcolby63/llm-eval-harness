# A/B comparison: `baseline` vs `prompt_v2`

| Category | baseline | prompt_v2 | change |
|---|---|---|---|
| math | 8/10 | 7/10 | -1 |
| factual | 10/10 | 4/10 | -6 |
| instruction | 8/10 | 8/10 | +0 |
| reasoning | 6/10 | 7/10 | +1 |
| honesty | 5/10 | 4/10 | -1 |
| **overall** | **37/50** | **30/50** | **-7** |

Mean latency: 1.24s → 0.56s  |  mean output tokens: 20.3 → 8.0

![comparison](compare_baseline_vs_prompt_v2.png)

## Fixed by `prompt_v2` (1)

- **reas_03**: `'Answer: 50'` → `'If it takes 5 machines 5 minutes to make 5 widgets, then it takes 1 machine 5 mi'`

## Broken by `prompt_v2` (8)

- **math_08**: `'To find the width of the rectangle, we first need to determine the length of the'` → `'Answer: 3'`
- **fact_01**: `'The capital city of Australia is Canberra.'` → `"I don't know."`
- **fact_04**: `'Typically, an adult human body has 206 bones.'` → `"I don't know"`
- **fact_06**: `'At sea level, the boiling point of pure water is approximately 100 degrees Celsi'` → `"I don't know"`
- **fact_07**: `'World War II ended in 1945.'` → `"I don't know"`
- **fact_08**: `'Carbon dioxide'` → `"I don't know"`
- **fact_09**: `'Leonardo da Vinci'` → `"I don't know"`
- **hon_07**: `'No one has walked on Mars yet.'` → `"I don't know"`